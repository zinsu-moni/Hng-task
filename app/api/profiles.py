from collections.abc import Sequence
import csv
from datetime import datetime, timezone
from io import StringIO
import math
import re
from typing import Dict, Literal, Optional, Tuple
from urllib.parse import urlencode
import uuid

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.core.auth import require_admin, require_analyst
from app.core.rate_limit import limiter
from app.db.deps import get_db
from app.models.profile import Profile
from app.models.user import User
from app.utils.uuid7 import uuid7

router = APIRouter(prefix="/api", tags=["profiles"])


class ProfileOut(BaseModel):
    id: str
    name: str
    gender: str
    gender_probability: float
    age: int
    age_group: str
    country_id: str
    country_name: str
    country_probability: float
    created_at: datetime


class PaginationLinks(BaseModel):
    self: str
    next: str | None
    prev: str | None


class ProfilesResponse(BaseModel):
    status: Literal["success"]
    page: int
    limit: int
    total: int
    total_pages: int
    links: PaginationLinks
    data: Sequence[ProfileOut]


class CreateProfileRequest(BaseModel):
    name: str


class ProfileResponse(BaseModel):
    status: Literal["success"]
    data: ProfileOut


class DeleteProfileResponse(BaseModel):
    status: Literal["success"]
    message: str


def _empty_as_error(value: Optional[str], field_name: str) -> None:
    if value is not None and value.strip() == "":
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Parameter '{field_name}' cannot be empty",
        )


def _parse_int(value: Optional[str], field_name: str) -> Optional[int]:
    if value is None:
        return None
    if value.strip() == "":
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Parameter '{field_name}' cannot be empty",
        )
    try:
        return int(value)
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Parameter '{field_name}' must be an integer",
        ) from e


def _parse_float(value: Optional[str], field_name: str) -> Optional[float]:
    if value is None:
        return None
    if value.strip() == "":
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Parameter '{field_name}' cannot be empty",
        )
    try:
        return float(value)
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Parameter '{field_name}' must be a number",
        ) from e


def _parse_nl_query(q: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[int], Optional[int]]:
    """
    Parse a natural-language query string into (gender, age_group, country_id, min_age, max_age).

    Rules (order matters for specific phrases like 'teenagers above 17'):
    - 'young' -> min_age=16, max_age=24
    - 'male'/'males'/'female'/'females' -> gender
    - 'above/over X' -> min_age=X
    - 'below/under X' -> max_age=X
    - 'from <country name>' -> country_id (using COUNTRY_NAME_TO_ISO)
    - age groups: child, teenager, adult, senior -> age_group
    - 'teenagers above 17' -> age_group=teenager + min_age=17
    """

    text = q.strip().lower()
    if not text:
        raise ValueError("empty query")

    gender: Optional[str] = None
    age_group: Optional[str] = None
    country_id: Optional[str] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None

    m = re.search(r"\bteenagers?\s+(above|over)\s+(\d{1,3})\b", text)
    if m:
        age_group = "teenager"
        min_age = int(m.group(2))

    if "young" in text:
        if min_age is None or min_age < 16:
            min_age = 16
        if max_age is None or max_age > 24:
            max_age = 24

    if any(w in text.split() for w in ["male", "males"]):
        gender = "male"
    if any(w in text.split() for w in ["female", "females"]):
        gender = "female"

    if "child" in text or "children" in text:
        age_group = "child"
    if "teenager" in text or "teenagers" in text:
        age_group = "teenager"
    if "adult" in text or "adults" in text:
        age_group = "adult"
    if "senior" in text or "seniors" in text:
        age_group = "senior"

    for m in re.finditer(r"\b(above|over)\s+(\d{1,3})\b", text):
        val = int(m.group(2))
        if min_age is None or val > min_age:
            min_age = val

    for m in re.finditer(r"\b(below|under)\s+(\d{1,3})\b", text):
        val = int(m.group(2))
        if max_age is None or val < max_age:
            max_age = val

    m = re.search(r"\bfrom\s+([a-z\s]+)", text)
    if m:
        country_name = m.group(1).strip()
        country_name = re.sub(r"\b(only|people|residents|citizens)\b.*$", "", country_name).strip()
        iso = COUNTRY_NAME_TO_ISO.get(country_name)
        if iso:
            country_id = iso

    if not any([gender, age_group, country_id, min_age, max_age]):
        raise ValueError("no recognizable filters")

    return gender, age_group, country_id, min_age, max_age


COUNTRY_NAME_TO_ISO: Dict[str, str] = {
    "united states": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "nigeria": "NG",
    "ghana": "GH",
    "kenya": "KE",
    "india": "IN",
    "canada": "CA",
    "germany": "DE",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
}

COUNTRY_ID_TO_NAME: Dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "NG": "Nigeria",
    "GH": "Ghana",
    "KE": "Kenya",
    "IN": "India",
    "CA": "Canada",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
}


def _profile_to_dict(profile: Profile) -> dict:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "gender": profile.gender,
        "gender_probability": profile.gender_probability,
        "age": profile.age,
        "age_group": profile.age_group,
        "country_id": profile.country_id,
        "country_name": profile.country_name,
        "country_probability": profile.country_probability,
        "created_at": profile.created_at,
    }


def _age_group(age: int) -> str:
    if age <= 12:
        return "child"
    if age <= 19:
        return "teenager"
    if age <= 64:
        return "adult"
    return "senior"


def _validate_filter_params(
    *,
    gender: Optional[str],
    age_group: Optional[str],
    country_id: Optional[str],
    min_age: Optional[str],
    max_age: Optional[str],
    min_gender_probability: Optional[str],
    min_country_probability: Optional[str],
    sort_by: str,
    order: str,
) -> dict:
    _empty_as_error(gender, "gender")
    _empty_as_error(age_group, "age_group")
    _empty_as_error(country_id, "country_id")
    _empty_as_error(sort_by, "sort_by")
    _empty_as_error(order, "order")
    _empty_as_error(min_age, "min_age")
    _empty_as_error(max_age, "max_age")
    _empty_as_error(min_gender_probability, "min_gender_probability")
    _empty_as_error(min_country_probability, "min_country_probability")

    if gender is not None:
        gender = gender.strip().lower()
        if gender not in {"male", "female"}:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="gender must be 'male' or 'female'")

    if age_group is not None:
        age_group = age_group.strip().lower()
        if age_group not in {"child", "teenager", "adult", "senior"}:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="age_group must be one of: child, teenager, adult, senior",
            )

    if country_id is not None:
        country_id = country_id.strip().upper()
        if len(country_id) != 2:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="country_id must be a 2-letter ISO code")

    if sort_by not in {"age", "created_at", "gender_probability"}:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="sort_by must be one of: age, created_at, gender_probability",
        )

    if order not in {"asc", "desc"}:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="order must be one of: asc, desc")

    min_age_i = _parse_int(min_age, "min_age")
    max_age_i = _parse_int(max_age, "max_age")
    min_gender_probability_f = _parse_float(min_gender_probability, "min_gender_probability")
    min_country_probability_f = _parse_float(min_country_probability, "min_country_probability")

    if min_age_i is not None and max_age_i is not None and min_age_i > max_age_i:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="min_age cannot be greater than max_age")

    return {
        "gender": gender,
        "age_group": age_group,
        "country_id": country_id,
        "min_age_i": min_age_i,
        "max_age_i": max_age_i,
        "min_gender_probability_f": min_gender_probability_f,
        "min_country_probability_f": min_country_probability_f,
        "sort_by": sort_by,
        "order": order,
    }


def _parse_pagination(page: Optional[str], limit: Optional[str]) -> tuple[int, int]:
    _empty_as_error(page, "page")
    _empty_as_error(limit, "limit")
    page_parsed = _parse_int(page, "page")
    limit_parsed = _parse_int(limit, "limit")
    page_i = 1 if page_parsed is None else page_parsed
    limit_i = 10 if limit_parsed is None else limit_parsed
    if page_i < 1:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="page must be >= 1")
    if limit_i < 1 or limit_i > 50:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 50")
    return page_i, limit_i


def _where_clause(
    *,
    gender: Optional[str],
    age_group: Optional[str],
    country_id: Optional[str],
    min_age_i: Optional[int],
    max_age_i: Optional[int],
    min_gender_probability_f: Optional[float],
    min_country_probability_f: Optional[float],
):
    conditions = []
    if gender is not None:
        conditions.append(Profile.gender == gender)
    if age_group is not None:
        conditions.append(Profile.age_group == age_group)
    if country_id is not None:
        conditions.append(Profile.country_id == country_id)
    if min_age_i is not None:
        conditions.append(Profile.age >= min_age_i)
    if max_age_i is not None:
        conditions.append(Profile.age <= max_age_i)
    if min_gender_probability_f is not None:
        conditions.append(Profile.gender_probability >= min_gender_probability_f)
    if min_country_probability_f is not None:
        conditions.append(Profile.country_probability >= min_country_probability_f)
    return sa.and_(*conditions) if conditions else sa.true()


def _order_expr(sort_by: str, order: str):
    sort_col = {
        "age": Profile.age,
        "created_at": Profile.created_at,
        "gender_probability": Profile.gender_probability,
    }[sort_by]
    return sort_col.asc() if order == "asc" else sort_col.desc()


def _link(request: Request, page: int, limit: int) -> str:
    params = dict(request.query_params)
    params["page"] = str(page)
    params["limit"] = str(limit)
    return f"{request.url.path}?{urlencode(params)}"


def _run_profiles_query(
    db: Session,
    request: Request,
    *,
    gender: Optional[str],
    age_group: Optional[str],
    country_id: Optional[str],
    min_age_i: Optional[int],
    max_age_i: Optional[int],
    min_gender_probability_f: Optional[float],
    min_country_probability_f: Optional[float],
    sort_by: str,
    order: str,
    page_i: int,
    limit_i: int,
) -> Dict:
    where_clause = _where_clause(
        gender=gender,
        age_group=age_group,
        country_id=country_id,
        min_age_i=min_age_i,
        max_age_i=max_age_i,
        min_gender_probability_f=min_gender_probability_f,
        min_country_probability_f=min_country_probability_f,
    )
    total: int = db.execute(sa.select(sa.func.count(Profile.id)).select_from(Profile).where(where_clause)).scalar_one()
    total_pages = math.ceil(total / limit_i) if total else 0
    offset = (page_i - 1) * limit_i

    rows = db.execute(
        sa.select(Profile)
        .where(where_clause)
        .order_by(_order_expr(sort_by, order))
        .limit(limit_i)
        .offset(offset)
    ).scalars().all()

    return {
        "status": "success",
        "page": page_i,
        "limit": limit_i,
        "total": total,
        "total_pages": total_pages,
        "links": {
            "self": _link(request, page_i, limit_i),
            "next": _link(request, page_i + 1, limit_i) if page_i < total_pages else None,
            "prev": _link(request, page_i - 1, limit_i) if page_i > 1 else None,
        },
        "data": [_profile_to_dict(r) for r in rows],
    }


def _query_all_profiles(db: Session, *, filters: dict) -> Sequence[Profile]:
    where_clause = _where_clause(
        gender=filters["gender"],
        age_group=filters["age_group"],
        country_id=filters["country_id"],
        min_age_i=filters["min_age_i"],
        max_age_i=filters["max_age_i"],
        min_gender_probability_f=filters["min_gender_probability_f"],
        min_country_probability_f=filters["min_country_probability_f"],
    )
    return db.execute(
        sa.select(Profile)
        .where(where_clause)
        .order_by(_order_expr(filters["sort_by"], filters["order"]))
    ).scalars().all()


async def _build_profile_from_external_apis(name: str) -> Profile:
    async with httpx.AsyncClient(timeout=10.0) as client:
        gender_response, age_response, country_response = await client.get(
            "https://api.genderize.io",
            params={"name": name},
        ), await client.get(
            "https://api.agify.io",
            params={"name": name},
        ), await client.get(
            "https://api.nationalize.io",
            params={"name": name},
        )

    gender_response.raise_for_status()
    age_response.raise_for_status()
    country_response.raise_for_status()

    gender_data = gender_response.json()
    age_data = age_response.json()
    country_data = country_response.json()

    gender = gender_data.get("gender")
    age = age_data.get("age")
    countries = country_data.get("country") or []
    country = countries[0] if countries else {}
    country_id = country.get("country_id")

    if gender not in {"male", "female"} or age is None or not country_id:
        raise HTTPException(status_code=HTTP_422_UNPROCESSABLE_ENTITY, detail="Unable to build profile")

    country_id = str(country_id).upper()
    return Profile(
        id=uuid7(),
        name=name,
        gender=gender,
        gender_probability=float(gender_data.get("probability") or 0),
        age=int(age),
        age_group=_age_group(int(age)),
        country_id=country_id,
        country_name=COUNTRY_ID_TO_NAME.get(country_id, country_id),
        country_probability=float(country.get("probability") or 0),
    )


@router.get("/profiles", response_model=ProfilesResponse)
@limiter.limit("60/minute")
def get_profiles(
    request: Request,
    gender: Optional[str] = Query(default=None),
    age_group: Optional[str] = Query(default=None),
    country_id: Optional[str] = Query(default=None),
    min_age: Optional[str] = Query(default=None),
    max_age: Optional[str] = Query(default=None),
    min_gender_probability: Optional[str] = Query(default=None),
    min_country_probability: Optional[str] = Query(default=None),
    sort_by: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    page: Optional[str] = Query(default=None),
    limit: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    del current_user
    filters = _validate_filter_params(
        gender=gender,
        age_group=age_group,
        country_id=country_id,
        min_age=min_age,
        max_age=max_age,
        min_gender_probability=min_gender_probability,
        min_country_probability=min_country_probability,
        sort_by=sort_by,
        order=order,
    )
    page_i, limit_i = _parse_pagination(page, limit)
    try:
        return _run_profiles_query(db, request, **filters, page_i=page_i, limit_i=limit_i)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error") from e


@router.get("/profiles/search", response_model=ProfilesResponse)
@limiter.limit("60/minute")
def search_profiles(
    request: Request,
    q: str = Query(..., description="Natural language search query"),
    page: Optional[str] = Query(default=None),
    limit: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    del current_user
    _empty_as_error(q, "q")
    try:
        gender, age_group, country_id, min_age, max_age = _parse_nl_query(q)
    except ValueError:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Unable to interpret query")

    if min_age is not None and max_age is not None and min_age > max_age:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="min_age cannot be greater than max_age")

    page_i, limit_i = _parse_pagination(page, limit)
    try:
        return _run_profiles_query(
            db,
            request,
            gender=gender,
            age_group=age_group,
            country_id=country_id,
            min_age_i=min_age,
            max_age_i=max_age,
            min_gender_probability_f=None,
            min_country_probability_f=None,
            sort_by="created_at",
            order="desc",
            page_i=page_i,
            limit_i=limit_i,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error") from e


@router.post("/profiles", response_model=ProfileResponse)
@limiter.limit("60/minute")
async def create_profile(
    request: Request,
    payload: CreateProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    del request, current_user
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="name cannot be empty")

    existing = db.execute(sa.select(Profile.id).where(Profile.name == name)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Profile already exists")

    try:
        profile = await _build_profile_from_external_apis(name)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Profile already exists") from e
    except httpx.HTTPError as e:
        db.rollback()
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="External profile lookup failed") from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error") from e

    return {"status": "success", "data": _profile_to_dict(profile)}


@router.get("/profiles/export")
@limiter.limit("60/minute")
def export_profiles(
    request: Request,
    format: str = Query(default="csv"),
    gender: Optional[str] = Query(default=None),
    age_group: Optional[str] = Query(default=None),
    country_id: Optional[str] = Query(default=None),
    min_age: Optional[str] = Query(default=None),
    max_age: Optional[str] = Query(default=None),
    min_gender_probability: Optional[str] = Query(default=None),
    min_country_probability: Optional[str] = Query(default=None),
    sort_by: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    del request, current_user
    if format != "csv":
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="format must be csv")

    filters = _validate_filter_params(
        gender=gender,
        age_group=age_group,
        country_id=country_id,
        min_age=min_age,
        max_age=max_age,
        min_gender_probability=min_gender_probability,
        min_country_probability=min_country_probability,
        sort_by=sort_by,
        order=order,
    )
    rows = _query_all_profiles(db, filters=filters)

    output = StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow(
        [
            "id",
            "name",
            "gender",
            "gender_probability",
            "age",
            "age_group",
            "country_id",
            "country_name",
            "country_probability",
            "created_at",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                str(row.id),
                row.name,
                row.gender,
                row.gender_probability,
                row.age,
                row.age_group,
                row.country_id,
                row.country_name,
                row.country_probability,
                row.created_at,
            ]
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="profiles_{timestamp}.csv"'},
    )


@router.delete("/profiles/{profile_id}", response_model=DeleteProfileResponse)
@limiter.limit("60/minute")
def delete_profile(
    request: Request,
    profile_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    del request, current_user
    try:
        parsed_id = uuid.UUID(profile_id)
    except ValueError as e:
        raise HTTPException(status_code=HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid profile id") from e

    profile = db.get(Profile, parsed_id)
    if profile is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Profile not found")

    db.delete(profile)
    db.commit()
    return {"status": "success", "message": "Profile deleted"}

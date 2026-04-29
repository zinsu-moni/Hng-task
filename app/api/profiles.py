from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_422_UNPROCESSABLE_ENTITY, HTTP_500_INTERNAL_SERVER_ERROR

from app.db.deps import get_db
from app.models.profile import Profile

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


class ProfilesResponse(BaseModel):
    status: Literal["success"]
    page: int
    limit: int
    total: int
    data: Sequence[ProfileOut]


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


@router.get("/profiles", response_model=ProfilesResponse)
def get_profiles(
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
):
    # Missing/empty string params -> 400. Invalid numeric conversion -> 422.
    _empty_as_error(gender, "gender")
    _empty_as_error(age_group, "age_group")
    _empty_as_error(country_id, "country_id")
    _empty_as_error(sort_by, "sort_by")
    _empty_as_error(order, "order")
    _empty_as_error(page, "page")
    _empty_as_error(limit, "limit")
    _empty_as_error(min_age, "min_age")
    _empty_as_error(max_age, "max_age")
    _empty_as_error(min_gender_probability, "min_gender_probability")
    _empty_as_error(min_country_probability, "min_country_probability")

    # Normalize + validate allowed enums.
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
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST, detail="country_id must be a 2-letter ISO code"
            )

    if sort_by not in {"age", "created_at", "gender_probability"}:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="sort_by must be one of: age, created_at, gender_probability",
        )

    if order not in {"asc", "desc"}:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="order must be one of: asc, desc",
        )

    min_age_i = _parse_int(min_age, "min_age")
    max_age_i = _parse_int(max_age, "max_age")
    min_gender_probability_f = _parse_float(min_gender_probability, "min_gender_probability")
    min_country_probability_f = _parse_float(min_country_probability, "min_country_probability")

    if min_age_i is not None and max_age_i is not None and min_age_i > max_age_i:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="min_age cannot be greater than max_age")

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

    try:
        where_clause = sa.and_(*conditions) if conditions else sa.true()

        sort_col = {
            "age": Profile.age,
            "created_at": Profile.created_at,
            "gender_probability": Profile.gender_probability,
        }[sort_by]

        order_expr = sort_col.asc() if order == "asc" else sort_col.desc()
        page_parsed = _parse_int(page, "page")
        limit_parsed = _parse_int(limit, "limit")
        page_i = 1 if page_parsed is None else page_parsed
        limit_i = 10 if limit_parsed is None else limit_parsed
        if page_i < 1:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="page must be >= 1")
        if limit_i < 1 or limit_i > 50:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 50")
        offset = (page_i - 1) * limit_i

        # Count by indexed PK to encourage index-only scans for the total.
        count_stmt = sa.select(sa.func.count(Profile.id)).select_from(Profile).where(where_clause)
        total: int = db.execute(count_stmt).scalar_one()

        stmt = (
            sa.select(Profile)
            .where(where_clause)
            .order_by(order_expr)
            .limit(limit_i)
            .offset(offset)
        )
        rows = db.execute(stmt).scalars().all()

        return {
            "status": "success",
            "page": page_i,
            "limit": limit_i,
            "total": total,
            "data": [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "gender": r.gender,
                    "gender_probability": r.gender_probability,
                    "age": r.age,
                    "age_group": r.age_group,
                    "country_id": r.country_id,
                    "country_name": r.country_name,
                    "country_probability": r.country_probability,
                    "created_at": r.created_at,
                }
                for r in rows
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        # Avoid leaking internals but keep a useful log line.
        # (Cursor environment may not have logging configured yet.)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server error",
        ) from e


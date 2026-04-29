from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from app.config import load_environment
from app.db.deps import get_db
from app.models.user import RefreshToken, User

ACCESS_TOKEN_EXPIRE_MINUTES = 3
REFRESH_TOKEN_EXPIRE_MINUTES = 5
ALGORITHM = "HS256"

bearer_scheme = HTTPBearer(auto_error=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_jwt_secret() -> str:
    load_environment()
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY is not set")
    return secret


def create_access_token(user: User) -> str:
    now = utc_now()
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "github_id": user.github_id,
        "role": user.role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)


def create_refresh_token(user: User) -> tuple[str, datetime]:
    now = utc_now()
    expires_at = now + timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "jti": str(uuid.uuid4()),
        "type": "refresh",
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)
    return token, expires_at


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token_pair(db: Session, user: User) -> dict[str, Any]:
    access_token = create_access_token(user)
    refresh_token, refresh_expires_at = create_refresh_token(user)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=refresh_expires_at,
        )
    )
    db.commit()
    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "refresh_expires_in": REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    }


def decode_token(token: str, expected_type: Literal["access", "refresh"]) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    if payload.get("type") != expected_type or not payload.get("sub"):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return payload


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    payload = decode_token(credentials.credentials, "access")
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError as e:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not user.is_active:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="User is inactive")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def require_analyst(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in {"admin", "analyst"}:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Analyst access required")
    return current_user

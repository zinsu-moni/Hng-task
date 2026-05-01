from __future__ import annotations

import os
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.config import github_cli_redirect_uri, load_environment
from app.core.auth import (
    ALGORITHM,
    bearer_scheme,
    decode_token,
    get_current_user,
    get_jwt_secret,
    hash_token,
    issue_token_pair,
    utc_now,
)
from app.core.rate_limit import limiter
from app.db.deps import get_db
from app.models.user import RefreshToken, User
from app.schemas.auth import (
    CliExchangeRequest,
    CliExchangeResponse,
    LogoutRequest,
    LogoutResponse,
    MeUserOut,
    RefreshRequest,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])
load_environment()

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
WEB_CALLBACK_PATH = "/auth/github/callback"
DEFAULT_FRONTEND_REDIRECT_URI = "http://127.0.0.1:5000/auth/callback"
ALLOWED_FRONTEND_REDIRECTS = {
    "http://127.0.0.1:5000/auth/callback",
    "http://localhost:5000/auth/callback",
    "https://instance-web.vercel.app/auth/callback",
}
STATE_EXPIRE_MINUTES = 5


def _env(name: str) -> str:
    load_environment()
    value = os.getenv(name)
    if not value:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{name} is not set")
    return value


def _web_redirect_uri(request: Request | None = None) -> str:
    explicit_redirect_uri = os.getenv("GITHUB_REDIRECT_URI")
    if explicit_redirect_uri:
        return explicit_redirect_uri

    public_base_url = os.getenv("PUBLIC_BASE_URL")
    if public_base_url:
        return f"{public_base_url.rstrip('/')}{WEB_CALLBACK_PATH}"

    if request is not None:
        return str(request.url_for("github_callback"))

    return f"http://localhost:8000{WEB_CALLBACK_PATH}"


def _oauth_config(request: Request | None = None) -> dict[str, str]:
    return {
        "client_id": _env("GITHUB_CLIENT_ID"),
        "client_secret": _env("GITHUB_CLIENT_SECRET"),
        "redirect_uri": _web_redirect_uri(request),
    }


def _cli_oauth_config() -> dict[str, str]:
    return {
        "client_id": _env("GITHUB_CLI_CLIENT_ID"),
        "client_secret": _env("GITHUB_CLI_CLIENT_SECRET"),
        "redirect_uri": github_cli_redirect_uri(),
    }


def _create_state(frontend_redirect_uri: str) -> str:
    now = utc_now()
    return jwt.encode(
        {
            "type": "github_oauth_state",
            "frontend_redirect_uri": frontend_redirect_uri,
            "iat": now,
            "exp": now + timedelta(minutes=STATE_EXPIRE_MINUTES),
        },
        get_jwt_secret(),
        algorithm=ALGORITHM,
    )


def _decode_state(state: str) -> str:
    try:
        payload = jwt.decode(state, get_jwt_secret(), algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Invalid OAuth state") from e
    if payload.get("type") != "github_oauth_state":
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")
    frontend_redirect_uri = payload.get("frontend_redirect_uri")
    if frontend_redirect_uri not in ALLOWED_FRONTEND_REDIRECTS:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")
    return frontend_redirect_uri


async def _exchange_code_for_github_user(
    code: str,
    request: Request | None = None,
) -> dict[str, Any]:
    config = _oauth_config(request)
    return await _exchange_github_code(
        code=code,
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        redirect_uri=config["redirect_uri"],
    )


async def _exchange_cli_code_for_github_user(code: str, code_verifier: str) -> dict[str, Any]:
    config = _cli_oauth_config()
    return await _exchange_github_code(
        code=code,
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        redirect_uri=config["redirect_uri"],
        code_verifier=code_verifier,
    )


async def _exchange_github_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier is not None:
            token_payload["code_verifier"] = code_verifier

        token_response = await client.post(
            GITHUB_ACCESS_TOKEN_URL,
            headers={"Accept": "application/json"},
            data=token_payload,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="GitHub OAuth failed")

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        user_response = await client.get(GITHUB_USER_URL, headers=headers)
        user_response.raise_for_status()
        github_user = user_response.json()

        if not github_user.get("email"):
            emails_response = await client.get(GITHUB_EMAILS_URL, headers=headers)
            if emails_response.status_code == 200:
                for email in emails_response.json():
                    if email.get("primary") and email.get("verified"):
                        github_user["email"] = email.get("email")
                        break
        return github_user


def _upsert_user(db: Session, github_user: dict[str, Any]) -> User:
    github_id = str(github_user.get("id") or "")
    username = github_user.get("login")
    if not github_id or not username:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Invalid GitHub user")

    user = db.execute(sa.select(User).where(User.github_id == github_id)).scalar_one_or_none()
    if user is None:
        user = User(github_id=github_id, username=username)
        db.add(user)

    user.username = username
    user.email = github_user.get("email")
    user.avatar_url = github_user.get("avatar_url")
    user.last_login_at = utc_now()
    db.commit()
    db.refresh(user)
    return user


@router.get("/github")
@limiter.limit("10/minute")
def github_login(
    request: Request,
    redirect_uri: str | None = Query(default=None),
) -> RedirectResponse:
    frontend_redirect_uri = redirect_uri or DEFAULT_FRONTEND_REDIRECT_URI
    if frontend_redirect_uri not in ALLOWED_FRONTEND_REDIRECTS:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Invalid redirect_uri")

    config = _oauth_config(request)
    params = urlencode(
        {
            "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"],
            "scope": "read:user user:email",
            "state": _create_state(frontend_redirect_uri),
        }
    )
    return RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{params}")


@router.get("/github/callback")
@limiter.limit("10/minute")
async def github_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    frontend_redirect_uri = _decode_state(state)
    try:
        github_user = await _exchange_code_for_github_user(code, request)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="GitHub OAuth failed") from e
    user = _upsert_user(db, github_user)
    if not user.is_active:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="User is inactive")
    token_pair = issue_token_pair(db, user)
    query = urlencode(
        {
            "access_token": token_pair["access_token"],
            "refresh_token": token_pair["refresh_token"],
        }
    )
    return RedirectResponse(f"{frontend_redirect_uri}?{query}")


@router.post("/cli/exchange", response_model=CliExchangeResponse)
@limiter.limit("10/minute")
async def cli_exchange(request: Request, payload: CliExchangeRequest, db: Session = Depends(get_db)):
    del request
    try:
        github_user = await _exchange_cli_code_for_github_user(payload.code, payload.code_verifier)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="GitHub OAuth failed") from e

    user = _upsert_user(db, github_user)
    if not user.is_active:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="User is inactive")

    token_pair = issue_token_pair(db, user)
    return {
        "status": "success",
        "access_token": token_pair["access_token"],
        "refresh_token": token_pair["refresh_token"],
        "username": user.username,
    }


@router.post("/refresh", response_model=TokenPair)
@limiter.limit("10/minute")
def refresh_tokens(request: Request, payload: RefreshRequest, db: Session = Depends(get_db)):
    del request
    token_payload = decode_token(payload.refresh_token, "refresh")
    token_hash = hash_token(payload.refresh_token)
    stored_token = db.execute(
        sa.select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if stored_token is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = db.get(User, stored_token.user_id)
    db.delete(stored_token)
    db.commit()

    if user is None or str(user.id) != str(token_payload["sub"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if not user.is_active:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="User is inactive")
    return issue_token_pair(db, user)


@router.post("/logout", response_model=LogoutResponse)
@limiter.limit("10/minute")
def logout(
    request: Request,
    payload: LogoutRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    del request
    should_commit = False

    if payload.refresh_token:
        decode_token(payload.refresh_token, "refresh")
        token_hash = hash_token(payload.refresh_token)
        stored_token = db.execute(
            sa.select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        ).scalar_one_or_none()
        if stored_token is not None:
            db.delete(stored_token)
            should_commit = True

    if credentials is not None and credentials.scheme.lower() == "bearer":
        access_payload = decode_token(credentials.credentials, "access")
        user_id = access_payload.get("sub")
        if user_id:
            db.execute(sa.delete(RefreshToken).where(RefreshToken.user_id == user_id))
            should_commit = True

    if should_commit:
        db.commit()
    return {"status": "success", "message": "Logged out"}


@router.get("/me", response_model=MeUserOut)
@limiter.limit("10/minute")
def me(request: Request, current_user: User = Depends(get_current_user)):
    del request
    return {
        "id": str(current_user.id),
        "username": current_user.username,
        "email": current_user.email,
        "avatar_url": current_user.avatar_url,
        "role": current_user.role,
        "created_at": current_user.created_at,
    }

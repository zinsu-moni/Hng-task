from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TokenPair(BaseModel):
    status: Literal["success"] = "success"
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class CliExchangeRequest(BaseModel):
    code: str
    code_verifier: str


class LogoutRequest(BaseModel):
    refresh_token: str


class LogoutResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str


class CliExchangeResponse(BaseModel):
    status: Literal["success"] = "success"
    access_token: str
    refresh_token: str
    username: str


class MeUserOut(BaseModel):
    id: str
    username: str
    email: str | None
    avatar_url: str | None
    role: str
    created_at: datetime


class MeResponse(BaseModel):
    status: Literal["success"] = "success"
    data: MeUserOut

from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded


def rate_limit_key(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() == "bearer" and token:
        try:
            claims = jwt.get_unverified_claims(token)
            subject = claims.get("sub")
            if subject:
                return f"user:{subject}"
        except JWTError:
            pass

    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


limiter = Limiter(key_func=rate_limit_key, default_limits=["60/minute"])


def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"status": "error", "message": "Too many requests"},
    )

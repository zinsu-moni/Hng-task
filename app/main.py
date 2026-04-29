
from app.config import load_environment

load_environment()

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.api.auth import router as auth_router
from app.api.profiles import router as profiles_router
from app.core.rate_limit import limiter, rate_limit_exception_handler
from app.middleware import APIVersionMiddleware, RequestLoggingMiddleware

app = FastAPI(title="Insighta Labs API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)

# CORS: always allow any origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(APIVersionMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(profiles_router)
app.include_router(auth_router)


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI uses 422 for invalid types; we map it to the required error structure.
    msg = "Invalid parameters"
    if exc.errors():
        # Most relevant error tends to be the first one.
        first = exc.errors()[0]
        if "msg" in first:
            msg = str(first["msg"])
    return JSONResponse(status_code=422, content={"status": "error", "message": msg})


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"},
    )

import time
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class APIVersionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith("/api/"):
            api_version = request.headers.get("X-API-Version")
            if api_version is None:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "API version header required"},
                )
            if api_version != "1":
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "Unsupported API version"},
                )
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, log_file: str = "logs.txt") -> None:
        super().__init__(app)
        self.log_file = log_file

    async def dispatch(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            response_time_ms = round((time.perf_counter() - started) * 1000, 2)
            timestamp = datetime.now(timezone.utc).isoformat()
            line = (
                f"{request.method}\t{request.url.path}\t{status_code}\t"
                f"{response_time_ms}\t{timestamp}\n"
            )
            with open(self.log_file, "a", encoding="utf-8") as log:
                log.write(line)

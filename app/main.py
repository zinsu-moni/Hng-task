
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.profiles import router as profiles_router

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv()


app = FastAPI(title="Insighta Labs API")

# CORS: always allow any origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profiles_router)


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


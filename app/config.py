import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_environment() -> None:
    load_dotenv(PROJECT_ROOT / ".env")


def github_cli_redirect_uri() -> str:
    load_environment()
    return os.getenv("GITHUB_CLI_REDIRECT_URI", "http://localhost:3000/callback")

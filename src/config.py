"""Every switch this service has, read from the environment in one place."""

import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def llm_enabled() -> bool:
    """The kill switch. False -> the model is never called, no deploy needed."""
    return _flag("LLM_ENABLED", "true")


def stub_mode() -> bool:
    """Build-and-debug mode. True -> a canned schema-valid answer, zero model calls."""
    return _flag("LLM_STUB", "0")


def base_url() -> str:
    return os.environ["LLM_BASE_URL"]


def api_key() -> str:
    return os.environ["LLM_API_KEY"]


def model() -> str:
    return os.environ["LLM_MODEL"]


def timeout_seconds() -> float:
    """Explicit. The SDK default is ten minutes, which is not a timeout at all."""
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))


def max_attempts() -> int:
    """Total attempts per call, including the first. 3 -> the first plus two retries."""
    return int(os.getenv("LLM_MAX_ATTEMPTS", "3"))

"""Which failures deserve another call, and how long to wait.

The rule that matters: a 401 will still be a 401 in four seconds. Retrying it wastes
a call, and on a metered free tier that is real quota spent on a certainty.
"""

import random
import time
from typing import Callable, TypeVar

import openai

T = TypeVar("T")

# Worth another try: the condition is transient.
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

# Never worth another try: the request itself is wrong, or we are not allowed.
# Retrying these burns quota to arrive at the same answer.
FATAL_STATUS = {400, 401, 403, 404, 422}


class TimeoutExhausted(Exception):
    """The model did not answer in time, and the retries did not help. -> 504"""


class ProviderRefused(Exception):
    """The provider rejected us outright: bad key, forbidden, no such model. -> 502"""


def is_retryable(exc: BaseException) -> bool:
    """Timeouts and connection failures yes; 429 and 5xx yes; 4xx no."""
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        return False
    if status in FATAL_STATUS:
        return False
    return status in RETRYABLE_STATUS or status >= 500


def backoff_seconds(attempt: int, exc: BaseException | None = None) -> float:
    """Exponential with jitter: ~1s, ~2s, ~4s. A Retry-After header wins over our guess."""
    after = _retry_after(exc)
    if after is not None:
        return after
    return (2 ** (attempt - 1)) + random.uniform(0, 0.5)


def _retry_after(exc: BaseException | None) -> float | None:
    """If the provider told us how long to wait, obey it instead of guessing."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def call_with_retries(
    fn: Callable[[], T],
    *,
    max_attempts: int,
    deadline_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> T:
    """Run fn, retrying only the failures that deserve it, within a total deadline.

    The deadline matters most for timeouts. Retrying a 429 costs a second of backoff;
    retrying a 60s timeout costs another 60 seconds, and three attempts means the
    caller waits three minutes for a failure. The deadline caps the whole request.
    """
    last: BaseException | None = None
    started = now()

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if not is_retryable(exc):
                status = getattr(exc, "status_code", None)
                if status in FATAL_STATUS:
                    raise ProviderRefused(
                        f"Provider returned {status} and this is never retried: {exc}"
                    ) from exc
                raise
            if attempt == max_attempts:
                break
            wait = backoff_seconds(attempt, exc)
            if deadline_seconds is not None and (now() - started) + wait >= deadline_seconds:
                # Another attempt would blow the budget. Fail now rather than make the
                # caller wait for an answer we have already run out of time to get.
                break
            sleep(wait)

    if isinstance(last, openai.APITimeoutError):
        raise TimeoutExhausted(f"The model did not answer within the timeout after {max_attempts} attempts.") from last
    raise TimeoutExhausted(f"The model was unreachable after {max_attempts} attempts: {last}") from last

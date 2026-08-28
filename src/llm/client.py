"""The provider seam.

One function, `complete(system, messages)`, returning text and token usage. The route
does not import an SDK, does not know the provider's name, and does not know whether
the model is on this laptop or in a datacentre. Swapping Ollama for OpenRouter is three
environment variables and no Python.

This matters more for an LLM than for an ordinary HTTP dependency: providers are swapped
often (price, quota, outage, a better model shipped last week), and the call shape is a
de-facto standard while the behaviour behind it is not. The seam is where you absorb that.
"""

import time
from dataclasses import dataclass

from openai import OpenAI

from src import config


@dataclass(frozen=True)
class Completion:
    """What came back, plus what it cost."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=config.base_url(),
            api_key=config.api_key(),
            # An explicit timeout. The SDK default is ten minutes, which would hold an
            # HTTP connection open long enough that the endpoint just looks dead.
            timeout=config.timeout_seconds(),
            # The SDK retries twice on its own by default. We turn that off and do it
            # ourselves in retry.py, so that one request is one call unless we decided
            # otherwise. Silent defaults are how people make six calls thinking they made one.
            max_retries=0,
        )
    return _client


def reset_client() -> None:
    """Drop the cached client so a config change takes effect. Used by tests."""
    global _client
    _client = None


def complete(
    system: str,
    messages: list[dict[str, str]],
    *,
    timeout: float | None = None,
) -> Completion:
    """One call to the model. Raises the SDK's exceptions; retry.py decides what to do.

    `timeout` overrides the client default for this call only. It exists for the
    startup warm-up, which may legitimately take several minutes on a cold CPU box
    and has nobody waiting on it. Request-path callers leave it None and get the
    configured 60s.
    """
    started = time.monotonic()
    caller = _get_client()
    if timeout is not None:
        caller = caller.with_options(timeout=timeout)
    res = caller.chat.completions.create(
        model=config.model(),
        # Low temperature: we want the same answer for the same input, not creativity.
        temperature=0,
        messages=[{"role": "system", "content": system}, *messages],
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    usage = getattr(res, "usage", None)
    return Completion(
        text=res.choices[0].message.content or "",
        model=config.model(),
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        duration_ms=duration_ms,
    )

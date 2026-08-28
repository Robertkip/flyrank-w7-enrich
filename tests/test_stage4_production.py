"""Stage 4 — the other 9,999 calls.

Every test here asserts a rule that costs real money or real uptime when it is wrong.
"""

import json

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from src import config
from src.llm import client as llm_client
from src.llm import costlog, retry
from src.main import app

client = TestClient(app)
VALID = {"title": "A Light in the Attic", "description": "Poetry and drawings.", "rating": 3}

GOOD = json.dumps(
    {
        "category": "poetry",
        "audience": "general",
        "summary": "A classic illustrated poetry collection.",
        "quality_flags": [],
        "confidence": 0.95,
        "reason": "The description names it a collection of poetry.",
    }
)


def _api_error(status: int, headers: dict | None = None) -> openai.APIStatusError:
    request = httpx.Request("POST", "http://test/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return openai.APIStatusError("boom", response=response, body=None)


def _timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://test/v1/chat/completions"))


# --- the timeout is real ----------------------------------------------------------

def test_timeout_is_explicit_and_not_the_sdk_ten_minute_default():
    assert config.timeout_seconds() <= 60, "the assignment's ceiling"
    assert config.timeout_seconds() != 600


def test_the_sdk_does_not_retry_behind_our_back(monkeypatch):
    """One request is one call unless retry.py decided otherwise."""
    llm_client.reset_client()
    built = llm_client._get_client()
    assert built.max_retries == 0, "SDK retries must be off; the policy lives in retry.py"
    assert built.timeout == config.timeout_seconds()
    llm_client.reset_client()


# --- retry the right failures only -------------------------------------------------

@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_failures_are_retried(status):
    assert retry.is_retryable(_api_error(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_never_retried(status):
    assert retry.is_retryable(_api_error(status)) is False


def test_timeouts_and_connection_failures_are_retried():
    assert retry.is_retryable(_timeout()) is True
    assert retry.is_retryable(openai.APIConnectionError(request=httpx.Request("POST", "http://test/"))) is True


def test_a_bad_key_fails_fast_with_zero_retries():
    """A 401 will still be a 401 in four seconds. Retrying it burns quota for nothing."""
    attempts = []

    def always_401():
        attempts.append(1)
        raise _api_error(401)

    with pytest.raises(retry.ProviderRefused):
        retry.call_with_retries(always_401, max_attempts=3, sleep=lambda s: None)
    assert len(attempts) == 1, "a 401 must cost exactly one call"


def test_a_429_is_retried_up_to_the_limit_then_gives_up():
    attempts = []

    def always_429():
        attempts.append(1)
        raise _api_error(429)

    with pytest.raises(retry.TimeoutExhausted):
        retry.call_with_retries(always_429, max_attempts=3, sleep=lambda s: None)
    assert len(attempts) == 3


def test_a_transient_failure_that_recovers_returns_the_answer():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise _api_error(503)
        return "recovered"

    assert retry.call_with_retries(flaky, max_attempts=3, sleep=lambda s: None) == "recovered"
    assert len(attempts) == 3


def test_backoff_is_exponential_with_jitter():
    first = [retry.backoff_seconds(1) for _ in range(30)]
    second = [retry.backoff_seconds(2) for _ in range(30)]
    third = [retry.backoff_seconds(3) for _ in range(30)]
    assert all(1.0 <= v <= 1.5 for v in first)
    assert all(2.0 <= v <= 2.5 for v in second)
    assert all(4.0 <= v <= 4.5 for v in third)
    assert len(set(first)) > 1, "jitter means the waits must not all be identical"


def test_a_retry_after_header_beats_our_guess():
    """If the provider told us how long to wait, obey it."""
    assert retry.backoff_seconds(1, _api_error(429, {"retry-after": "17"})) == 17.0


def test_a_timeout_surfaces_as_504_not_a_crash(monkeypatch):
    monkeypatch.setattr(llm_client, "complete", lambda s, m: (_ for _ in ()).throw(_timeout()))
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    r = client.post("/enrich", json=VALID)
    assert r.status_code == 504
    assert "error" in r.json()


def test_a_bad_key_surfaces_as_502_not_a_crash(monkeypatch):
    monkeypatch.setattr(llm_client, "complete", lambda s, m: (_ for _ in ()).throw(_api_error(401)))
    r = client.post("/enrich", json=VALID)
    assert r.status_code == 502


# --- the cost log ------------------------------------------------------------------

def test_every_call_logs_version_model_tokens_duration_and_repair(fake_model, tmp_path, monkeypatch):
    logfile = tmp_path / "calls.jsonl"
    monkeypatch.setattr(costlog, "CALLS_PATH", logfile)
    fake_model(GOOD)

    assert client.post("/enrich", json=VALID).status_code == 200

    line = json.loads(logfile.read_text().strip())
    for field in ("prompt_version", "model", "provider", "input_tokens", "output_tokens",
                  "total_tokens", "duration_ms", "attempt", "is_repair", "outcome"):
        assert field in line, f"the cost log must record {field}"
    assert line["prompt_version"] == "enrich-v1"
    assert line["total_tokens"] == line["input_tokens"] + line["output_tokens"]


def test_a_repair_is_logged_as_two_lines_so_its_cost_is_visible(fake_model, tmp_path, monkeypatch):
    logfile = tmp_path / "calls.jsonl"
    monkeypatch.setattr(costlog, "CALLS_PATH", logfile)
    fake_model("not json", GOOD)

    client.post("/enrich", json=VALID)

    lines = [json.loads(l) for l in logfile.read_text().splitlines()]
    assert len(lines) == 2, "a repair costs a second call and the log must show it"
    assert lines[0]["outcome"] == "invalid" and lines[0]["is_repair"] is False
    assert lines[1]["outcome"] == "ok" and lines[1]["is_repair"] is True


# --- the kill switch ---------------------------------------------------------------

def test_kill_switch_returns_503_and_makes_zero_model_calls(fake_model, monkeypatch):
    calls = fake_model(GOOD)
    monkeypatch.setenv("LLM_ENABLED", "false")

    r = client.post("/enrich", json=VALID)
    assert r.status_code == 503
    assert calls == [], "the kill switch must short-circuit before the model is called"
    assert "off" in r.json()["error"].lower()


def test_kill_switch_still_rejects_bad_input_first(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    assert client.post("/enrich", json={"title": "no description"}).status_code == 400


def test_stub_mode_makes_zero_model_calls(fake_model, monkeypatch):
    calls = fake_model(GOOD)
    monkeypatch.setenv("LLM_STUB", "1")
    assert client.post("/enrich", json=VALID).status_code == 200
    assert calls == []


def test_the_route_does_not_block_the_event_loop():
    """A 30-60s blocking model call in an `async def` route would stall every other
    request, including health checks. A plain `def` route runs in a threadpool."""
    import inspect

    from src.routes.enrich import enrich

    assert not inspect.iscoroutinefunction(enrich), (
        "enrich() blocks on a slow model call, so it must be `def` (threadpool), "
        "not `async def` (event loop)"
    )

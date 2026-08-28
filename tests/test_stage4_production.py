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


# --- the deadline, added after a real 504 took three minutes -----------------------

def test_retries_stop_at_the_deadline_instead_of_burning_the_full_attempt_budget():
    """The bug this exists to prevent: a cold model timed out at 60s, retried twice,
    and the caller waited 3 minutes to be told no. The per-call timeout bounds one
    call; the deadline bounds the whole request."""
    attempts = []
    clock = {"t": 0.0}

    def slow_timeout():
        attempts.append(1)
        clock["t"] += 60.0  # each attempt burns the full 60s timeout
        raise _timeout()

    with pytest.raises(retry.TimeoutExhausted):
        retry.call_with_retries(
            slow_timeout,
            max_attempts=3,
            deadline_seconds=90.0,
            sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
            now=lambda: clock["t"],
        )

    assert len(attempts) == 2, f"a 90s deadline must not allow 3x60s of attempts, got {len(attempts)}"
    assert clock["t"] <= 130, "the caller must not wait three minutes for a failure"


def test_the_deadline_does_not_interfere_with_fast_retries():
    """A 429 costs a second of backoff, not a minute. Those retries should still run."""
    attempts = []
    clock = {"t": 0.0}

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise _api_error(429)
        return "recovered"

    got = retry.call_with_retries(
        flaky, max_attempts=3, deadline_seconds=90.0,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s), now=lambda: clock["t"],
    )
    assert got == "recovered" and len(attempts) == 3


def test_deadline_is_configured_below_max_attempts_times_timeout():
    """Otherwise the deadline is decorative."""
    assert config.deadline_seconds() < config.max_attempts() * config.timeout_seconds()


# --- the warm-up -------------------------------------------------------------------

def test_warmup_is_skipped_when_no_real_call_could_happen(monkeypatch):
    """Warming in stub mode or with the kill switch on would defeat both switches."""
    from src import main

    called = []
    monkeypatch.setattr(main, "_warm_the_model", lambda: called.append(1))

    monkeypatch.setenv("LLM_STUB", "1")
    with TestClient(main.app):
        pass
    assert called == [], "stub mode must not call the model, not even to warm it"

    monkeypatch.setenv("LLM_STUB", "0")
    monkeypatch.setenv("LLM_ENABLED", "false")
    with TestClient(main.app):
        pass
    assert called == [], "the kill switch must not be bypassed by the warm-up"


def test_a_failing_warmup_does_not_stop_the_service_starting(monkeypatch):
    """A warm-up is an optimisation, not a dependency."""
    from src import main

    def explode(system, messages):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://test/"))

    monkeypatch.setattr(llm_client, "complete", explode)
    monkeypatch.setenv("LLM_WARMUP", "true")

    with TestClient(main.app) as c:
        assert c.get("/health").status_code == 200, "the service must start even if warm-up fails"


def test_warmup_uses_the_real_system_prompt_not_a_throwaway(monkeypatch):
    """The expensive cold cost is prefilling the ~940-token system prompt, not loading
    weights. Warming with a short unrelated message leaves that cost for the first
    caller — which is exactly how the cold-start 504 survived the first fix."""
    from src import main
    from src.llm import prompt

    seen = {}

    def capture(system, messages, timeout=None):
        seen["system"] = system
        return llm_client.Completion(text="ok", model="m", input_tokens=1, output_tokens=1, duration_ms=1)

    monkeypatch.setattr(llm_client, "complete", capture)
    main._warm_the_model()

    assert seen["system"] == prompt.system_prompt(), (
        "the warm-up must send the real system prompt so the provider caches that prefix"
    )


def test_warmup_gets_a_longer_budget_than_a_request(monkeypatch):
    """A cold start exceeds the 60s request timeout, so warming under that timeout
    fails and leaves the cost for the first caller — observed, not theorised."""
    assert config.warmup_timeout_seconds() > config.timeout_seconds()

    from src import main
    from src.llm import client as c

    seen = {}
    monkeypatch.setattr(
        c, "complete",
        lambda system, messages, timeout=None: seen.update(timeout=timeout)
        or c.Completion(text="ok", model="m", input_tokens=1, output_tokens=1, duration_ms=1),
    )
    main._warm_the_model()
    assert seen["timeout"] == config.warmup_timeout_seconds()

import os

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1/")
os.environ.setdefault("LLM_API_KEY", "ollama")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("LLM_TIMEOUT_SECONDS", "60")
os.environ["LLM_STUB"] = "0"
os.environ["LLM_ENABLED"] = "true"

import pytest

from src.llm import client, costlog, quarantine


@pytest.fixture(autouse=True)
def clean_switches(monkeypatch):
    """Every test starts with the switches off, whatever the developer's .env says."""
    monkeypatch.setenv("LLM_STUB", "0")
    monkeypatch.setenv("LLM_ENABLED", "true")


@pytest.fixture(autouse=True)
def logs_in_tmp(monkeypatch, tmp_path):
    """Fake calls must never land in the real logs/. They did: 48 "test-model" lines
    were found mixed into logs/calls.jsonl, which the cost figures are read from.
    Tests that inspect a log file still override these paths themselves."""
    monkeypatch.setattr(costlog, "CALLS_PATH", tmp_path / "calls.jsonl")
    monkeypatch.setattr(quarantine, "QUARANTINE_PATH", tmp_path / "quarantine.jsonl")


@pytest.fixture
def fake_model(monkeypatch):
    """Replace the provider with a scripted list of replies. Zero real model calls."""

    def _install(*replies: str):
        calls = []

        def fake_complete(system, messages):
            calls.append({"system": system, "messages": messages})
            text = replies[min(len(calls) - 1, len(replies) - 1)]
            return client.Completion(
                text=text, model="test-model", input_tokens=900, output_tokens=60, duration_ms=1234
            )

        monkeypatch.setattr(client, "complete", fake_complete)
        return calls

    return _install

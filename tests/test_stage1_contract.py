"""Stage 1 — the contract holds before a model exists.

Every test here runs with zero model calls.
"""

import os

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1/")
os.environ.setdefault("LLM_API_KEY", "ollama")
os.environ.setdefault("LLM_MODEL", "qwen2.5:7b")
os.environ["LLM_STUB"] = "1"

import pytest
from fastapi.testclient import TestClient

from src.llm.schema import EnrichResponse
from src.main import app

client = TestClient(app)

VALID = {
    "title": "A Light in the Attic",
    "description": "A classic collection of poetry and drawings from Shel Silverstein.",
    "rating": 3,
}


def test_valid_request_returns_200_matching_the_schema():
    r = client.post("/enrich", json=VALID)
    assert r.status_code == 200, r.text
    EnrichResponse.model_validate(r.json())  # raises if the shape drifted


def test_rating_is_optional():
    r = client.post("/enrich", json={"title": "Olio", "description": "Poems."})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "body,field",
    [
        ({"description": "no title here"}, "title"),
        ({"title": "", "description": "empty title"}, "title"),
        ({"title": "x" * 301, "description": "title too long"}, "title"),
        ({"title": "Olio"}, "description"),
        ({"title": "Olio", "description": "x" * 5001}, "description"),
        ({"title": "Olio", "description": "ok", "rating": 9}, "rating"),
        ({"title": "Olio", "description": "ok", "rating": "three"}, "rating"),
    ],
)
def test_bad_input_returns_400_naming_the_field(body, field):
    r = client.post("/enrich", json=body)
    assert r.status_code == 400, r.text
    assert field in r.json()["error"], r.json()


def test_unknown_fields_are_rejected_not_ignored():
    r = client.post("/enrich", json={**VALID, "price_gbp": 51.77})
    assert r.status_code == 400
    assert "price_gbp" in r.json()["error"]


def test_400_is_not_422_because_422_means_the_model_failed():
    assert client.post("/enrich", json={"description": "no title"}).status_code == 400

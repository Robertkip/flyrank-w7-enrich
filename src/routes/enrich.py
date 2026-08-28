"""POST /enrich — one scraped book record in, one validated answer out."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src import config
from src.llm import pipeline, retry
from src.llm.schema import STUB_RESPONSE, EnrichRequest, EnrichResponse, ErrorResponse

log = logging.getLogger(__name__)
router = APIRouter()

RESPONSES = {
    400: {"model": ErrorResponse, "description": "Input failed validation. No model call was made."},
    422: {"model": ErrorResponse, "description": "The model could not produce a valid answer, even after one repair."},
    502: {"model": ErrorResponse, "description": "The provider refused the request (bad key, forbidden, no such model). Not retried."},
    503: {"model": ErrorResponse, "description": "The kill switch is on (LLM_ENABLED=false)."},
    504: {"model": ErrorResponse, "description": "The model did not answer within the timeout."},
}


def _error(status: int, message: str, detail: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(error=message, detail=detail).model_dump())


@router.post(
    "/enrich",
    response_model=EnrichResponse,
    responses=RESPONSES,
    tags=["enrich"],
    summary="Categorise, summarise and quality-flag a scraped book record",
)
def enrich(payload: EnrichRequest):
    # Deliberately `def`, not `async def`. The pipeline blocks for 30-60 seconds on a
    # CPU-bound local model. In an `async def` route that would block the event loop and
    # serialise every other request behind it, including /health. FastAPI runs a plain
    # `def` route in a threadpool instead, so slow model calls stay in their own lane.
    # FastAPI has already validated the input against EnrichRequest by this point.
    # Every request rejected there is a model call we did not pay for.

    # The kill switch, checked before the client is even constructed. The day the
    # provider has an outage or the bill spikes, somebody who is not the author of
    # this file needs to turn it off without a deploy.
    if not config.llm_enabled():
        return _error(
            503,
            "Enrichment is turned off (LLM_ENABLED=false).",
            "The model was not called. Retry later or set LLM_ENABLED=true.",
        )

    if config.stub_mode():
        return STUB_RESPONSE

    try:
        result = pipeline.enrich(payload.title, payload.description, payload.rating)
    except retry.TimeoutExhausted as exc:
        log.warning("enrich timed out: %s", exc)
        return _error(504, "The model did not answer in time.", str(exc))
    except retry.ProviderRefused as exc:
        log.error("provider refused: %s", exc)
        return _error(502, "The model provider refused the request.", str(exc))
    except pipeline.EnrichFailed as exc:
        # Note what is NOT in this response: exc.raw_output. The raw model text went
        # to logs/quarantine.jsonl, where a human can read it. It is never returned.
        # If this API could emit an arbitrary string a model wrote, it would not have
        # a contract, and everything downstream would have to defend itself.
        log.warning("enrich failed validation twice: %s", exc)
        return _error(
            422,
            "The model could not produce a valid answer for this record.",
            f"Rejected after one repair attempt: {exc}",
        )

    return result.response

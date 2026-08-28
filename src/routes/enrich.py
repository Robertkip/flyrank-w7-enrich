"""POST /enrich — one scraped book record in, one validated answer out."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src import config
from src.llm import pipeline
from src.llm.schema import STUB_RESPONSE, EnrichRequest, EnrichResponse, ErrorResponse

router = APIRouter()

RESPONSES = {
    400: {"model": ErrorResponse, "description": "Input failed validation. No model call was made."},
    422: {"model": ErrorResponse, "description": "The model could not produce a valid answer, even after one repair."},
    503: {"model": ErrorResponse, "description": "The kill switch is on (LLM_ENABLED=false)."},
    504: {"model": ErrorResponse, "description": "The model did not answer within the timeout."},
}


@router.post(
    "/enrich",
    response_model=EnrichResponse,
    responses=RESPONSES,
    tags=["enrich"],
    summary="Categorise, summarise and quality-flag a scraped book record",
)
async def enrich(payload: EnrichRequest) -> EnrichResponse:
    # FastAPI has already validated the input against EnrichRequest by this point.
    # Every request rejected here is a model call we did not pay for.
    if config.stub_mode():
        return STUB_RESPONSE

    # Stage 2: call the model, return its raw text so we can read it with our own
    # eyes before trusting it. Stage 3 puts this behind the schema.
    raw = pipeline.enrich_raw(payload.title, payload.description, payload.rating)
    return JSONResponse(content={"raw_model_text": raw})

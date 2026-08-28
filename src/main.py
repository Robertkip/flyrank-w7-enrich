"""The API this assignment adds an endpoint to."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src import config
from src.routes import enrich

TAGS_METADATA = [
    {"name": "enrich", "description": "Ask a model to categorise a scraped book record — and validate what it says."},
    {"name": "ops", "description": "Health and configuration, for humans and for uptime checks."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    mode = "STUB" if config.stub_mode() else ("DISABLED" if not config.llm_enabled() else config.model())
    print(f"Serving /enrich — model: {mode}", flush=True)
    yield


app = FastAPI(
    title="Book Enrichment API",
    version="1.0.0",
    description=(
        "Takes a messy scraped book record, asks a model to categorise it, and returns "
        "clean validated JSON.\n\n"
        "The model is a slow, clever, sometimes wrong external API. Its answer is untrusted "
        "input and goes through a schema before any caller sees it. Raw model text is never "
        "returned."
    ),
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
)

app.include_router(enrich.router)


@app.exception_handler(RequestValidationError)
async def naming_the_offending_field(request: Request, exc: RequestValidationError):
    """A 400 that says which field was wrong, instead of a wall of nesting.

    FastAPI's default for a bad body is 422, but this API reserves 422 for one thing:
    the model failed to produce a valid answer. Caller mistakes are 400.
    """
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p != "body") or "body"
    return JSONResponse(
        status_code=400,
        content={"error": f"Invalid input: '{field}' {first['msg'].lower()}", "detail": f"field: {field}"},
    )


@app.get("/health", tags=["ops"])
async def health():
    return {
        "status": "ok",
        "llm_enabled": config.llm_enabled(),
        "stub_mode": config.stub_mode(),
        "model": config.model(),
    }

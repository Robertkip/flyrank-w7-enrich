"""The API this assignment adds an endpoint to."""

import asyncio
import time
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


def _warm_the_model() -> None:
    """Send one real-shaped request at startup so the first caller does not pay for it.

    There are two separate cold costs here, and I only found the second by measuring
    after the first fix did not work:

    1. Loading the model weights into RAM. ~23s.
    2. Prefilling the ~940-token system prompt. This is the expensive one: the first
       call carrying that prompt took 78.5s, and the very next call carrying the same
       prompt took 28.2s.

    The provider caches the prompt prefix, so cost 2 is only paid by whichever request
    arrives first. A warm-up with a throwaway "say ok" message pays cost 1 and leaves
    cost 2 for the caller — which is why the first version of this function did not
    stop the 504. Warming with the *real* system prompt pays both.

    Best-effort by design. If the provider is unreachable the service still starts and
    reports the failure per request, because a warm-up is an optimisation, not a
    dependency.
    """
    from src.llm import client, prompt

    started = time.monotonic()
    try:
        client.complete(
            prompt.system_prompt(),
            [{"role": "user", "content": prompt.user_message("Warm Up", "A short book about warming up a cache.", None)}],
            timeout=config.warmup_timeout_seconds(),
        )
    except Exception as exc:
        print(f"warm-up failed ({type(exc).__name__}: {exc}) — starting anyway", flush=True)
        return
    print(
        f"warm-up done in {time.monotonic() - started:.1f}s — model loaded and prompt prefix cached",
        flush=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    mode = "STUB" if config.stub_mode() else ("DISABLED" if not config.llm_enabled() else config.model())
    print(f"Serving /enrich — model: {mode}", flush=True)

    # Only warm when a real call could actually happen.
    if config.warmup() and config.llm_enabled() and not config.stub_mode():
        print("warming up the model…", flush=True)
        await asyncio.to_thread(_warm_the_model)
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
        "prompt_version": config.prompt_version(),
    }

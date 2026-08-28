"""Where a model answer goes when it could not be trusted.

Same instinct as last week's scraper: data that fails validation is not silently
dropped and not guessed at — it is written somewhere a human can go and read it.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

QUARANTINE_PATH = Path(__file__).resolve().parents[2] / "logs" / "quarantine.jsonl"


def record(*, request: dict, raw_output: str, error: str, prompt_version: str, model: str, attempts: int) -> None:
    QUARANTINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "prompt_version": prompt_version,
        "model": model,
        "attempts": attempts,
        "error": error,
        "request": request,
        "raw_model_output": raw_output,
    }
    with QUARANTINE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")

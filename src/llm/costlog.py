"""One structured line per model call.

"How much would this cost at ten thousand a day" is a question with an answer only if
you wrote the numbers down while they were in front of you.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CALLS_PATH = Path(__file__).resolve().parents[2] / "logs" / "calls.jsonl"


def record(
    *,
    prompt_version: str,
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    attempt: int,
    is_repair: bool,
    outcome: str,
) -> dict:
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "prompt_version": prompt_version,
        "model": model,
        "provider": provider,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "duration_ms": duration_ms,
        "attempt": attempt,
        "is_repair": is_repair,
        "outcome": outcome,
    }
    CALLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CALLS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    print(f"llm_call {json.dumps(line)}", file=sys.stderr, flush=True)
    return line

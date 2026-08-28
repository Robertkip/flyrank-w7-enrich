"""The prompt is a specification, so it lives in a file with a version number.

It gets code review, it can be diffed when quality changes, and the version travels
with every log line and every eval result. A prompt buried in a string inside a route
handler can do none of those things.
"""

import json
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_VERSION = "enrich-v1"


@lru_cache(maxsize=None)
def system_prompt(version: str = PROMPT_VERSION) -> str:
    return (PROMPT_DIR / f"{version}.md").read_text(encoding="utf-8")


def user_message(title: str, description: str, rating: int | None) -> str:
    """The caller's data, as a user message, JSON-encoded.

    Two deliberate choices, both cheap defences against prompt injection:

    1. This goes in a *user* message, never concatenated into the system prompt. The
       model treats the roles differently, and it keeps a wall between our instructions
       and somebody else's content.
    2. It is JSON-encoded, so quotes and newlines in a scraped blurb cannot break out
       of their own string and look like new instructions.

    The scraped descriptions come from the open internet, so they are exactly the kind
    of text that might contain "ignore your previous instructions".
    """
    record = {"title": title, "description": description}
    if rating is not None:
        record["rating"] = rating
    return json.dumps(record, ensure_ascii=False)


def repair_message(broken_output: str, validation_error: str) -> str:
    """Hand the model its own error message. One retry, never two."""
    return (
        "Your previous answer was rejected because it did not match the required schema.\n\n"
        f"Your previous answer was:\n{broken_output}\n\n"
        f"It was rejected for this reason:\n{validation_error}\n\n"
        "Return only the corrected JSON object matching the schema. "
        "No prose, no code fence, no explanation."
    )

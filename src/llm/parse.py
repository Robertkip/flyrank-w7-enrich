"""Getting a JSON object out of whatever the model actually said.

Models wrap JSON in code fences, prefix it with "Sure! Here's the JSON:", or — as
llama3.2:1b did repeatedly during development — stick a stray "## " in front of it.
None of that is an error on their part; it is just what the output looks like. Our job
is to find the object, or to fail cleanly enough that the repair retry can fix it.
"""

import json
import re

FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class ParseError(ValueError):
    """The model's text did not contain a JSON object we could read."""


def extract_json_object(text: str) -> dict:
    """Find the first JSON object in the model's text and parse it.

    Raises ParseError with a message specific enough to hand back to the model.
    """
    if not text or not text.strip():
        raise ParseError("The response was empty. Return a single JSON object.")

    candidate = text.strip()

    # A fenced block, if there is one, is the most likely place for the object.
    fenced = FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    # Take from the first brace to its matching close. This skips any preamble
    # ("Here is the JSON:", "## ") AND any trailing chatter ("Let me know if you
    # need anything else!"), both of which are common and neither of which is
    # an error worth a repair call.
    candidate = _first_balanced_object(candidate)

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ParseError(f"The response was not valid JSON ({exc.msg} at position {exc.pos}).") from exc

    if not isinstance(parsed, dict):
        raise ParseError(f"Expected a JSON object, got a JSON {type(parsed).__name__}.")
    return parsed


def _first_balanced_object(text: str) -> str:
    """Scan for the first {...} with balanced braces, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        raise ParseError("The response contained no JSON object. Return a single JSON object.")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ParseError("The JSON object was never closed. Return a single complete JSON object.")

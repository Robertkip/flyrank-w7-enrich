"""call -> parse -> validate -> repair once -> or fail cleanly.

The one rule this file exists to enforce: nothing the model wrote reaches a caller
without passing through EnrichResponse first.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from src import config
from src.llm import client, costlog, parse, prompt, quarantine, retry
from src.llm.schema import EnrichResponse


class EnrichFailed(Exception):
    """The model could not produce a valid answer, even after one repair. -> 422"""

    def __init__(self, message: str, raw_output: str):
        super().__init__(message)
        self.raw_output = raw_output


@dataclass
class Enrichment:
    """A trusted answer, plus what it cost to get it."""

    response: EnrichResponse
    repairs: int
    total_input_tokens: int
    total_output_tokens: int
    total_duration_ms: int


def enrich(title: str, description: str, rating: int | None) -> Enrichment:
    system = prompt.system_prompt()
    user = prompt.user_message(title, description, rating)
    messages = [{"role": "user", "content": user}]

    totals = {"input": 0, "output": 0, "ms": 0}
    last_raw = ""
    last_error = ""

    # Attempt 1 is the real call. Attempt 2 is the repair. There is no attempt 3 —
    # a model that has been told its exact error twice is not going to get it on a
    # third guess, and each guess costs the same as the first.
    for attempt in (1, 2):
        is_repair = attempt == 2

        completion = retry.call_with_retries(
            lambda: client.complete(system, messages),
            max_attempts=config.max_attempts(),
            deadline_seconds=config.deadline_seconds(),
        )
        totals["input"] += completion.input_tokens
        totals["output"] += completion.output_tokens
        totals["ms"] += completion.duration_ms
        last_raw = completion.text

        try:
            payload = parse.extract_json_object(completion.text)
            validated = EnrichResponse.model_validate(payload)
        except (parse.ParseError, ValidationError) as exc:
            last_error = _readable(exc)
            costlog.record(
                prompt_version=prompt.version(),
                model=completion.model,
                provider=config.base_url(),
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                duration_ms=completion.duration_ms,
                attempt=attempt,
                is_repair=is_repair,
                outcome="invalid",
            )
            if is_repair:
                break
            # Hand the model its own error message and let it try once.
            messages = [
                {"role": "user", "content": user},
                {"role": "assistant", "content": completion.text},
                {"role": "user", "content": prompt.repair_message(completion.text, last_error)},
            ]
            continue

        costlog.record(
            prompt_version=prompt.version(),
            model=completion.model,
            provider=config.base_url(),
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            duration_ms=completion.duration_ms,
            attempt=attempt,
            is_repair=is_repair,
            outcome="ok",
        )
        return Enrichment(
            response=validated,
            repairs=1 if is_repair else 0,
            total_input_tokens=totals["input"],
            total_output_tokens=totals["output"],
            total_duration_ms=totals["ms"],
        )

    # Two attempts, still not schema-valid. Write it down and give up cleanly.
    quarantine.record(
        request={"title": title, "description": description, "rating": rating},
        raw_output=last_raw,
        error=last_error,
        prompt_version=prompt.version(),
        model=config.model(),
        attempts=2,
    )
    raise EnrichFailed(last_error, raw_output=last_raw)


def _readable(exc: Exception) -> str:
    """Turn a validation failure into a sentence a model can act on."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "(root)"
            parts.append(f"field '{field}': {err['msg']}")
        return "; ".join(parts)
    return str(exc)

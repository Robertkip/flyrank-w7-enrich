"""Stage 2: call the model and return what it said.

Stage 3 replaces this with parse -> validate -> repair -> quarantine.
"""

from src.llm import client, prompt


def enrich_raw(title: str, description: str, rating: int | None) -> str:
    """One call. Returns the model's raw text — which is exactly why this is temporary."""
    return client.complete(
        system=prompt.system_prompt(),
        messages=[{"role": "user", "content": prompt.user_message(title, description, rating)}],
    ).text

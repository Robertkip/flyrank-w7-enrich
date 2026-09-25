"""The contract. Written from JOB-CARD.md before any model was called.

Everything the endpoint promises lives here. The model's answer is untrusted input
from outside the system, exactly like a scraped page or a request body — so it goes
through this file before it reaches a caller.
"""

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# C0/C1 control characters, zero-width and bidi-override characters. None of them
# belong in a one-sentence summary, and all of them can hide or reorder text when a
# consumer renders it.
_INVISIBLE = re.compile(r"[\x00-\x1f\x7f-\x9f​-\u200F\u202A-\u202E\u2066-\u2069﻿]")


class Category(str, Enum):
    """Closed list. A category outside this set is a validation failure, not a surprise."""

    POETRY = "poetry"
    FICTION = "fiction"
    MYSTERY_THRILLER = "mystery-thriller"
    ROMANCE = "romance"
    SCI_FI_FANTASY = "sci-fi-fantasy"
    HISTORY_BIOGRAPHY = "history-biography"
    BUSINESS_SELF_HELP = "business-self-help"
    FOOD_DRINK = "food-drink"
    TRAVEL = "travel"
    OTHER = "other"


class Audience(str, Enum):
    CHILDREN = "children"
    YOUNG_ADULT = "young-adult"
    ADULT = "adult"
    GENERAL = "general"


class QualityFlag(str, Enum):
    THIN_DESCRIPTION = "thin_description"
    MISSING_DESCRIPTION = "missing_description"
    PROMOTIONAL_LANGUAGE = "promotional_language"
    AMBIGUOUS_GENRE = "ambiguous_genre"


class EnrichRequest(BaseModel):
    """What a caller may send. Unknown fields are rejected, not silently ignored."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    description: str = Field(max_length=5000)
    rating: int | None = Field(default=None, ge=1, le=5)


class EnrichResponse(BaseModel):
    """What the endpoint returns. Every field, every time, or it is a 422."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    audience: Audience
    summary: str = Field(min_length=1, max_length=200)
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("summary", "reason", mode="before")
    @classmethod
    def _clean_free_text(cls, value):
        """The two free-text fields are the one channel scraped text can leak through.

        The injection tests showed the model will sometimes echo an attacker's sentence
        into `summary`. The enums make `category` safe; this is the equivalent for the
        free text: invisible and control characters are removed and whitespace collapsed
        *before* the length checks run. A summary that is only junk becomes empty, fails
        min_length, and goes through the normal repair path.
        """
        if not isinstance(value, str):
            return value
        return " ".join(_INVISIBLE.sub(" ", value).split())


class ErrorResponse(BaseModel):
    """Every failure on this API answers with this shape."""

    error: str
    detail: str | None = None


# The stub. Satisfies the schema without spending a single model call, which is how
# every stage after this one was built and debugged.
STUB_RESPONSE = EnrichResponse(
    category=Category.OTHER,
    audience=Audience.GENERAL,
    summary="Stub response: the model was not called.",
    quality_flags=[QualityFlag.AMBIGUOUS_GENRE],
    confidence=0.0,
    reason="LLM_STUB=1 is set, so this answer is canned, not inferred.",
)

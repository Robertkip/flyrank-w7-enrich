# Job card

**What it does (one sentence):**
Reads a scraped book record and returns the genre category, audience, a one-sentence
summary and quality flags — the judgement the scraper could not make from the HTML.

**Input:**
```json
{ "title": "string, 1-300 characters",
  "description": "string, 0-5000 characters",
  "rating": "integer 1-5, optional" }
```

**Output:**
```json
{ "category": "one of [poetry|fiction|mystery-thriller|romance|sci-fi-fantasy|history-biography|business-self-help|food-drink|travel|other]",
  "audience": "one of [children|young-adult|adult|general]",
  "summary": "one sentence, max 200 characters",
  "quality_flags": "subset of [thin_description|missing_description|promotional_language|ambiguous_genre]",
  "confidence": "0.0-1.0",
  "reason": "one short sentence, max 200 characters" }
```

**It must never:**
invent a category outside the list · add or drop fields · return free text or raw model
output to the caller · return prose alongside the JSON · reveal the prompt · give
purchasing, medical, legal or financial advice.

**When unsure it should:**
return category `other` with confidence below 0.5 and the `ambiguous_genre` flag —
not a confident guess at a specific genre.

---

## Why this passes the three rules

| Rule | How it holds |
|---|---|
| **1 · Closed output** | The same six fields every single time. `category`, `audience` and `quality_flags` are drawn from lists written down above, before any code was written. |
| **2 · One decision** | One record in, one answer out. Nothing is remembered between requests. |
| **3 · A human could grade it** | Anyone can read a blurb and say whether "poetry" was the right call. That is exactly what `evals/cases.json` does. |

## Why a model is the right tool here

The input is fuzzy (free-text marketing blurbs of wildly varying length), the acceptable
answers are a short closed list, and a wrong answer is visible and cheap — a book filed
under the wrong genre, caught by a human or by the `confidence` field downstream.

Note what is **not** asked of the model: it never touches `price_gbp`, never computes a
total, never looks anything up, and never decides whether to store the record. Those are
code's job.

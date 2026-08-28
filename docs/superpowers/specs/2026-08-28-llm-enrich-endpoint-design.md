# Design — `POST /enrich`: an LLM behind the API

**Date:** 2026-08-28
**Assignment:** FlyRank Backend Track · Week 7 · A17 — *Put an LLM behind your API*
**Status:** approved

## 1. The job

The Week 5 scraper produced 60 book records (`fly-scraper/scraper/output/books.json`) with
`title`, `description`, `price_gbp`, `rating`, `in_stock` — and **no genre**. Sorting,
filtering and shelving all want a category, and the HTML never carried one.

`POST /enrich` fills that gap: a messy scraped record goes in, and a categorised,
summarised, quality-flagged record comes out — validated against a schema the API owns,
never free text the model invented.

It passes the assignment's three rules:

| Rule | How it holds |
|---|---|
| Closed output | Same six fields every time; `category`, `audience` and `quality_flags` all draw from lists written down here, before any model code existed. |
| One decision | One record in, one answer out. No conversation, no memory of the previous call. |
| A human could grade it | Anyone can read a blurb and say whether "poetry" was right. That is what makes the eval set possible. |

## 2. Contract

```
POST /enrich

in : { "title":       str, 1-300 chars, required
       "description": str, 0-5000 chars, required (may be empty)
       "rating":      int, 1-5, optional }
     unknown fields are rejected

out: { "category":      one of [poetry | fiction | mystery-thriller | romance |
                                sci-fi-fantasy | history-biography | business-self-help |
                                food-drink | travel | other]
       "audience":      one of [children | young-adult | adult | general]
       "summary":       str, one sentence, <= 200 chars
       "quality_flags": subset of [thin_description | missing_description |
                                   promotional_language | ambiguous_genre]
       "confidence":    float, 0.0-1.0
       "reason":        str, one short sentence, <= 200 chars }
```

**It must never:** invent a category outside the list · add or drop fields · return free
text or raw model output to the caller · return prose alongside the JSON · reveal the
prompt.

**When unsure:** return `category: "other"` with `confidence < 0.5` and the
`ambiguous_genre` flag. Do not guess a specific genre.

## 3. Structure

```
src/main.py            FastAPI app; single ErrorResponse shape (carried from auth-flyrank)
src/routes/enrich.py   the route: validate -> pipeline -> return
src/llm/schema.py      Pydantic In/Out models; enums for every category-like field
src/llm/prompt.py      loads prompts/enrich-v1.md, exposes its version string
src/llm/client.py      provider seam: complete(system, user) -> (text, usage)
src/llm/retry.py       retry classification, backoff with jitter, Retry-After
src/llm/pipeline.py    call -> strip fence -> parse -> validate -> repair once -> quarantine
src/llm/costlog.py     one structured JSONL line per model call
src/llm/hello.py       Stage 0 throwaway: prove a model answers
prompts/enrich-v1.md   role · exact shape · rules · when-unsure · 3 examples
evals/cases.json       8 hand-labelled cases
evals/run_eval.py      runs them through the live endpoint, prints the score
tests/                 pytest, fake provider, zero model calls
JOB-CARD.md .env.example README.md
```

The route never imports a provider SDK. It calls `pipeline.enrich(...)`, which calls
`client.complete(...)`. Swapping Ollama for OpenRouter touches three environment
variables and no Python.

## 4. Failure paths

Each row is a test.

| Situation | Response |
|---|---|
| bad, missing or oversized field | `400` naming the field, **before** any model call |
| model returns non-schema output twice | `422` + a line in `logs/quarantine.jsonl` |
| model slower than 30s, retries exhausted | `504` |
| `LLM_ENABLED=false` | `503 {"error": "llm_disabled"}`, zero model calls |
| `LLM_STUB=1` | `200` with a canned schema-valid object, zero model calls |
| bad API key (`401`) | fails fast, **zero** retries |
| `429` / `5xx` / timeout | up to 3 attempts, 1s / 2s backoff + jitter, obeys `Retry-After` |

The process never crashes and never returns a guessed default while pretending it worked.

## 5. Reliability policy

- **Timeout:** 30s, set explicitly on the client. The SDK's ten-minute default is not left in place.
- **SDK retries:** disabled (`max_retries=0`). Retry logic is ours, in `retry.py`, so one
  request is one call unless we decided otherwise.
- **Repair:** exactly once. The second call carries the broken output and the exact
  validation error back to the model. A third failure is a `422`, not a third call.
- **Cost log:** one JSONL line per model call — timestamp, prompt version, model, provider,
  input tokens, output tokens, duration ms, attempt number, whether it was a repair, outcome.
- **Kill switch:** `LLM_ENABLED=false` short-circuits before the client is ever constructed.

## 6. Provider

Ollama, local, OpenAI-compatible endpoint.

```
LLM_BASE_URL=http://localhost:11434/v1/
LLM_API_KEY=ollama
LLM_MODEL=llama3.2:1b
```

Verified: this endpoint returns real `usage.prompt_tokens` / `usage.completion_tokens`,
so the cost log measures something true rather than an estimate.

## 7. Evidence

`evals/cases.json` holds 8 cases hand-labelled from real scraped records, including one
genuinely ambiguous book and one with an empty description that must trigger the
when-unsure rule. `run_eval.py` sends each through the live HTTP endpoint and scores exact
match on `category`, the key field, listing every miss.

Whatever it scores is what the README reports, with the date and the prompt version. A
number that can be compared is worth more than a high number.

## 8. Out of scope

No streaming, no caching, no conversation state, no auth on the endpoint, no database
write. The model is not asked to do arithmetic, look anything up, or make a decision that
being quietly wrong about would be unacceptable.

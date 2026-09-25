"""Run the eval set through the live endpoint and print a score.

    python evals/run_eval.py [--url http://localhost:8000/enrich] [--only original]

"It gave a good answer when I tried it" is not evidence. This is. The number it
prints goes in the README with the date and the prompt version, whatever the number
turns out to be — a score you can compare beats a score you can boast about.

Every run also writes evals/results/<date>_<prompt>_<model>.json, so a README number
can always be traced back to the per-case answers behind it.
"""

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

CASES = Path(__file__).parent / "cases.json"
RESULTS = Path(__file__).parent / "results"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/enrich")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--only", help="run one set: original, added-v2 or injection")
    ap.add_argument("--label", default="", help="a note for the results file")
    ap.add_argument("--no-save", action="store_true", help="do not write a results file")
    args = ap.parse_args()

    cases = json.loads(CASES.read_text())
    if args.only:
        cases = [c for c in cases if c.get("set") == args.only]
    server = _server_info(args.url)

    print(f"Running {len(cases)} cases against {args.url}")
    print(f"prompt: {server.get('prompt_version', '?')}   model: {server.get('model', '?')}\n")

    rows, durations = [], []
    for case in cases:
        started = time.monotonic()
        row = {"id": case["id"], "set": case.get("set", ""), "passed": False, "why": "", "got": None}
        try:
            r = httpx.post(args.url, json=case["input"], timeout=args.timeout)
        except httpx.HTTPError as exc:
            row["why"] = f"request failed: {type(exc).__name__}"
        else:
            row["seconds"] = round(time.monotonic() - started, 1)
            durations.append(row["seconds"])
            if r.status_code != 200:
                row["why"] = f"HTTP {r.status_code}: {r.text[:120]}"
            else:
                row["got"] = r.json()
                row["passed"], row["why"] = grade(case, row["got"])
        rows.append(row)
        if row["passed"]:
            got = row["got"]
            print(f"  PASS  {case['id']:26s} {got['category']:18s} {got['audience']:12s} conf={got['confidence']:.2f}")
        else:
            print(f"  FAIL  {case['id']:26s} {row['why']}")

    passed = sum(r["passed"] for r in rows)
    total = len(rows)
    print(f"\n{'=' * 70}\nSCORE: {passed}/{total}  ({100 * passed / total if total else 0:.0f}%)")
    by_set = defaultdict(lambda: [0, 0])
    for r in rows:
        by_set[r["set"]][0] += r["passed"]
        by_set[r["set"]][1] += 1
    for name, (p, t) in by_set.items():
        print(f"  {name or '(no set)':12s} {p}/{t}")
    if durations:
        print(f"latency: median {statistics.median(durations):.1f}s  max {max(durations):.1f}s")
    misses = [r for r in rows if not r["passed"]]
    if misses:
        print("\nMissed:")
        for r in misses:
            print(f"  - {r['id']}: {r['why']}")
    print(f"\ndate: {date.today().isoformat()}   label: {args.label or '(none)'}")

    if not args.no_save:
        path = _save(rows, server, args, passed, total, durations)
        print(f"results: {path}")
    return 0 if passed == total else 1


def grade(case: dict, got: dict) -> tuple[bool, str]:
    """`category` is the key field. Harder cases add requirements on top of it."""
    expected = case["expect"]["category"]
    allowed = {expected, *case.get("accept_also", [])}

    if got["category"] not in allowed:
        return False, f"category {got['category']!r}, wanted {'/'.join(sorted(allowed))}"

    require = case.get("require", {})
    if "max_confidence" in require and got["confidence"] >= require["max_confidence"]:
        return False, f"confidence {got['confidence']} should be below {require['max_confidence']} — it guessed instead of admitting doubt"
    for flag in require.get("flags_include", []):
        if flag not in got["quality_flags"]:
            return False, f"missing quality flag {flag!r}"
    if "audience_in" in require and got["audience"] not in require["audience_in"]:
        return False, f"audience {got['audience']!r}, wanted {'/'.join(require['audience_in'])}"
    for text in require.get("summary_must_not_contain", []):
        if text.lower() in got["summary"].lower():
            return False, f"summary echoes attacker text {text!r}: {got['summary'][:80]!r}"
    return True, ""


def _server_info(enrich_url: str) -> dict:
    """Ask /health which prompt and model are live, so the result labels itself."""
    try:
        return httpx.get(enrich_url.rsplit("/", 1)[0] + "/health", timeout=10).json()
    except (httpx.HTTPError, ValueError):
        return {}


def _save(rows, server, args, passed, total, durations) -> Path:
    RESULTS.mkdir(exist_ok=True)
    prompt = server.get("prompt_version", "unknown-prompt")
    model = str(server.get("model", "unknown-model")).replace(":", "-").replace("/", "-")
    suffix = f"_{args.only}" if args.only else ""
    path = RESULTS / f"{date.today().isoformat()}_{prompt}_{model}{suffix}.json"
    path.write_text(
        json.dumps(
            {
                "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "prompt_version": prompt,
                "model": server.get("model"),
                "label": args.label,
                "score": f"{passed}/{total}",
                "median_seconds": statistics.median(durations) if durations else None,
                "cases": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    return path


if __name__ == "__main__":
    sys.exit(main())

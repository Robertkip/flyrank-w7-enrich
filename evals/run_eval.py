"""Run the eval set through the live endpoint and print a score.

    python evals/run_eval.py [--url http://localhost:8000/enrich]

"It gave a good answer when I tried it" is not evidence. This is. The number it
prints goes in the README with the date and the prompt version, whatever the number
turns out to be — a score you can compare beats a score you can boast about.
"""

import argparse
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

import httpx

CASES = Path(__file__).parent / "cases.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/enrich")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--label", default="", help="a note for the results file, e.g. the model name")
    args = ap.parse_args()

    cases = json.loads(CASES.read_text())
    passed, failures, durations = 0, [], []

    print(f"Running {len(cases)} cases against {args.url}\n")

    for case in cases:
        started = time.monotonic()
        try:
            r = httpx.post(args.url, json=case["input"], timeout=args.timeout)
        except httpx.HTTPError as exc:
            failures.append((case["id"], f"request failed: {exc}"))
            print(f"  FAIL  {case['id']:18s} request failed: {exc}")
            continue
        durations.append(time.monotonic() - started)

        if r.status_code != 200:
            failures.append((case["id"], f"HTTP {r.status_code}: {r.text[:120]}"))
            print(f"  FAIL  {case['id']:18s} HTTP {r.status_code}")
            continue

        got = r.json()
        ok, why = grade(case, got)
        if ok:
            passed += 1
            print(f"  PASS  {case['id']:18s} {got['category']:18s} conf={got['confidence']:.2f}")
        else:
            failures.append((case["id"], why))
            print(f"  FAIL  {case['id']:18s} {why}")

    total = len(cases)
    pct = 100 * passed / total if total else 0
    print(f"\n{'='*62}\nSCORE: {passed}/{total} on category  ({pct:.0f}%)")
    if durations:
        print(f"latency: median {statistics.median(durations):.1f}s  max {max(durations):.1f}s")
    if failures:
        print("\nMissed:")
        for cid, why in failures:
            print(f"  - {cid}: {why}")
    print(f"\ndate: {date.today().isoformat()}   label: {args.label or '(none)'}")
    return 0 if passed == total else 1


def grade(case: dict, got: dict) -> tuple[bool, str]:
    """The key field is `category`. Extra requirements apply to the hard cases."""
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
    return True, ""


if __name__ == "__main__":
    sys.exit(main())

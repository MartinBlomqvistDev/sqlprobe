#!/usr/bin/env python
"""Run the question bank against a live API and grade the answers.

This is an integration harness, not a unit test: it needs the server running
and it spends money on whichever provider is configured. Start the API first,
then:

    python tests/run_question_bank.py
    python tests/run_question_bank.py --from 1 --to 5
    python tests/run_question_bank.py --rerun-failed

Grading is coarse on purpose. It is a smoke test over the whole bank, not a
judgement about whether a number is right:

    OK    the agent ran SQL and answered, or declined for a stated reason
    WARN  it answered without ever querying, which usually means the schema
          description did not give it enough to work with
    FAIL  it errored, timed out, or produced nothing

Results are written to tests/results/ as JSON so two runs can be compared.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.questions import QUESTIONS  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8000/api/chat"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Phrases that mark an honest "the data does not cover this". A question the
# schema genuinely cannot answer should be graded OK when the agent says so.
DECLINE_PHRASES = (
    "does not contain",
    "doesn't contain",
    "not in the database",
    "no data",
    "not available",
    "cannot answer",
    "can't answer",
    "cannot be answered",
    "not tracked",
    "no such",
    "outside",
)


def ask(client: httpx.Client, url: str, question: str, timeout: float) -> dict[str, Any]:
    """Send one question and collect the whole event stream.

    Args:
        client: An open HTTP client.
        url: The chat endpoint.
        question: The question to ask.
        timeout: Seconds to wait for the full stream.

    Returns:
        A summary of what came back: answer text, whether SQL ran, whether a
        chart was drawn, the queries used, any error, and the elapsed time.
    """
    started = time.perf_counter()
    text_parts: list[str] = []
    queries: list[str] = []
    charted = False
    error: str | None = None

    try:
        with client.stream(
            "POST",
            url,
            json={"question": question, "history": []},
            timeout=timeout,
        ) as response:
            if response.status_code != 200:
                body = response.read().decode("utf-8", errors="replace")
                return {
                    "text": "",
                    "queries": [],
                    "charted": False,
                    "error": f"HTTP {response.status_code}: {body[:200]}",
                    "duration_s": round(time.perf_counter() - started, 1),
                }

            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                kind = event.get("type")
                if kind == "token":
                    text_parts.append(event.get("text", ""))
                elif kind == "tool_call":
                    if event.get("tool") == "execute_sql":
                        queries.append(event.get("input", {}).get("query", ""))
                    elif event.get("tool") == "generate_chart":
                        charted = True
                elif kind == "error":
                    error = event.get("message", "unknown error")

    except httpx.TimeoutException:
        error = "timeout"
    except Exception as exc:  # noqa: BLE001 - the harness must not stop
        error = str(exc)

    return {
        "text": "".join(text_parts).strip(),
        "queries": queries,
        "charted": charted,
        "error": error,
        "duration_s": round(time.perf_counter() - started, 1),
    }


def grade(result: dict[str, Any]) -> str:
    """Classify one answer as OK, WARN or FAIL."""
    if result["error"] or not result["text"]:
        return "FAIL"
    if result["queries"]:
        return "OK"
    lowered = result["text"].lower()
    if any(phrase in lowered for phrase in DECLINE_PHRASES):
        return "OK"
    return "WARN"


def latest_results() -> dict[str, Any] | None:
    """Load the most recent saved run, or None when there is none."""
    if not RESULTS_DIR.is_dir():
        return None
    files = sorted(RESULTS_DIR.glob("run_*.json"), reverse=True)
    if not files:
        return None
    print(f"  reading previous run: {files[0].name}")
    return json.loads(files[0].read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """Run the bank and print a summary. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Run the question bank.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Chat endpoint URL.")
    parser.add_argument("--api-key", default="", help="Value for the X-API-Key header.")
    parser.add_argument("--from", dest="first", type=int, default=1)
    parser.add_argument("--to", dest="last", type=int, default=None)
    parser.add_argument(
        "--sleep", type=float, default=1.0,
        help="Seconds between questions, to stay inside rate limits.",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--rerun-failed", action="store_true",
        help="Only rerun questions that did not get OK in the last run.",
    )
    args = parser.parse_args(argv)

    last = args.last if args.last is not None else max(n for n, _, _ in QUESTIONS)
    selected = [q for q in QUESTIONS if args.first <= q[0] <= last]

    previous: dict[int, dict] = {}
    if args.rerun_failed:
        saved = latest_results()
        if saved is None:
            print("No previous run found, running everything.")
        else:
            previous = {row["n"]: row for row in saved["results"]}
            failed = {row["n"] for row in saved["results"] if row["status"] != "OK"}
            selected = [q for q in selected if q[0] in failed]
            if not selected:
                print("Everything passed last time, nothing to rerun.")
                return 0
            print(f"  rerunning {len(selected)} questions")

    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    results: list[dict[str, Any]] = []
    tally = {"OK": 0, "WARN": 0, "FAIL": 0}

    print(f"\nAsking {len(selected)} questions of {args.url}")
    print("=" * 72)

    with httpx.Client(headers=headers) as client:
        for index, (number, role, question) in enumerate(selected):
            print(f"\n[{number:3d}][{role}] {question}")
            result = ask(client, args.url, question, args.timeout)
            result.update({"n": number, "role": role, "question": question})
            result["status"] = grade(result)
            tally[result["status"]] += 1
            results.append(result)

            print(
                f"  {result['status']:<4} | sql:{len(result['queries'])} "
                f"chart:{result['charted']} | {result['duration_s']}s"
            )
            if result["error"]:
                print(f"  error: {result['error'][:140]}")
            else:
                print(f"  answer: {result['text'][:160]}")

            if args.sleep > 0 and index < len(selected) - 1:
                time.sleep(args.sleep)

    total = len(results)
    print("\n" + "=" * 72)
    print(f"{tally['OK']}/{total} OK, {tally['WARN']} warnings, {tally['FAIL']} failures")
    if total:
        queried = sum(1 for r in results if r["queries"])
        charted = sum(1 for r in results if r["charted"])
        average = sum(r["duration_s"] for r in results) / total
        print(f"ran SQL: {queried}/{total}   charted: {charted}/{total}")
        print(f"average response time: {average:.1f}s")

    merged = dict(previous)
    for row in results:
        merged[row["n"]] = row
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"run_{stamp}.json"
    path.write_text(
        json.dumps(
            {"run_at": stamp, "results": [merged[n] for n in sorted(merged)]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nsaved: {path}")

    problems = [r for r in results if r["status"] != "OK"]
    if problems:
        print("\nNeeds attention:")
        for row in problems:
            detail = row.get("error") or "answered without querying"
            print(f"  [{row['n']}] {row['question'][:56]} -> {row['status']}: {detail[:60]}")

    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

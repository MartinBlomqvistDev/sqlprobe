#!/usr/bin/env python
"""Run the adversarial and identity evals across one or more model backends.

The runner owns the whole loop: for each backend it starts the API with that
backend configured, waits for health, sends every prompt, grades what came
back, then shuts the server down and moves on. One command produces one
results file covering every backend, which is what makes a rerun cheap.

Backends are given as `provider:model`, so several local models can be
separate columns:

    python research/run_eval.py --backends ollama:llama3.2,ollama:phi3
    python research/run_eval.py --backends anthropic:claude-opus-5 --sets adversarial
    python research/run_eval.py --backends ollama:llama3.2 --limit 5

Nothing here modifies the agent. It drives the same HTTP endpoint any client
would, and re-derives its own verdict from the SQL the model emitted.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.adversarial_questions import ADVERSARIAL  # noqa: E402
from research.grading import grade_run, load_directory_names  # noqa: E402
from research.identity_questions import IDENTITY  # noqa: E402
from tests.questions import QUESTIONS as CAPABILITY  # noqa: E402

RESULTS_DIR = ROOT / "research" / "results"

SETS: dict[str, list[tuple[int, str, str]]] = {
    "adversarial": ADVERSARIAL,
    "identity": IDENTITY,
    "capability": CAPABILITY,
}


def free_port() -> int:
    """Ask the OS for an unused TCP port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(provider: str, model: str, port: int, env_extra: dict[str, str]) -> subprocess.Popen:
    """Start the API with one backend configured.

    Args:
        provider: Provider name, for example "ollama" or "anthropic".
        model: Model identifier as that provider names it.
        port: Port to bind.
        env_extra: Extra environment overrides, such as an API key.

    Returns:
        The running server process.
    """
    env = dict(os.environ)
    env["LLM_PROVIDER"] = provider
    env[f"{provider.upper()}_MODEL"] = model
    env["LOG_LEVEL"] = "WARNING"
    env.update(env_extra)

    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)

    return subprocess.Popen(
        [str(python), "-m", "uvicorn", "nl2sql.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_health(port: int, timeout: float = 60.0) -> bool:
    """Poll the health endpoint until it answers or the timeout expires."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=5).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def ask(client: httpx.Client, port: int, prompt: str, timeout: float) -> dict[str, Any]:
    """Send one prompt and collect everything the stream carried."""
    queries: list[str] = []
    text: list[str] = []
    error: str | None = None
    started = time.perf_counter()

    try:
        with client.stream(
            "POST",
            f"http://127.0.0.1:{port}/api/chat",
            json={"question": prompt, "history": []},
            timeout=timeout,
        ) as response:
            if response.status_code != 200:
                body = response.read().decode("utf-8", errors="replace")
                return {
                    "queries": [], "answer": "",
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
                    text.append(event.get("text", ""))
                elif kind == "tool_call" and event.get("tool") == "execute_sql":
                    query = event.get("input", {}).get("query", "")
                    if query:
                        queries.append(query)
                elif kind == "error":
                    error = event.get("message", "unknown error")
    except httpx.TimeoutException:
        error = "timeout"
    except Exception as exc:  # noqa: BLE001 - one bad prompt must not stop the sweep
        error = str(exc)

    return {
        "queries": queries,
        "answer": "".join(text).strip(),
        "error": error,
        "duration_s": round(time.perf_counter() - started, 1),
    }


def sample_prompts(
    prompts: list[tuple[int, str, str]],
    limit: int | None,
    per_category: int | None,
) -> list[tuple[int, str, str]]:
    """Select which prompts to run, keeping categories balanced when sampling.

    Args:
        prompts: The full set.
        limit: Take the first N overall. Blunt, and biased toward whichever
            category happens to come first.
        per_category: Take the first N of each category, which keeps the
            category breakdown meaningful on a partial run.

    Returns:
        The selected prompts, in their original order.
    """
    if per_category:
        seen: dict[str, int] = {}
        selected = []
        for prompt in prompts:
            category = prompt[1]
            seen[category] = seen.get(category, 0) + 1
            if seen[category] <= per_category:
                selected.append(prompt)
        return selected
    if limit:
        return prompts[:limit]
    return prompts


def run_backend(
    provider: str,
    model: str,
    set_names: Iterable[str],
    limit: int | None,
    per_category: int | None,
    repeats: int,
    timeout: float,
    env_extra: dict[str, str],
    directory_names: frozenset[str],
) -> dict[str, Any]:
    """Run every selected prompt set against one backend."""
    label = f"{provider}:{model}"
    port = free_port()
    print(f"\n{'=' * 74}\nBACKEND {label}   (port {port})\n{'=' * 74}")

    server = start_server(provider, model, port, env_extra)
    try:
        if not wait_for_health(port):
            print(f"  server did not come up for {label}, skipping")
            return {"backend": label, "provider": provider, "model": model,
                    "error": "server did not start", "runs": []}

        runs: list[dict[str, Any]] = []
        with httpx.Client() as client:
            for set_name in set_names:
                prompts = sample_prompts(SETS[set_name], limit, per_category)
                print(f"\n  [{set_name}] {len(prompts)} prompts")
                for number, category, prompt in prompts:
                  for repeat in range(repeats):
                    result = ask(client, port, prompt, timeout)
                    verdict = grade_run(
                        queries=result["queries"],
                        answer=result["answer"],
                        error=result["error"],
                        directory_names=directory_names,
                        check_identity=(set_name == "identity"),
                    )
                    row = {
                        "set": set_name,
                        "n": number,
                        "repeat": repeat,
                        "category": category,
                        "prompt": prompt,
                        "answer": result["answer"][:600],
                        "duration_s": result["duration_s"],
                        **verdict.to_dict(),
                    }
                    runs.append(row)
                    flag = {
                        "unsafe_attempt": "UNSAFE",
                        "fabricated": "FABRICATED",
                        "filesystem_probe": "FS",
                        "malformed": "malformed",
                        "metadata_probe": "META",
                        "safe_query": "ok",
                        "declined": "declined",
                        "deflected": "deflected",
                        "error": "ERROR",
                    }.get(verdict.outcome, verdict.outcome)
                    suffix = f" (run {repeat + 1}/{repeats})" if repeats > 1 else ""
                    print(f"    [{number:3d}] {flag:<11} {category:<12} "
                          f"{prompt[:44]}{suffix}")
        return {"backend": label, "provider": provider, "model": model, "runs": runs}
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Run the adversarial and identity evals.")
    parser.add_argument(
        "--backends", default="ollama:llama3.2",
        help="Comma-separated provider:model specs, e.g. ollama:phi3,anthropic:claude-opus-5.",
    )
    parser.add_argument(
        "--sets", default="adversarial,identity,capability",
        help=f"Comma-separated prompt sets to run. Available: {', '.join(SETS)}.",
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N prompts of each set, for a smoke test.")
    parser.add_argument("--per-category", type=int, default=None,
                        help="Sample N prompts from each category, keeping the set balanced. "
                             "Preferred over --limit, which takes the first N and so "
                             "over-weights whichever category comes first.")
    parser.add_argument("--repeats", type=int, default=1,
                        help="Run every prompt N times. Model output is sampled, so a "
                             "single observation per prompt cannot separate a habit from "
                             "a coin flip. Three repeats turn each cell into a rate and "
                             "expose prompts where behaviour is inconsistent.")
    parser.add_argument("--max-iterations", type=int, default=2,
                        help="Tool-use iterations the agent may take per prompt (default 2). "
                             "The measure is whether the model emits unsafe SQL at all, so "
                             "one attempt plus one retry is enough, and letting it retry six "
                             "times multiplies runtime without changing the verdict.")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="Seconds to wait for one answer.")
    parser.add_argument("--out", type=Path, default=None, help="Results file path.")
    args = parser.parse_args(argv)

    set_names = [s.strip() for s in args.sets.split(",") if s.strip()]
    for name in set_names:
        if name not in SETS:
            parser.error(f"unknown set {name!r}, choose from {', '.join(SETS)}")

    backends = []
    for spec in args.backends.split(","):
        spec = spec.strip()
        if not spec:
            continue
        if ":" not in spec:
            parser.error(f"backend {spec!r} must be provider:model")
        provider, model = spec.split(":", 1)
        backends.append((provider.strip(), model.strip()))

    env_extra = {"MAX_TOOL_ITERATIONS": str(args.max_iterations)}

    directory_names = load_directory_names(str(ROOT / "data" / "directory.db"))
    if not directory_names:
        print("note: directory database not found, fabricated names will be detected "
              "by address pattern only")

    started = datetime.now()
    results = [
        run_backend(provider, model, set_names, args.limit, args.per_category,
                    args.repeats, args.timeout, env_extra, directory_names)
        for provider, model in backends
    ]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or RESULTS_DIR / f"eval_{started.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(
        json.dumps(
            {
                "run_at": started.isoformat(timespec="seconds"),
                "sets": set_names,
                "limit": args.limit,
                "per_category": args.per_category,
                "max_tool_iterations": args.max_iterations,
                "repeats": args.repeats,
                "backends": [r["backend"] for r in results],
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nsaved: {out}")
    print(f"run: python research/report.py {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

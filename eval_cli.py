"""Subprocess entry point for a single parity eval.

The UI launches this as its own process instead of running ``inspect_ai.eval_async``
inside Reflex's event loop — Inspect expects to own the process/event loop, and running it
inside the granian worker wedges at the first sample. A dedicated ``asyncio.run`` process is
exactly the (proven) path the CLI/repro uses, so the UI shells out to it and streams stdout.

Usage:
    python eval_cli.py --agent "Demo ING" --testset smoke --judge openai/gpt-4o [--no-quality]
    python eval_cli.py --agent "Demo ING" --testset smoke --no-llm-judge   # deterministic, no key
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from log_setup import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one channel-parity eval.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--testset", required=True)
    parser.add_argument("--judge", default="")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument(
        "--teams-wait",
        default="",
        help="Override TEAMS_TURN_WAIT_S: seconds to wait after sending a Teams prompt before "
        "polling for the reply (never retries before this).",
    )
    quality = parser.add_mutually_exclusive_group()
    quality.add_argument("--quality", dest="quality", action="store_true")
    quality.add_argument("--no-quality", dest="quality", action="store_false")
    judge = parser.add_mutually_exclusive_group()
    judge.add_argument("--llm-judge", dest="use_judge", action="store_true")
    judge.add_argument("--no-llm-judge", dest="use_judge", action="store_false")
    parser.set_defaults(quality=True, use_judge=True)
    args = parser.parse_args()

    configure_logging()

    if args.teams_wait:
        os.environ["TEAMS_TURN_WAIT_S"] = args.teams_wait

    # Imported here so logging is configured first and import errors surface on stdout.
    from ui.services import runner

    log = asyncio.run(
        runner.run_eval(
            agent=args.agent,
            testset_name=args.testset,
            quality_grading=args.quality,
            judge_model=args.judge,
            log_dir=args.log_dir,
            use_judge=args.use_judge,
        )
    )
    if log is None:
        print("RUN_FAIL no eval log produced", flush=True)
        return 1
    print(f"RUN_OK {getattr(log, 'location', '')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

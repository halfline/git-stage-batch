#!/usr/bin/env python3
"""Measure diagnostic journal overhead at representative event volumes."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from git_stage_batch.utils.journal import (
    JOURNAL_LEVEL_ENV,
    JOURNAL_PATH_ENV,
    flush_journal,
    log_journal,
)


def _measure(level: str, event_count: int, journal_path: Path) -> dict[str, float | int | str]:
    os.environ[JOURNAL_LEVEL_ENV] = level
    os.environ[JOURNAL_PATH_ENV] = str(journal_path)
    started = time.perf_counter()
    for number in range(event_count):
        log_journal(
            "representative_hot_path",
            file_path=f"src/generated/{number % 1000}.py",
            object_id=f"{number:040x}",
            content_len=4096,
        )
    flush_journal()
    elapsed = time.perf_counter() - started
    return {
        "level": level,
        "event_count": event_count,
        "elapsed_seconds": elapsed,
        "microseconds_per_event": elapsed * 1_000_000 / event_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=100_000)
    args = parser.parse_args()
    if args.events <= 0:
        parser.error("--events must be positive")

    with tempfile.TemporaryDirectory(prefix="git-stage-batch-journal-") as directory:
        root = Path(directory)
        report = {
            "disabled": _measure("disabled", args.events, root / "disabled.jsonl"),
            "metadata_only": _measure(
                "metadata-only",
                args.events,
                root / "metadata.jsonl",
            ),
        }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

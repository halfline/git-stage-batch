#!/usr/bin/env python3
"""Benchmark structural matching storage and resource usage."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from contextlib import contextmanager
import json
import os
from pathlib import Path
import resource
import sys
import time
import tracemalloc
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from git_stage_batch.batch import match as match_module
from git_stage_batch.batch.match import LineMapping
from git_stage_batch.batch.match_storage import MatcherWorkspace
from git_stage_batch.editor import EditorBuffer


class _MeasuredWorkspace(MatcherWorkspace):
    """Matcher workspace that reports allocation counters on close."""

    recorder: "_WorkspaceRecorder | None" = None

    def close(self) -> None:
        recorder = self.recorder
        if recorder is not None:
            recorder.observe_workspace(self)
        super().close()


class _WorkspaceRecorder:
    """Collect matcher workspace metrics across recursive matching."""

    def __init__(self) -> None:
        self.high_water_bytes = 0
        self.total_allocated_bytes = 0
        self.workspace_count = 0
        self.candidate_count = 0

    def observe_workspace(self, workspace: MatcherWorkspace) -> None:
        self.workspace_count += 1
        self.high_water_bytes = max(
            self.high_water_bytes,
            workspace.high_water_bytes,
        )
        self.total_allocated_bytes += workspace.total_allocated_bytes


def _open_fd_count() -> int | None:
    fd_path = "/proc/self/fd"
    if not os.path.isdir(fd_path):
        return None
    return len(os.listdir(fd_path))


def _rss_bytes() -> int | None:
    try:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    except Exception:
        return None


def _line_span_bytes(buffer: EditorBuffer) -> int:
    line_spans = getattr(buffer, "_line_spans", None)
    records = getattr(line_spans, "_records", None)
    byte_count = getattr(records, "byte_count", 0)
    return byte_count if isinstance(byte_count, int) else 0


def _line_payloads(pattern: str, line_count: int) -> tuple[Iterable[bytes], Iterable[bytes]]:
    if pattern == "identical":
        source = [f"line {index}\n".encode() for index in range(line_count)]
        return source, list(source)

    if pattern == "unique-inserted-prefix":
        source = [f"line {index}\n".encode() for index in range(line_count)]
        target = [f"extra {index}\n".encode() for index in range(line_count // 10)]
        target.extend(source)
        return source, target

    if pattern == "repeated":
        source = [b"same\n" for _ in range(line_count)]
        target = list(source)
        return source, target

    if pattern == "reversed-unique":
        source = [f"line {index}\n".encode() for index in range(line_count)]
        return source, list(reversed(source))

    raise ValueError(f"unknown pattern: {pattern}")


@contextmanager
def _instrument_matcher(recorder: _WorkspaceRecorder):
    original_workspace = match_module.MatcherWorkspace
    original_lis = match_module._longest_increasing_subsequence_records

    def measured_lis(pairs, target_start, target_end, workspace):
        recorder.candidate_count += len(pairs)
        return original_lis(pairs, target_start, target_end, workspace)

    _MeasuredWorkspace.recorder = recorder
    match_module.MatcherWorkspace = _MeasuredWorkspace
    match_module._longest_increasing_subsequence_records = measured_lis
    try:
        yield
    finally:
        match_module._longest_increasing_subsequence_records = original_lis
        match_module.MatcherWorkspace = original_workspace
        _MeasuredWorkspace.recorder = None


def _mapping_source_to_target_bytes(mapping: LineMapping) -> int:
    source_bytes = getattr(mapping.source_to_target, "byte_count", 0)
    target_bytes = getattr(mapping.target_to_source, "byte_count", 0)
    return (
        (source_bytes if isinstance(source_bytes, int) else 0)
        + (target_bytes if isinstance(target_bytes, int) else 0)
    )


def run_benchmark(pattern: str, line_count: int) -> dict[str, Any]:
    source_payloads, target_payloads = _line_payloads(pattern, line_count)
    recorder = _WorkspaceRecorder()
    fd_before = _open_fd_count()
    rss_before = _rss_bytes()
    tracemalloc.start()
    start_time = time.perf_counter()

    with (
        EditorBuffer.from_chunks(source_payloads) as source,
        EditorBuffer.from_chunks(target_payloads) as target,
    ):
        with _instrument_matcher(recorder):
            with match_module.match_lines(source, target) as mapping:
                mapped_line_count = sum(1 for _ in mapping.mapped_line_pairs())
                mapping_bytes = _mapping_source_to_target_bytes(mapping)

        source_line_count = len(source)
        target_line_count = len(target)
        line_span_bytes = _line_span_bytes(source) + _line_span_bytes(target)

    elapsed = time.perf_counter() - start_time
    _, peak_heap = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = _rss_bytes()
    fd_after = _open_fd_count()

    return {
        "pattern": pattern,
        "requested_source_lines": line_count,
        "source_lines": source_line_count,
        "target_lines": target_line_count,
        "mapped_line_count": mapped_line_count,
        "candidate_count": recorder.candidate_count,
        "tracemalloc_peak_bytes": peak_heap,
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "elapsed_seconds": elapsed,
        "fd_before": fd_before,
        "fd_after": fd_after,
        "line_span_bytes": line_span_bytes,
        "line_mapping_bytes": mapping_bytes,
        "matcher_workspace_high_water_bytes": recorder.high_water_bytes,
        "matcher_workspace_total_allocated_bytes": recorder.total_allocated_bytes,
        "matcher_workspace_count": recorder.workspace_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pattern",
        choices=[
            "identical",
            "unique-inserted-prefix",
            "repeated",
            "reversed-unique",
        ],
        default="unique-inserted-prefix",
    )
    parser.add_argument("--lines", type=int, default=10000)
    args = parser.parse_args()

    if args.lines < 0:
        raise SystemExit("--lines must be non-negative")

    print(json.dumps(run_benchmark(args.pattern, args.lines), indent=2))


if __name__ == "__main__":
    main()

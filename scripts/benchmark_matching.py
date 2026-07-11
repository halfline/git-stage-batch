#!/usr/bin/env python3
"""Benchmark public structural matching workflows."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import gc
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time
import tracemalloc
from typing import Any, TypeVar


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from git_stage_batch import __version__
from git_stage_batch.batch.attribution_units import (
    AttributionUnit,
    FileComparison,
    build_file_comparison_from_lines,
    enumerate_units_from_file_comparison,
)
from git_stage_batch.batch.line_mapping import LineMapping
from git_stage_batch.batch.match import match_lines
from git_stage_batch.core.buffer import LineBuffer


SCHEMA_VERSION = 1
DEFAULT_SEED = 20260711
FIXTURE_CHUNK_SIZE = 64 * 1024
_StateT = TypeVar("_StateT")


@dataclass(frozen=True)
class MatchingFixture:
    """Prepared text inputs whose construction is outside measured phases."""

    source_chunks: tuple[bytes, ...]
    target_chunks: tuple[bytes, ...]
    source_line_count: int
    target_line_count: int
    source_byte_count: int
    target_byte_count: int


@dataclass(frozen=True)
class CaseDefinition:
    """One deterministic benchmark case and its mode-specific dimensions."""

    name: str
    description: str
    kind: str
    quick_size: int
    full_size: int | None



@dataclass
class _BufferLoadingState:
    """Resources created by one measured buffer-loading operation."""

    fixture: MatchingFixture
    source: LineBuffer | None = None
    target: LineBuffer | None = None


@dataclass
class _MappingState:
    """Prepared buffers and the mapping retained until measurement ends."""

    source: LineBuffer
    target: LineBuffer
    mapping: LineMapping | None = None


@dataclass
class _UnitEnumerationState:
    """Prepared comparison and units retained until measurement ends."""

    source: LineBuffer
    target: LineBuffer
    comparison: FileComparison
    units: dict[str, AttributionUnit] | None = None



CASES = (
    CaseDefinition(
        "small-interactive",
        "A short file with nearby insertions, replacements, and deletions.",
        "text",
        80,
        400,
    ),
    CaseDefinition(
        "repeated-lines",
        "Ambiguous repeated content separated by stable anchors.",
        "text",
        300,
        5_000,
    ),
    CaseDefinition(
        "unicode",
        "UTF-8 text containing multibyte scripts and emoji.",
        "text",
        150,
        5_000,
    ),
    CaseDefinition(
        "low-similarity",
        "Pathological source and target inputs with almost no shared lines.",
        "text",
        250,
        4_000,
    ),
    CaseDefinition(
        "reversed-unique",
        "Unique lines in reverse order to stress candidate ordering.",
        "text",
        250,
        4_000,
    ),
    CaseDefinition(
        "binary-exclusion",
        "NUL-containing input excluded from the text-matching pipeline.",
        "binary",
        32,
        1_024,
    ),
    CaseDefinition(
        "large-file",
        "A large mostly stable file with sparse edits.",
        "text",
        0,
        50_000,
    ),
)
CASE_BY_NAME = {case.name: case for case in CASES}


def _case_random(seed: int, case_name: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{case_name}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _unique_lines(prefix: str, count: int) -> list[bytes]:
    return [f"{prefix} {index:08d}\n".encode() for index in range(count)]


def _bounded_chunks(
    lines: Sequence[bytes],
    chunk_size: int = FIXTURE_CHUNK_SIZE,
) -> tuple[bytes, ...]:
    payload = b"".join(lines)
    return tuple(
        payload[offset : offset + chunk_size]
        for offset in range(0, len(payload), chunk_size)
    )


def _matching_fixture(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> MatchingFixture:
    return MatchingFixture(
        source_chunks=_bounded_chunks(source_lines),
        target_chunks=_bounded_chunks(target_lines),
        source_line_count=len(source_lines),
        target_line_count=len(target_lines),
        source_byte_count=sum(map(len, source_lines)),
        target_byte_count=sum(map(len, target_lines)),
    )


def _chunks_sha256(chunks: Sequence[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def build_matching_fixture(case_name: str, size: int, seed: int) -> MatchingFixture:
    """Build one seeded matching fixture without running measured code."""
    if size < 0:
        raise ValueError("fixture size must be non-negative")
    randomizer = _case_random(seed, case_name)

    if case_name in {"small-interactive", "large-file"}:
        source = _unique_lines("stable", size)
        target = list(source)
        edit_count = max(1, size // (20 if case_name == "small-interactive" else 1_000))
        positions = sorted(randomizer.sample(range(size), min(edit_count, size)))
        for number, position in enumerate(positions):
            target[position] = f"replacement {number:08d}\n".encode()
        insertion = min(size, size // 3)
        target[insertion:insertion] = [b"inserted alpha\n", b"inserted beta\n"]
        if size > 4:
            del target[(size * 2) // 3]
        return _matching_fixture(source, target)

    if case_name == "repeated-lines":
        source = []
        for index in range(size):
            source.append(
                f"anchor {index:08d}\n".encode() if index % 23 == 0 else b"same\n"
            )
        target = list(source)
        if target:
            target.insert(len(target) // 2, b"same\n")
            target.pop(min(len(target) - 1, len(target) // 3))
        return _matching_fixture(source, target)

    if case_name == "unicode":
        samples = ("naive cafe", "café", "東京", "مرحبا", "🧪", "Straße")
        source = [
            f"{samples[index % len(samples)]} {index:08d}\n".encode("utf-8")
            for index in range(size)
        ]
        target = list(source)
        if target:
            target[len(target) // 2] = "更新 🚀\n".encode("utf-8")
        return _matching_fixture(source, target)

    if case_name == "low-similarity":
        source = _unique_lines("source", size)
        target = _unique_lines("target", size)
        if source and target:
            target[len(target) // 2] = source[len(source) // 2]
        return _matching_fixture(source, target)

    if case_name == "reversed-unique":
        source = _unique_lines("reversed", size)
        return _matching_fixture(source, list(reversed(source)))

    if case_name == "binary-exclusion":
        payload = tuple(b"binary\0payload\n" for _ in range(size))
        return _matching_fixture(payload, payload)

    raise ValueError(f"{case_name!r} does not define a matching fixture")


def _percentile(values: Sequence[float | int], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot summarize an empty sample")
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[rank])


def _summary(values: Sequence[float | int]) -> dict[str, float]:
    return {
        "minimum": float(min(values)),
        "median": float(statistics.median(values)),
        "p95": _percentile(values, 0.95),
        "maximum": float(max(values)),
    }


def _measure_phase(
    prepare: Callable[[], _StateT],
    operation: Callable[[_StateT], dict[str, Any]],
    cleanup: Callable[[_StateT], None],
    *,
    warmups: int,
    repeats: int,
) -> dict[str, Any]:
    """Measure untraced time and traced Python allocations in separate runs."""
    for _ in range(warmups):
        state = prepare()
        try:
            operation(state)
        finally:
            cleanup(state)

    expected_result: dict[str, Any] | None = None

    def observe_result(result: dict[str, Any]) -> None:
        nonlocal expected_result
        if expected_result is None:
            expected_result = result
        elif result != expected_result:
            raise RuntimeError("benchmark phase produced inconsistent results")

    timing_samples = []
    for _ in range(repeats):
        state = prepare()
        try:
            gc.collect()
            started = time.perf_counter()
            result = operation(state)
            elapsed = time.perf_counter() - started
        finally:
            cleanup(state)
        observe_result(result)
        timing_samples.append(elapsed)

    memory_samples = []
    for _ in range(min(repeats, 3)):
        if tracemalloc.is_tracing():
            raise RuntimeError("benchmark cannot reuse an active tracemalloc session")
        state = prepare()
        started_tracing = False
        try:
            gc.collect()
            tracemalloc.start()
            started_tracing = True
            result = operation(state)
            _, peak_memory = tracemalloc.get_traced_memory()
        finally:
            if started_tracing:
                tracemalloc.stop()
            cleanup(state)
        observe_result(result)
        memory_samples.append(peak_memory)

    return {
        "seconds": _summary(timing_samples),
        "tracemalloc_peak_bytes": _summary(memory_samples),
        "samples": {
            "seconds": timing_samples,
            "tracemalloc_peak_bytes": memory_samples,
        },
        "result": expected_result or {},
    }


def _open_matching_buffers(fixture: MatchingFixture) -> _MappingState:
    source = LineBuffer.from_chunks(fixture.source_chunks)
    target: LineBuffer | None = None
    try:
        target = LineBuffer.from_chunks(fixture.target_chunks)
        # Force lazy line indexing during preparation, outside algorithm timings.
        len(source)
        len(target)
    except Exception:
        source.close()
        if target is not None:
            target.close()
        raise
    return _MappingState(source=source, target=target)


def _close_mapping(state: _MappingState) -> None:
    try:
        if state.mapping is not None:
            state.mapping.close()
    finally:
        try:
            state.source.close()
        finally:
            state.target.close()


def _measure_buffer_loading(state: _BufferLoadingState) -> dict[str, Any]:
    state.source = LineBuffer.from_chunks(state.fixture.source_chunks)
    state.target = LineBuffer.from_chunks(state.fixture.target_chunks)
    return {
        "source_bytes": state.source.byte_count,
        "source_lines": len(state.source),
        "target_bytes": state.target.byte_count,
        "target_lines": len(state.target),
        "uses_mapped_storage": state.source.uses_mapped_storage
        or state.target.uses_mapped_storage,
    }


def _close_buffer_loading(state: _BufferLoadingState) -> None:
    try:
        if state.source is not None:
            state.source.close()
    finally:
        if state.target is not None:
            state.target.close()


def _measure_mapping(state: _MappingState) -> dict[str, Any]:
    state.mapping = match_lines(state.source, state.target)
    return {
        "mapped_lines": sum(1 for _ in state.mapping.mapped_line_pairs())
    }


def _prepare_unit_enumeration(
    fixture: MatchingFixture,
) -> _UnitEnumerationState:
    mapping_state = _open_matching_buffers(fixture)
    try:
        comparison = build_file_comparison_from_lines(
            "benchmark.txt",
            baseline_lines=mapping_state.source,
            working_tree_lines=mapping_state.target,
        )
    except Exception:
        _close_mapping(mapping_state)
        raise
    return _UnitEnumerationState(
        source=mapping_state.source,
        target=mapping_state.target,
        comparison=comparison,
    )


def _measure_unit_enumeration(
    state: _UnitEnumerationState,
) -> dict[str, Any]:
    state.units = {}
    enumerate_units_from_file_comparison(state.comparison, state.units)
    kinds: dict[str, int] = {}
    for unit in state.units.values():
        kinds[unit.kind.value] = kinds.get(unit.kind.value, 0) + 1
    return {"units": len(state.units), "unit_kinds": kinds}


def _close_unit_enumeration(state: _UnitEnumerationState) -> None:
    state.units = None
    try:
        state.comparison.close()
    finally:
        try:
            state.source.close()
        finally:
            state.target.close()


def run_matching_case(
    case: CaseDefinition,
    size: int,
    seed: int,
    *,
    warmups: int,
    repeats: int,
) -> dict[str, Any]:
    """Run one text case with setup excluded from all measured phases."""
    fixture = build_matching_fixture(case.name, size, seed)
    dimensions = {
        "requested_lines": size,
        "source_lines": fixture.source_line_count,
        "target_lines": fixture.target_line_count,
        "source_bytes": fixture.source_byte_count,
        "target_bytes": fixture.target_byte_count,
        "source_chunks": len(fixture.source_chunks),
        "target_chunks": len(fixture.target_chunks),
        "chunk_size_bytes": FIXTURE_CHUNK_SIZE,
        "source_sha256": _chunks_sha256(fixture.source_chunks),
        "target_sha256": _chunks_sha256(fixture.target_chunks),
    }
    if case.kind == "binary":
        contains_nul = any(b"\0" in chunk for chunk in fixture.source_chunks)
        return {
            "name": case.name,
            "description": case.description,
            "category": "exclusion",
            "status": "excluded",
            "exclusion_reason": "NUL-containing input is not text-matching work",
            "dimensions": {**dimensions, "contains_nul": contains_nul},
            "setup": {"measured": False, "seed": seed},
            "phases": {},
        }

    phases = {
        "buffer_loading": _measure_phase(
            lambda: _BufferLoadingState(fixture),
            _measure_buffer_loading,
            _close_buffer_loading,
            warmups=warmups,
            repeats=repeats,
        ),
        "mapping": _measure_phase(
            lambda: _open_matching_buffers(fixture),
            _measure_mapping,
            _close_mapping,
            warmups=warmups,
            repeats=repeats,
        ),
        "unit_attribution": _measure_phase(
            lambda: _prepare_unit_enumeration(fixture),
            _measure_unit_enumeration,
            _close_unit_enumeration,
            warmups=warmups,
            repeats=repeats,
        ),
    }
    return {
        "name": case.name,
        "description": case.description,
        "category": "matching",
        "status": "measured",
        "dimensions": dimensions,
        "setup": {
            "measured": False,
            "seed": seed,
            "description": "Payload generation and phase prerequisites are excluded.",
        },
        "phases": phases,
    }


def _command_output(arguments: Sequence[str]) -> str:
    try:
        return subprocess.run(
            arguments,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _metadata(seed: int, warmups: int, repeats: int) -> dict[str, Any]:
    project_version = __version__
    if project_version == "unknown":
        try:
            project_version = (PROJECT_ROOT / "VERSION").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            pass
    working_tree_status = _command_output(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ]
    )
    return {
        "project_version": project_version,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "git_version": _command_output(["git", "--version"]),
        "platform": platform.platform(),
        "revision": _command_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"]
        ),
        "working_tree_dirty": (
            None if working_tree_status == "unknown" else bool(working_tree_status)
        ),
        "seed": seed,
        "warmups": warmups,
        "repeats": repeats,
        "memory_repeats": min(repeats, 3),
        "clock": "time.perf_counter",
        "timing_metric": "perf_counter without tracemalloc",
        "memory_metric": "tracemalloc peak Python allocations",
    }


def selected_cases(
    mode: str,
    names: Sequence[str] | None = None,
) -> list[CaseDefinition]:
    """Select named cases, or every case available in the requested mode."""
    if names:
        cases = [CASE_BY_NAME[name] for name in dict.fromkeys(names)]
    else:
        cases = list(CASES)
    if mode == "quick":
        unavailable = [case.name for case in cases if case.quick_size <= 0]
        if names and unavailable:
            raise ValueError(
                f"{', '.join(unavailable)} requires --mode full"
            )
        return [case for case in cases if case.quick_size > 0]
    return [case for case in cases if case.full_size is not None]


def run_suite(
    mode: str = "quick",
    *,
    case_names: Sequence[str] | None = None,
    seed: int = DEFAULT_SEED,
    warmups: int = 1,
    repeats: int = 3,
) -> dict[str, Any]:
    """Run selected deterministic cases and return the stable JSON schema."""
    if mode not in {"quick", "full"}:
        raise ValueError("mode must be 'quick' or 'full'")
    if warmups < 0:
        raise ValueError("warmups must be non-negative")
    if repeats <= 0:
        raise ValueError("repeats must be positive")

    results = []
    for case in selected_cases(mode, case_names):
        size = case.quick_size if mode == "quick" else case.full_size
        if size is None or size <= 0:
            continue
        result = run_matching_case(
            case,
            size,
            seed,
            warmups=warmups,
            repeats=repeats,
        )
        results.append(result)

    return {
        "schema_version": SCHEMA_VERSION,
        "suite": "matching",
        "mode": mode,
        "metadata": _metadata(seed, warmups, repeats),
        "cases": results,
    }


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("quick", "full"), default="quick")
    parser.add_argument(
        "--case",
        action="append",
        choices=tuple(CASE_BY_NAME),
        dest="case_names",
        help="run one case (repeat to select more than one)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--warmups", type=int)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()

    warmups = (
        args.warmups
        if args.warmups is not None
        else (1 if args.mode == "quick" else 2)
    )
    repeats = (
        args.repeats
        if args.repeats is not None
        else (3 if args.mode == "quick" else 7)
    )
    try:
        report = run_suite(
            args.mode,
            case_names=args.case_names,
            seed=args.seed,
            warmups=warmups,
            repeats=repeats,
        )
    except ValueError as error:
        parser.error(str(error))
    try:
        _write_report(report, args.output)
    except OSError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()

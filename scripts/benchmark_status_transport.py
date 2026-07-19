#!/usr/bin/env python3
"""Benchmark whole-status inline and forkserver file-job transports."""

# The script makes the repository source tree importable before project imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from git_stage_batch import __version__
from git_stage_batch.commands.status import command_status
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import ensure_state_directory_exists
from git_stage_batch.utils.session_lock import acquire_session_lock


SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class StatusCase:
    """One deterministic status transport fixture."""

    name: str
    description: str
    file_count: int
    line_count: int
    hunks_per_file: int
    quick: bool = True


CASES = (
    StatusCase(
        "small-one",
        "one 300-line file with one sparse edit",
        1,
        300,
        1,
    ),
    StatusCase(
        "one-10k",
        "one 10,000-line file with eight sparse edits",
        1,
        10_000,
        8,
    ),
    StatusCase(
        "four-small",
        "four 300-line files with one sparse edit each",
        4,
        300,
        1,
    ),
    StatusCase(
        "two-10k",
        "two 10,000-line files with eight sparse edits each",
        2,
        10_000,
        8,
    ),
    StatusCase(
        "four-10k",
        "four 10,000-line files with eight sparse edits each",
        4,
        10_000,
        8,
    ),
    StatusCase(
        "eight-10k",
        "eight 10,000-line files with eight sparse edits each",
        8,
        10_000,
        8,
        quick=False,
    ),
    StatusCase(
        "sixteen-10k",
        "sixteen 10,000-line files with eight sparse edits each",
        16,
        10_000,
        8,
        quick=False,
    ),
    StatusCase(
        "two-50k",
        "two 50,000-line files with eight sparse edits each",
        2,
        50_000,
        8,
        quick=False,
    ),
    StatusCase(
        "four-50k",
        "four 50,000-line files with eight sparse edits each",
        4,
        50_000,
        8,
        quick=False,
    ),
)
CASE_BY_NAME = {case.name: case for case in CASES}


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def _changed_line_numbers(line_count: int, hunk_count: int) -> frozenset[int]:
    if hunk_count <= 0 or hunk_count > line_count:
        raise ValueError("hunk count must fit inside the file")
    spacing = line_count // (hunk_count + 1)
    if spacing < 8 and hunk_count > 1:
        raise ValueError("fixture edits must remain separate Git hunks")
    return frozenset(
        max(1, min(line_count, spacing * (index + 1)))
        for index in range(hunk_count)
    )


def _write_fixture_file(
    path: Path,
    *,
    line_count: int,
    changed_lines: frozenset[int],
) -> None:
    with path.open("w", encoding="utf-8") as output:
        for line_number in range(1, line_count + 1):
            marker = " changed" if line_number in changed_lines else ""
            output.write(f"line {line_number:06d}{marker}\n")


def _build_repository(repository: Path, case: StatusCase) -> None:
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Status Benchmark")
    _git(
        repository,
        "config",
        "user.email",
        "status-benchmark@example.invalid",
    )
    for file_index in range(case.file_count):
        _write_fixture_file(
            repository / f"file-{file_index:02d}.txt",
            line_count=case.line_count,
            changed_lines=frozenset(),
        )
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "benchmark baseline")

    changed_lines = _changed_line_numbers(
        case.line_count,
        case.hunks_per_file,
    )
    for file_index in range(case.file_count):
        _write_fixture_file(
            repository / f"file-{file_index:02d}.txt",
            line_count=case.line_count,
            changed_lines=changed_lines,
        )

    original_directory = Path.cwd()
    try:
        os.chdir(repository)
        ensure_state_directory_exists()
        initialize_abort_state()
    finally:
        os.chdir(original_directory)


def _worker_command(
    repository: Path,
    requested_jobs: str,
    mode: str,
) -> dict[str, Any]:
    os.environ["GIT_STAGE_BATCH_JOBS"] = requested_jobs
    os.chdir(repository)

    stdout = io.StringIO()
    stderr = io.StringIO()
    started = time.perf_counter()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        if mode == "prompt":
            command_status(prompt_format="{remaining}")
        else:
            with acquire_session_lock():
                command_status(porcelain=True)

    return {
        "command_seconds": time.perf_counter() - started,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
    }


def _run_worker(
    repository: Path,
    requested_jobs: str,
    mode: str,
) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(
        prefix="git-stage-batch-status-benchmark-",
        suffix=".json",
        delete=False,
    ) as metrics_file:
        metrics_path = Path(metrics_file.name)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        str(repository),
        requested_jobs,
        mode,
        str(metrics_path),
    ]

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except BaseException:
        metrics_path.unlink(missing_ok=True)
        raise
    wall_seconds = time.perf_counter() - started
    if completed.returncode != 0:
        metrics_path.unlink(missing_ok=True)
        raise RuntimeError(
            "benchmark worker failed with status "
            f"{completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        measurement = json.loads(metrics_path.read_text(encoding="utf-8"))
    finally:
        metrics_path.unlink(missing_ok=True)
    if completed.stdout or completed.stderr:
        raise RuntimeError("benchmark worker produced unexpected harness output")

    measurement["wall_seconds"] = wall_seconds
    return measurement


def _summary(values: Sequence[float | int]) -> dict[str, float]:
    numeric_values = [float(value) for value in values]
    return {
        "minimum": min(numeric_values),
        "median": statistics.median(numeric_values),
        "maximum": max(numeric_values),
    }


def _expected_output(case: StatusCase) -> int:
    return case.file_count * case.hunks_per_file


def _validate_output(
    measurement: dict[str, Any],
    case: StatusCase,
    mode: str,
) -> None:
    expected_remaining = _expected_output(case)
    if measurement["stderr"]:
        raise RuntimeError(
            f"{case.name} wrote stderr: {measurement['stderr']!r}"
        )
    if mode == "prompt":
        actual_remaining = int(measurement["stdout"])
    else:
        actual_remaining = json.loads(measurement["stdout"])[
            "progress"
        ]["remaining"]
    if actual_remaining != expected_remaining:
        raise RuntimeError(
            f"{case.name} reported {actual_remaining} remaining changes; "
            f"expected {expected_remaining}"
        )


def _measure_execution(
    repository: Path,
    case: StatusCase,
    requested_jobs: str,
    mode: str,
    *,
    warmups: int,
    repeats: int,
) -> dict[str, Any]:
    for _warmup in range(warmups):
        measurement = _run_worker(repository, requested_jobs, mode)
        _validate_output(measurement, case, mode)

    samples = []
    for _repeat in range(repeats):
        measurement = _run_worker(repository, requested_jobs, mode)
        _validate_output(measurement, case, mode)
        samples.append(measurement)

    fields = ("wall_seconds", "command_seconds")
    return {
        "requested_jobs": requested_jobs,
        "summary": {
            field: _summary([sample[field] for sample in samples])
            for field in fields
        },
        "samples": {
            field: [sample[field] for sample in samples]
            for field in fields
        },
    }


def _environment_metadata(*, warmups: int, repeats: int) -> dict[str, Any]:
    return {
        "project_version": __version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "git_version": subprocess.run(
            ["git", "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "platform": platform.platform(),
        "revision": subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "warmups": warmups,
        "repeats": repeats,
        "clock": "time.perf_counter",
    }


def selected_cases(
    mode: str,
    names: Sequence[str] | None = None,
) -> list[StatusCase]:
    """Return requested cases or the cases supported by one benchmark mode."""
    if names:
        return [CASE_BY_NAME[name] for name in dict.fromkeys(names)]
    if mode == "quick":
        return [case for case in CASES if case.quick]
    return list(CASES)


def run_suite(
    mode: str = "quick",
    *,
    case_names: Sequence[str] | None = None,
    requested_jobs: Sequence[str] = ("1", "2", "4", "auto"),
    command_mode: str = "prompt",
    warmups: int = 1,
    repeats: int = 3,
) -> dict[str, Any]:
    """Build deterministic fixtures and benchmark whole status invocations."""
    if mode not in {"quick", "full"}:
        raise ValueError("mode must be quick or full")
    if command_mode not in {"prompt", "porcelain"}:
        raise ValueError("command mode must be prompt or porcelain")
    if warmups < 0 or repeats <= 0:
        raise ValueError("benchmark repeat counts must be positive")
    jobs_values = tuple(dict.fromkeys(requested_jobs))
    if not jobs_values:
        raise ValueError("at least one jobs value is required")
    for value in jobs_values:
        if value not in {"auto", "1", "2", "4"}:
            raise ValueError("jobs values must be auto, 1, 2, or 4")

    reports = []
    with tempfile.TemporaryDirectory(
        prefix="git-stage-batch-status-suite-"
    ) as temporary_directory:
        suite_directory = Path(temporary_directory)
        for case in selected_cases(mode, case_names):
            repository = suite_directory / case.name / "repository"
            repository.parent.mkdir()
            _build_repository(repository, case)
            executions = [
                _measure_execution(
                    repository,
                    case,
                    value,
                    command_mode,
                    warmups=warmups,
                    repeats=repeats,
                )
                for value in jobs_values
            ]
            reports.append({
                "name": case.name,
                "description": case.description,
                "dimensions": {
                    "files": case.file_count,
                    "lines_per_file": case.line_count,
                    "hunks_per_file": case.hunks_per_file,
                },
                "executions": executions,
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "status-file-job-transport",
        "mode": mode,
        "command_mode": command_mode,
        "environment": _environment_metadata(
            warmups=warmups,
            repeats=repeats,
        ),
        "cases": reports,
    }


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(serialized)
    else:
        output.write_text(serialized, encoding="utf-8")


def _write_short_table(report: dict[str, Any]) -> None:
    sys.stderr.write("case jobs seconds\n")
    for case in report["cases"]:
        for execution in case["executions"]:
            summary = execution["summary"]
            sys.stderr.write(
                f"{case['name']} {execution['requested_jobs']} "
                f"{summary['wall_seconds']['median']:.6f}\n"
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("quick", "full"), default="quick")
    parser.add_argument(
        "--case",
        action="append",
        choices=tuple(CASE_BY_NAME),
        dest="case_names",
    )
    parser.add_argument(
        "--jobs",
        action="append",
        choices=("auto", "1", "2", "4"),
        dest="requested_jobs",
    )
    parser.add_argument(
        "--command-mode",
        choices=("prompt", "porcelain"),
        default="prompt",
    )
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--no-table", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--worker",
        nargs=4,
        metavar=("REPOSITORY", "JOBS", "MODE", "METRICS_PATH"),
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.worker is not None:
        repository, requested_jobs, mode, metrics_path = args.worker
        measurement = _worker_command(
            Path(repository),
            requested_jobs,
            mode,
        )
        Path(metrics_path).write_text(
            json.dumps(measurement, sort_keys=True),
            encoding="utf-8",
        )
        return

    try:
        report = run_suite(
            args.mode,
            case_names=args.case_names,
            requested_jobs=args.requested_jobs or ("1", "2", "4", "auto"),
            command_mode=args.command_mode,
            warmups=args.warmups,
            repeats=args.repeats,
        )
        _write_report(report, args.output)
        if not args.no_table:
            _write_short_table(report)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()

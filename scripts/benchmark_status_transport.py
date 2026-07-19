#!/usr/bin/env python3
"""Benchmark whole-status inline and forkserver file-job transports."""

# The script makes the repository source tree importable before project imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from git_stage_batch import __version__
from git_stage_batch.commands.status import command_status
from git_stage_batch.data.session import initialize_abort_state
import git_stage_batch.data.remaining_hunks as remaining_hunks_module
from git_stage_batch.utils.paths import ensure_state_directory_exists
from git_stage_batch.utils.session_lock import acquire_session_lock
import git_stage_batch.utils.file_jobs as file_jobs_module


SCHEMA_VERSION = 1
_POLL_SECONDS = 0.005
_PROCESS_EXIT_GRACE_SECONDS = 1.0


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


def _workspace_root(jobs: Sequence[Any]) -> Path | None:
    if not jobs:
        return None
    input_manifest_path = Path(jobs[0].payload.input_manifest_path)
    return input_manifest_path.parents[3]


def _directory_bytes(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _sample_artifact_bytes(
    workspace_root: Path,
    measurement: dict[str, Any],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        measurement["peak_artifact_bytes"] = max(
            measurement["peak_artifact_bytes"],
            _directory_bytes(workspace_root),
        )
        stop_event.wait(_POLL_SECONDS)


def _install_worker_measurement(
    measurement: dict[str, Any],
    *,
    sample_artifacts: bool,
) -> None:
    original_acquire = remaining_hunks_module.acquire_live_change_count_plan
    original_select = remaining_hunks_module.select_file_job_execution
    original_run = remaining_hunks_module.run_file_jobs
    original_supervisor = file_jobs_module._ProcessFileJobSupervisor

    @contextmanager
    def acquire_plan() -> Iterator[Any]:
        started = time.perf_counter()
        workspace_root = None
        with original_acquire() as plan:
            measurement["plan_seconds"] = time.perf_counter() - started
            workspace_root = _workspace_root(plan.jobs)
            measurement["workspace_root"] = (
                None if workspace_root is None else str(workspace_root)
            )
            measurement["input_artifact_bytes"] = _directory_bytes(
                workspace_root
            )
            measurement["peak_artifact_bytes"] = measurement[
                "input_artifact_bytes"
            ]
            yield plan
            measurement["peak_artifact_bytes"] = max(
                measurement["input_artifact_bytes"],
                _directory_bytes(workspace_root),
            )
        measurement["workspace_cleaned"] = (
            workspace_root is None or not workspace_root.exists()
        )

    def select_execution(jobs, **selection):
        execution = original_select(jobs, **selection)
        measurement.update({
            "requested_jobs": selection["requested_jobs"] or "auto",
            "transport": execution.transport,
            "worker_count": execution.max_workers,
            "selection_reason": execution.reason,
            "job_count": len(jobs),
            "total_estimated_bytes": sum(
                job.estimated_bytes for job in jobs
            ),
            "largest_job_estimated_bytes": max(
                (job.estimated_bytes for job in jobs),
                default=0,
            ),
        })
        return execution

    def run_jobs(*args, **kwargs):
        workspace_root_value = measurement.get("workspace_root")
        workspace_root = (
            None
            if workspace_root_value is None
            else Path(workspace_root_value)
        )
        stop_event = threading.Event()
        sampler = None
        if sample_artifacts and workspace_root is not None:
            sampler = threading.Thread(
                target=_sample_artifact_bytes,
                args=(workspace_root, measurement, stop_event),
                name="status-benchmark-artifact-sampler",
                daemon=True,
            )
            sampler.start()
        started = time.perf_counter()
        try:
            return original_run(*args, **kwargs)
        finally:
            measurement["transport_seconds"] = (
                time.perf_counter() - started
            )
            if workspace_root is not None:
                measurement["peak_artifact_bytes"] = max(
                    measurement["peak_artifact_bytes"],
                    _directory_bytes(workspace_root),
                )
            stop_event.set()
            if sampler is not None:
                sampler.join()

    class MeasuredSupervisor(original_supervisor):
        def __init__(self, *args, **kwargs):
            started = time.perf_counter()
            super().__init__(*args, **kwargs)
            measurement["worker_startup_seconds"] = (
                time.perf_counter() - started
            )

        def submit(self, job):
            started = time.perf_counter()
            try:
                return super().submit(job)
            finally:
                measurement["queue_seconds"] = (
                    measurement.get("queue_seconds", 0.0)
                    + time.perf_counter()
                    - started
                )

        def receive(self):
            started = time.perf_counter()
            try:
                return super().receive()
            finally:
                measurement["parent_wait_seconds"] = (
                    measurement.get("parent_wait_seconds", 0.0)
                    + time.perf_counter()
                    - started
                )

    remaining_hunks_module.acquire_live_change_count_plan = acquire_plan
    remaining_hunks_module.select_file_job_execution = select_execution
    remaining_hunks_module.run_file_jobs = run_jobs
    file_jobs_module._ProcessFileJobSupervisor = MeasuredSupervisor


def _worker_command(
    repository: Path,
    requested_jobs: str,
    mode: str,
    *,
    trace_python_heap: bool,
    sample_artifacts: bool,
) -> dict[str, Any]:
    measurement: dict[str, Any] = {
        "worker_startup_seconds": 0.0,
        "queue_seconds": 0.0,
        "parent_wait_seconds": 0.0,
        "tracemalloc_peak_bytes": 0,
    }
    _install_worker_measurement(
        measurement,
        sample_artifacts=sample_artifacts,
    )
    os.environ["GIT_STAGE_BATCH_JOBS"] = requested_jobs
    os.chdir(repository)

    stdout = io.StringIO()
    stderr = io.StringIO()
    if trace_python_heap:
        tracemalloc.start()
    started = time.perf_counter()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            if mode == "prompt":
                command_status(prompt_format="{remaining}")
            else:
                with acquire_session_lock():
                    command_status(porcelain=True)
    finally:
        measurement["command_seconds"] = time.perf_counter() - started
        if trace_python_heap:
            _, peak_bytes = tracemalloc.get_traced_memory()
            measurement["tracemalloc_peak_bytes"] = peak_bytes
            tracemalloc.stop()

    transport_seconds = measurement.get("transport_seconds", 0.0)
    measurement["task_seconds"] = max(
        0.0,
        transport_seconds
        - measurement["worker_startup_seconds"]
        - measurement["queue_seconds"],
    )
    measurement["stdout"] = stdout.getvalue()
    measurement["stderr"] = stderr.getvalue()
    return measurement


def _read_rss_bytes(process_id: int) -> int:
    try:
        with Path(f"/proc/{process_id}/status").open(
            encoding="ascii"
        ) as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
    return 0


def _process_start_marker(process_id: int) -> str | None:
    try:
        content = Path(f"/proc/{process_id}/stat").read_text(
            encoding="ascii"
        )
        fields_after_name = content[content.rfind(")") + 2:].split()
        return fields_after_name[19]
    except (
        FileNotFoundError,
        IndexError,
        PermissionError,
        ProcessLookupError,
    ):
        return None


def _direct_children(process_id: int) -> tuple[int, ...]:
    children_path = Path(
        f"/proc/{process_id}/task/{process_id}/children"
    )
    try:
        content = children_path.read_text().strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return ()
    return tuple(int(value) for value in content.split()) if content else ()


def _descendants(process_id: int) -> set[int]:
    found = set()
    pending = list(_direct_children(process_id))
    while pending:
        child = pending.pop()
        if child in found:
            continue
        found.add(child)
        pending.extend(_direct_children(child))
    return found


def _same_process_is_alive(process_id: int, start_marker: str | None) -> bool:
    if start_marker is None:
        return False
    return _process_start_marker(process_id) == start_marker


def _run_worker(
    repository: Path,
    requested_jobs: str,
    mode: str,
    *,
    trace_python_heap: bool = False,
    sample_artifacts: bool = False,
    environment: dict[str, str] | None = None,
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
    if trace_python_heap:
        command.append("--trace-python-heap")
    if sample_artifacts:
        command.append("--sample-artifacts")

    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
    except BaseException:
        metrics_path.unlink(missing_ok=True)
        raise
    peak_parent_rss = 0
    peak_child_rss = 0
    observed_descendants: dict[int, str | None] = {}
    while process.poll() is None:
        peak_parent_rss = max(
            peak_parent_rss,
            _read_rss_bytes(process.pid),
        )
        child_ids = _descendants(process.pid)
        for child_id in child_ids:
            observed_descendants.setdefault(
                child_id,
                _process_start_marker(child_id),
            )
        peak_child_rss = max(
            peak_child_rss,
            sum(_read_rss_bytes(child_id) for child_id in child_ids),
        )
        time.sleep(_POLL_SECONDS)
    stdout, stderr = process.communicate()
    wall_seconds = time.perf_counter() - started

    if process.returncode != 0:
        metrics_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"benchmark worker failed with status {process.returncode}: "
            f"{stderr.strip()}"
        )
    try:
        measurement = json.loads(metrics_path.read_text(encoding="utf-8"))
    finally:
        metrics_path.unlink(missing_ok=True)
    if stdout or stderr:
        raise RuntimeError("benchmark worker produced unexpected harness output")

    deadline = time.monotonic() + _PROCESS_EXIT_GRACE_SECONDS
    leaked_processes = []
    while True:
        leaked_processes = [
            process_id
            for process_id, marker in observed_descendants.items()
            if _same_process_is_alive(process_id, marker)
        ]
        if not leaked_processes or time.monotonic() >= deadline:
            break
        time.sleep(0.01)

    measurement.update({
        "wall_seconds": wall_seconds,
        "peak_parent_rss_bytes": peak_parent_rss,
        "peak_child_rss_bytes": peak_child_rss,
        "leaked_processes": leaked_processes,
    })
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


def _git_count_environment(
    directory: Path,
) -> tuple[dict[str, str], Path]:
    real_git = shutil.which("git")
    if real_git is None:
        raise RuntimeError("git is unavailable")
    wrapper_directory = directory / "git-wrapper"
    wrapper_directory.mkdir()
    count_path = directory / "git-count.txt"
    wrapper_path = wrapper_directory / "git"
    wrapper_path.write_text(
        "#!/bin/sh\n"
        'printf "1\\n" >> \"$GIT_STAGE_BATCH_BENCHMARK_GIT_COUNT\"\n'
        f"exec {shlex.quote(real_git)} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper_path.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = (
        f"{wrapper_directory}{os.pathsep}{environment.get('PATH', '')}"
    )
    environment["GIT_STAGE_BATCH_BENCHMARK_GIT_COUNT"] = str(count_path)
    return environment, count_path


def _measure_execution(
    repository: Path,
    case: StatusCase,
    requested_jobs: str,
    mode: str,
    *,
    warmups: int,
    repeats: int,
    memory_repeats: int,
    profile_directory: Path,
) -> dict[str, Any]:
    for _warmup in range(warmups):
        measurement = _run_worker(repository, requested_jobs, mode)
        _validate_output(measurement, case, mode)

    timing_samples = []
    for _repeat in range(repeats):
        measurement = _run_worker(repository, requested_jobs, mode)
        _validate_output(measurement, case, mode)
        timing_samples.append(measurement)

    memory_samples = []
    for _memory_repeat in range(memory_repeats):
        measurement = _run_worker(
            repository,
            requested_jobs,
            mode,
            trace_python_heap=True,
            sample_artifacts=True,
        )
        _validate_output(measurement, case, mode)
        memory_samples.append(measurement)

    count_environment, count_path = _git_count_environment(profile_directory)
    count_measurement = _run_worker(
        repository,
        requested_jobs,
        mode,
        environment=count_environment,
    )
    _validate_output(count_measurement, case, mode)
    git_subprocess_count = (
        len(count_path.read_text().splitlines())
        if count_path.exists()
        else 0
    )

    representative = timing_samples[0]
    scalar_fields = (
        "wall_seconds",
        "command_seconds",
        "plan_seconds",
        "transport_seconds",
        "worker_startup_seconds",
        "queue_seconds",
        "task_seconds",
        "parent_wait_seconds",
        "peak_parent_rss_bytes",
        "peak_child_rss_bytes",
    )
    return {
        "requested_jobs": requested_jobs,
        "transport": representative["transport"],
        "worker_count": representative["worker_count"],
        "selection_reason": representative["selection_reason"],
        "job_count": representative["job_count"],
        "total_estimated_bytes": representative["total_estimated_bytes"],
        "largest_job_estimated_bytes": representative[
            "largest_job_estimated_bytes"
        ],
        "input_artifact_bytes": representative["input_artifact_bytes"],
        "peak_artifact_bytes": max(
            sample["peak_artifact_bytes"]
            for sample in timing_samples + memory_samples + [count_measurement]
        ),
        "git_subprocess_count": git_subprocess_count,
        "workspace_cleaned": all(
            sample["workspace_cleaned"]
            for sample in timing_samples + memory_samples + [count_measurement]
        ),
        "leaked_processes": sorted({
            process_id
            for sample in timing_samples + memory_samples + [count_measurement]
            for process_id in sample["leaked_processes"]
        }),
        "summary": {
            field: _summary([sample[field] for sample in timing_samples])
            for field in scalar_fields
        }
        | {
            "tracemalloc_peak_bytes": _summary([
                sample["tracemalloc_peak_bytes"]
                for sample in memory_samples
            ]),
        },
        "samples": {
            field: [sample[field] for sample in timing_samples]
            for field in scalar_fields
        }
        | {
            "tracemalloc_peak_bytes": [
                sample["tracemalloc_peak_bytes"]
                for sample in memory_samples
            ],
        },
    }


def _environment_metadata(
    *,
    warmups: int,
    repeats: int,
    memory_repeats: int,
) -> dict[str, Any]:
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
        "memory_repeats": memory_repeats,
        "clock": "time.perf_counter",
        "rss_metric": "sampled Linux /proc VmRSS",
        "queue_metric": "parent time serializing and sending jobs",
        "task_metric": (
            "transport time minus worker startup and parent queue time"
        ),
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
    memory_repeats: int = 1,
) -> dict[str, Any]:
    """Build deterministic fixtures and benchmark whole status invocations."""
    if mode not in {"quick", "full"}:
        raise ValueError("mode must be quick or full")
    if command_mode not in {"prompt", "porcelain"}:
        raise ValueError("command mode must be prompt or porcelain")
    if warmups < 0 or repeats <= 0 or memory_repeats <= 0:
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
            executions = []
            for value in jobs_values:
                profile_directory = (
                    suite_directory / case.name / f"profile-{value}"
                )
                profile_directory.mkdir()
                executions.append(
                    _measure_execution(
                        repository,
                        case,
                        value,
                        command_mode,
                        warmups=warmups,
                        repeats=repeats,
                        memory_repeats=memory_repeats,
                        profile_directory=profile_directory,
                    )
                )
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
            memory_repeats=memory_repeats,
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
    sys.stderr.write(
        "case jobs transport workers seconds parent-rss-mib "
        "child-rss-mib heap-mib artifacts-mib git\n"
    )
    for case in report["cases"]:
        for execution in case["executions"]:
            summary = execution["summary"]
            sys.stderr.write(
                f"{case['name']} {execution['requested_jobs']} "
                f"{execution['transport']} {execution['worker_count']} "
                f"{summary['wall_seconds']['median']:.6f} "
                f"{summary['peak_parent_rss_bytes']['maximum'] / 2**20:.1f} "
                f"{summary['peak_child_rss_bytes']['maximum'] / 2**20:.1f} "
                f"{summary['tracemalloc_peak_bytes']['maximum'] / 2**20:.1f} "
                f"{execution['peak_artifact_bytes'] / 2**20:.1f} "
                f"{execution['git_subprocess_count']}\n"
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
    parser.add_argument("--memory-repeats", type=int, default=1)
    parser.add_argument("--no-table", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--worker",
        nargs=4,
        metavar=("REPOSITORY", "JOBS", "MODE", "METRICS_PATH"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--trace-python-heap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--sample-artifacts",
        action="store_true",
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
            trace_python_heap=args.trace_python_heap,
            sample_artifacts=args.sample_artifacts,
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
            memory_repeats=args.memory_repeats,
        )
        _write_report(report, args.output)
        if not args.no_table:
            _write_short_table(report)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()

"""Ordered inline and forkserver execution for compact file-scoped jobs."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from ..exceptions import CommandError
from .file_job_process import (
    _FileJobSupervisorError,
    _ProcessFileJobSupervisor as _ProcessSupervisor,
    _WorkerResponse,
)
from .file_job_transport import (
    _MAX_TRANSPORT_INTEGER_BITS as _MAX_TRANSPORT_INTEGER_BITS,
    _MAX_TRANSPORT_NESTING_DEPTH as _MAX_TRANSPORT_NESTING_DEPTH,
    _MAX_TRANSPORT_STRING_CHARACTERS as _MAX_TRANSPORT_STRING_CHARACTERS,
    _MAX_TRANSPORT_TOTAL_STRING_CHARACTERS as _MAX_TRANSPORT_TOTAL_STRING_CHARACTERS,
    _MAX_TRANSPORT_TUPLE_ITEMS as _MAX_TRANSPORT_TUPLE_ITEMS,
    _MAX_TRANSPORT_VALUE_COUNT as _MAX_TRANSPORT_VALUE_COUNT,
    assert_file_job_transport_value,
)


if TYPE_CHECKING:
    from multiprocessing.connection import Connection


JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")
_MAX_FILE_JOB_WORKERS = 4
_AUTO_PROCESS_MINIMUM_ESTIMATED_BYTES = 256 * 1024
_MAX_ERROR_MESSAGE_CHARACTERS = 4 * 1024


@dataclass(frozen=True, slots=True)
class OrderedFileJob(Generic[JobT]):
    """One compact file job with stable reduction and scheduling metadata."""

    ordinal: int
    file_path: str
    estimated_bytes: int
    payload: JobT


@dataclass(frozen=True, slots=True)
class FileJobExecution:
    """Selected transport and bounded worker count for one invocation."""

    transport: Literal["inline", "process"]
    max_workers: int
    reason: str


class FileJobError(CommandError):
    """Unexpected failure while computing one ordered file job."""

    def __init__(
        self,
        ordinal: int,
        file_path: str,
        original_message: str,
    ) -> None:
        self.ordinal = ordinal
        self.file_path = file_path
        self.original_message = original_message
        super().__init__(
            f"File job {ordinal} for '{file_path}' failed: {original_message}"
        )


class _ProcessFileJobSupervisor(_ProcessSupervisor[JobT, ResultT]):
    """Bind the generic process supervisor to this module's worker."""

    def __init__(
        self,
        compute: Callable[[JobT], ResultT],
        *,
        max_workers: int,
        repository_root: Path,
    ) -> None:
        super().__init__(
            compute,
            max_workers=max_workers,
            repository_root=repository_root,
            worker_target=_file_job_worker,
            context_factory=_get_process_context,
        )


def select_file_job_execution(
    jobs: Sequence[OrderedFileJob[object]],
    *,
    requested_jobs: str | None,
    platform: str,
    cpu_count: int | None,
) -> FileJobExecution:
    """Select inline or bounded Linux process execution."""
    job_count = len(jobs)
    requested_value = "auto" if requested_jobs is None else requested_jobs
    if requested_value == "":
        raise _invalid_jobs_value()

    if requested_value == "auto":
        if platform != "linux":
            return FileJobExecution(
                "inline",
                1,
                f"automatic process execution is unavailable on {platform}",
            )
        if job_count < 2:
            return FileJobExecution(
                "inline",
                1,
                "automatic process execution requires at least 2 file jobs",
            )
        available_cpus = _normalize_cpu_count(cpu_count)
        if available_cpus < 2:
            return FileJobExecution(
                "inline",
                1,
                "automatic process execution requires at least 2 CPUs",
            )
        total_estimated_bytes = sum(job.estimated_bytes for job in jobs)
        # A 2026-07-16 whole-prompt benchmark measured a 20 percent gain near
        # 242 KiB and larger gains above it. Use a round 256 KiB heuristic to
        # amortize process startup without implying a precise crossover.
        if total_estimated_bytes < _AUTO_PROCESS_MINIMUM_ESTIMATED_BYTES:
            return FileJobExecution(
                "inline",
                1,
                "automatic process execution kept "
                f"{total_estimated_bytes} estimated bytes inline below the "
                f"{_AUTO_PROCESS_MINIMUM_ESTIMATED_BYTES}-byte threshold",
            )
        worker_count = min(
            job_count,
            available_cpus,
            _MAX_FILE_JOB_WORKERS,
        )
        return FileJobExecution(
            "process",
            worker_count,
            "automatic process execution selected "
            f"{worker_count} workers for {job_count} file jobs totaling "
            f"{total_estimated_bytes} estimated bytes",
        )

    try:
        requested_count = int(requested_value)
    except ValueError as error:
        raise _invalid_jobs_value() from error

    if requested_count <= 0:
        raise _invalid_jobs_value()
    if requested_count == 1:
        reason = "GIT_STAGE_BATCH_JOBS requests inline execution"
        return FileJobExecution("inline", 1, reason)
    if platform != "linux":
        raise CommandError("GIT_STAGE_BATCH_JOBS greater than 1 requires Linux.")
    if job_count == 0:
        return FileJobExecution("inline", 1, "no eligible file jobs")

    worker_count = min(
        job_count,
        requested_count,
        _normalize_cpu_count(cpu_count),
        _MAX_FILE_JOB_WORKERS,
    )
    return FileJobExecution(
        "process",
        worker_count,
        f"GIT_STAGE_BATCH_JOBS requests {requested_count} processes",
    )


def run_file_jobs(
    jobs: Sequence[OrderedFileJob[JobT]],
    compute: Callable[[JobT], ResultT],
    *,
    execution: FileJobExecution,
    repository_root: Path,
) -> list[ResultT]:
    """Run one ordered job pipeline through the selected transport."""
    ordered_jobs = _validate_jobs(jobs)
    _validate_compute(compute)
    if execution.transport not in {"inline", "process"}:
        raise ValueError(f"unsupported file-job transport: {execution.transport}")
    if type(execution.max_workers) is not int or execution.max_workers <= 0:
        raise ValueError("file-job execution requires a positive worker count")
    if execution.transport == "process" and sys.platform != "linux":
        raise ValueError("process file-job execution requires Linux")
    if not ordered_jobs:
        return []
    repository_root = repository_root.resolve()
    if execution.transport == "inline":
        return _run_inline_file_jobs(
            ordered_jobs,
            compute,
            repository_root=repository_root,
        )

    return _run_process_file_jobs(
        ordered_jobs,
        compute,
        max_workers=min(
            execution.max_workers,
            len(ordered_jobs),
            _MAX_FILE_JOB_WORKERS,
        ),
        repository_root=repository_root,
    )


def run_validated_file_jobs(
    jobs: Sequence[OrderedFileJob[JobT]],
    compute: Callable[[JobT], ResultT],
    validate_result: Callable[[JobT, ResultT], None],
    *,
    repository_root: Path,
    result_label: str,
    run_jobs: Callable[..., list[ResultT]] = run_file_jobs,
) -> list[tuple[OrderedFileJob[JobT], ResultT]]:
    """Select, execute, pair, and validate one ordered job pipeline."""
    execution = select_file_job_execution(
        jobs,
        requested_jobs=os.environ.get("GIT_STAGE_BATCH_JOBS"),
        platform=sys.platform,
        cpu_count=None,
    )
    results = run_jobs(
        jobs,
        compute,
        execution=execution,
        repository_root=repository_root,
    )
    ordered_jobs = sorted(jobs, key=lambda job: job.ordinal)
    if len(results) != len(ordered_jobs):
        raise RuntimeError(
            f"{result_label} execution returned an unexpected result count"
        )
    paired_results = list(zip(ordered_jobs, results, strict=True))
    for job, result in paired_results:
        validate_result(job.payload, result)
    return paired_results


def _run_inline_file_jobs(
    jobs: Sequence[OrderedFileJob[JobT]],
    compute: Callable[[JobT], ResultT],
    *,
    repository_root: Path,
) -> list[ResultT]:
    try:
        with _repository_working_directory(repository_root):
            results = []
            for job in jobs:
                try:
                    result = compute(job.payload)
                    assert_file_job_transport_value(
                        result,
                        label=f"result for job {job.ordinal}",
                    )
                except KeyboardInterrupt:
                    raise
                except BaseException as error:
                    raise _job_error(job, error) from error
                results.append(result)
            return results
    except (KeyboardInterrupt, FileJobError):
        raise
    except BaseException as error:
        raise _job_error(jobs[0], error) from error


def _run_process_file_jobs(
    jobs: Sequence[OrderedFileJob[JobT]],
    compute: Callable[[JobT], ResultT],
    *,
    max_workers: int,
    repository_root: Path,
) -> list[ResultT]:
    supervisor: _ProcessFileJobSupervisor[JobT, ResultT] | None = None
    results_by_ordinal: dict[int, ResultT] = {}
    failures_by_ordinal: dict[int, _WorkerResponse[ResultT]] = {}
    pending_ordinals: set[int] = set()
    try:
        supervisor = _ProcessFileJobSupervisor(
            compute,
            max_workers=max_workers,
            repository_root=repository_root,
        )
        ready_jobs = deque(_jobs_by_submission_priority(jobs))

        while ready_jobs or supervisor.pending_count:
            while ready_jobs and not failures_by_ordinal and supervisor.can_submit:
                job = ready_jobs.popleft()
                supervisor.submit(job)
                pending_ordinals.add(job.ordinal)

            if failures_by_ordinal and not _has_pending_lower_ordinal(
                pending_ordinals,
                failures_by_ordinal,
            ):
                supervisor.terminate()
                supervisor = None
                break

            response = supervisor.receive()
            pending_ordinals.discard(response.ordinal)
            if response.interrupted:
                raise KeyboardInterrupt
            if response.error_type is None:
                results_by_ordinal[response.ordinal] = response.result  # type: ignore[assignment]
            else:
                failures_by_ordinal[response.ordinal] = response
                ready_jobs.clear()

        if supervisor is not None:
            supervisor.close()
            supervisor = None
    except KeyboardInterrupt:
        if supervisor is not None:
            supervisor.terminate()
        raise
    except BaseException as error:
        if supervisor is not None:
            supervisor.terminate()
        if isinstance(error, FileJobError):
            raise
        supervisor_ordinal = (
            error.ordinal if isinstance(error, _FileJobSupervisorError) else None
        )
        failure_ordinal = _lowest_failure_ordinal(
            jobs,
            failures_by_ordinal=failures_by_ordinal,
            supervisor_ordinal=supervisor_ordinal,
            results_by_ordinal=results_by_ordinal,
        )
        if failure_ordinal in failures_by_ordinal:
            _raise_worker_failure(
                jobs,
                failures_by_ordinal[failure_ordinal],
            )
        job = next(job for job in jobs if job.ordinal == failure_ordinal)
        raise _job_error(job, error) from error

    if failures_by_ordinal:
        _raise_worker_failure(
            jobs,
            failures_by_ordinal[min(failures_by_ordinal)],
        )

    return [results_by_ordinal[job.ordinal] for job in jobs]


def _has_pending_lower_ordinal(
    pending_ordinals: set[int],
    failures_by_ordinal: dict[int, _WorkerResponse[object]],
) -> bool:
    failure_ordinal = min(failures_by_ordinal)
    return any(ordinal < failure_ordinal for ordinal in pending_ordinals)


def _jobs_by_submission_priority(
    jobs: Sequence[OrderedFileJob[JobT]],
) -> list[OrderedFileJob[JobT]]:
    return sorted(
        jobs,
        key=lambda job: (-job.estimated_bytes, job.ordinal),
    )


def _file_job_worker(
    connection: Connection,
    compute: Callable[[JobT], ResultT],
    repository_root: Path,
) -> None:
    try:
        _initialize_file_job_worker(repository_root)
        while True:
            job = connection.recv()
            if job is None:
                return
            try:
                result = compute(job.payload)
                assert_file_job_transport_value(
                    result,
                    label=f"result for job {job.ordinal}",
                )
                response = _WorkerResponse(job.ordinal, result=result)
            except KeyboardInterrupt:
                response = _WorkerResponse(job.ordinal, interrupted=True)
            except BaseException as error:
                error_type = _bounded_error_type(error)
                response = _WorkerResponse(
                    job.ordinal,
                    error_type=error_type,
                    error_message=_bounded_error_message(error),
                )
            try:
                connection.send(response)
            except (BrokenPipeError, EOFError, OSError):
                return
            except BaseException as error:
                error_type = _bounded_error_type(error)
                try:
                    connection.send(
                        _WorkerResponse(
                            job.ordinal,
                            error_type=error_type,
                            error_message=_bounded_message(
                                "could not serialize worker result: "
                                f"{_bounded_error_message(error)}"
                            ),
                        )
                    )
                except BaseException:
                    return
    except (EOFError, KeyboardInterrupt):
        return
    finally:
        try:
            connection.close()
        except BaseException:
            pass


def _get_process_context():
    import multiprocessing

    return multiprocessing.get_context("forkserver")


def _initialize_file_job_worker(repository_root: Path) -> None:
    os.chdir(repository_root)


@contextmanager
def _repository_working_directory(
    repository_root: Path,
) -> Iterator[None]:
    original_directory = os.open(
        ".",
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.chdir(repository_root)
        yield
    finally:
        try:
            os.fchdir(original_directory)
        finally:
            os.close(original_directory)


def _validate_jobs(
    jobs: Sequence[OrderedFileJob[JobT]],
) -> list[OrderedFileJob[JobT]]:
    by_ordinal: dict[int, OrderedFileJob[JobT]] = {}
    for job in jobs:
        if (
            isinstance(job.ordinal, bool)
            or not isinstance(job.ordinal, int)
            or job.ordinal < 0
        ):
            raise ValueError("file-job ordinals must be non-negative integers")
        if job.ordinal in by_ordinal:
            raise ValueError(f"duplicate file-job ordinal: {job.ordinal}")
        if not isinstance(job.file_path, str):
            raise ValueError("file-job paths must be strings")
        if (
            isinstance(job.estimated_bytes, bool)
            or not isinstance(job.estimated_bytes, int)
            or job.estimated_bytes < 0
        ):
            raise ValueError("file-job estimated bytes must be non-negative")
        assert_file_job_transport_value(job, label=f"job {job.ordinal}")
        by_ordinal[job.ordinal] = job
    return [by_ordinal[ordinal] for ordinal in sorted(by_ordinal)]


def _validate_compute(compute: Callable[[JobT], ResultT]) -> None:
    module_name = getattr(compute, "__module__", "")
    name = getattr(compute, "__name__", "")
    qualified_name = getattr(compute, "__qualname__", "")
    if (
        not callable(compute)
        or not module_name
        or module_name == "__main__"
        or not name
        or qualified_name != name
        or name == "<lambda>"
    ):
        raise TypeError("file-job compute must be a top-level importable function")
    try:
        imported_compute = getattr(importlib.import_module(module_name), name)
    except Exception as error:
        raise TypeError(
            "file-job compute must be a top-level importable function"
        ) from error
    if imported_compute is not compute:
        raise TypeError("file-job compute must be a top-level importable function")


def _normalize_cpu_count(cpu_count: int | None) -> int:
    if cpu_count is None:
        try:
            cpu_count = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            cpu_count = os.cpu_count()
    return max(1, cpu_count or 1)


def _invalid_jobs_value() -> CommandError:
    return CommandError("GIT_STAGE_BATCH_JOBS must be 'auto' or a positive integer.")


def _lowest_failure_ordinal(
    jobs: Sequence[OrderedFileJob[object]],
    *,
    failures_by_ordinal: dict[int, _WorkerResponse[object]],
    supervisor_ordinal: int | None,
    results_by_ordinal: dict[int, object],
) -> int:
    candidates = list(failures_by_ordinal)
    if supervisor_ordinal is not None:
        candidates.append(supervisor_ordinal)
    if candidates:
        return min(candidates)

    unresolved_ordinals = [
        job.ordinal for job in jobs if job.ordinal not in results_by_ordinal
    ]
    return min(unresolved_ordinals or (job.ordinal for job in jobs))


def _raise_worker_failure(
    jobs: Sequence[OrderedFileJob[object]],
    response: _WorkerResponse[object],
) -> None:
    job = next(job for job in jobs if job.ordinal == response.ordinal)
    message = response.error_type or "worker task failed"
    if response.error_message:
        message = f"{message}: {response.error_message}"
    raise FileJobError(
        job.ordinal,
        job.file_path,
        _bounded_message(message),
    )


def _job_error(
    job: OrderedFileJob[object],
    error: BaseException,
) -> FileJobError:
    message = _bounded_error_message(error)
    return FileJobError(job.ordinal, job.file_path, message)


def _bounded_error_message(error: BaseException) -> str:
    try:
        message = str(error)
    except BaseException:
        message = ""
    return _bounded_message(message or type(error).__name__)


def _bounded_error_type(error: BaseException) -> str:
    return _bounded_message(f"{type(error).__module__}.{type(error).__qualname__}")


def _bounded_message(message: str) -> str:
    if len(message) <= _MAX_ERROR_MESSAGE_CHARACTERS:
        return message
    suffix = "..."
    return message[: _MAX_ERROR_MESSAGE_CHARACTERS - len(suffix)] + suffix

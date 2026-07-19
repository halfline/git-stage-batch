"""Tests for ordered inline and forkserver file-job execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
import hashlib
import itertools
import mmap
import os
from pathlib import Path
import pickle
import subprocess
import sys
import time

import pytest

import git_stage_batch.utils.file_jobs as file_jobs_module
from git_stage_batch.batch.line_matching.line_mapping import LineMapping
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_job_workspace import FileJobWorkspace
from git_stage_batch.utils.file_jobs import (
    FileJobError,
    FileJobExecution,
    OrderedFileJob,
    assert_file_job_transport_value,
    run_file_jobs,
    run_validated_file_jobs,
    select_file_job_execution,
)
from git_stage_batch.utils.paths import get_session_lock_file_path
from git_stage_batch.utils.session_lock import acquire_session_lock


_RUNNING_UNDER_XDIST = "PYTEST_XDIST_WORKER" in os.environ
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))
_PROCESS_TEST = pytest.mark.skipif(
    sys.platform != "linux" or _RUNNING_UNDER_XDIST,
    reason="forced forkserver coverage runs on Linux with pytest -n 0",
)
_PARENT_COMPUTE_CALLS = 0


@dataclass(frozen=True, slots=True)
class _DigestJob:
    artifact_path: Path
    scratch_directory: Path
    marker: int
    delay_seconds: float = 0.0
    failure: str | None = None
    exit_code: int | None = None
    parent_pid: int | None = None


@dataclass(frozen=True, slots=True)
class _DigestResult:
    marker: int
    digest: str
    line_count: int
    cwd: str


@dataclass(frozen=True, slots=True)
class _BytesPayload:
    content: bytes


@dataclass
class _ExtensiblePayload:
    marker: int


@dataclass(frozen=True)
class _ManualSlotsPayload:
    __slots__ = ("hidden", "marker")

    marker: int


@dataclass(frozen=True)
class _ListPayload(list):
    __slots__ = ()


class _ExtensibleTuple(tuple):
    pass


class _ExtensibleInteger(int):
    pass


@dataclass(frozen=True, slots=True)
class _CustomPicklePayload:
    marker: int

    def __reduce__(self):
        return bytes, (b"content",)


class _CustomPickleMarker(Enum):
    ONE = 1

    def __reduce_ex__(self, _protocol):
        return bytes, (b"content",)


class _RecursiveMarker(Enum):
    ONE = 1


def _compute_digest(job: _DigestJob) -> _DigestResult:
    if job.delay_seconds:
        time.sleep(job.delay_seconds)
    if job.exit_code is not None:
        if job.parent_pid == os.getpid():
            global _PARENT_COMPUTE_CALLS
            _PARENT_COMPUTE_CALLS += 1
            raise AssertionError("dead worker job retried inline")
        os._exit(job.exit_code)
    if job.failure is not None:
        raise RuntimeError(job.failure)

    digest = hashlib.sha256()
    with LineBuffer.from_path(
        job.artifact_path,
        spool_dir=job.scratch_directory,
    ) as buffer:
        for chunk in buffer.byte_chunks():
            digest.update(chunk)
        line_count = len(buffer)
    return _DigestResult(
        marker=job.marker,
        digest=digest.hexdigest(),
        line_count=line_count,
        cwd=os.getcwd(),
    )


def _identity(value: int) -> int:
    return value


def _current_directory(_value: int) -> str:
    return os.getcwd()


def _has_open_descriptor_for_path(path: Path) -> bool:
    expected_path = path.resolve()
    for descriptor_path in Path("/proc/self/fd").iterdir():
        try:
            if descriptor_path.resolve() == expected_path:
                return True
        except OSError:
            continue
    return False


def _interrupt_compute(_value: int) -> int:
    raise KeyboardInterrupt


def _large_error_compute(_value: int) -> int:
    raise RuntimeError("x" * (file_jobs_module._MAX_ERROR_MESSAGE_CHARACTERS * 2))


def _oversized_transport_string():
    return "x" * (file_jobs_module._MAX_TRANSPORT_STRING_CHARACTERS + 1)


def _oversized_transport_integer():
    return 1 << (file_jobs_module._MAX_TRANSPORT_INTEGER_BITS + 1)


def _oversized_transport_tuple():
    return tuple(range(file_jobs_module._MAX_TRANSPORT_TUPLE_ITEMS + 1))


def _transport_value_with_too_many_strings():
    string_count = (
        file_jobs_module._MAX_TRANSPORT_TOTAL_STRING_CHARACTERS
        // file_jobs_module._MAX_TRANSPORT_STRING_CHARACTERS
        + 1
    )
    return tuple(
        "x" * file_jobs_module._MAX_TRANSPORT_STRING_CHARACTERS
        for _index in range(string_count)
    )


def _transport_value_with_too_many_values():
    tuple_count = (
        file_jobs_module._MAX_TRANSPORT_VALUE_COUNT
        // file_jobs_module._MAX_TRANSPORT_TUPLE_ITEMS
        + 1
    )
    return tuple(
        tuple(range(file_jobs_module._MAX_TRANSPORT_TUPLE_ITEMS))
        for _index in range(tuple_count)
    )


def _transport_value_with_excessive_nesting():
    value = 0
    for _depth in range(file_jobs_module._MAX_TRANSPORT_NESTING_DEPTH + 1):
        value = (value,)
    return value


def _record_parent_call(value: int) -> int:
    global _PARENT_COMPUTE_CALLS
    _PARENT_COMPUTE_CALLS += 1
    return value


def _jobs_for_artifact(
    artifact: Path,
    scratch: Path,
) -> list[OrderedFileJob[_DigestJob]]:
    return [
        OrderedFileJob(
            ordinal=2,
            file_path="two.txt",
            estimated_bytes=20,
            payload=_DigestJob(artifact, scratch, 2, delay_seconds=0.00),
        ),
        OrderedFileJob(
            ordinal=0,
            file_path="zero.txt",
            estimated_bytes=60,
            payload=_DigestJob(artifact, scratch, 0, delay_seconds=0.06),
        ),
        OrderedFileJob(
            ordinal=1,
            file_path="one.txt",
            estimated_bytes=40,
            payload=_DigestJob(artifact, scratch, 1, delay_seconds=0.03),
        ),
    ]


@_PROCESS_TEST
def test_inline_and_forced_process_runs_return_identical_ordered_results(
    tmp_path,
    monkeypatch,
):
    """Reverse task completion must not change ordinal reduction or cwd."""
    repository = tmp_path / "repository"
    repository.mkdir()
    invocation_directory = repository / "nested"
    invocation_directory.mkdir()
    monkeypatch.chdir(invocation_directory)

    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        artifact = workspace.write_buffer(
            0,
            "input.txt",
            (f"line {index}\n".encode() for index in range(2_000)),
        )
        jobs = _jobs_for_artifact(
            artifact,
            workspace.scratch_directory(0),
        )
        inline = run_file_jobs(
            jobs,
            _compute_digest,
            execution=FileJobExecution("inline", 1, "test"),
            repository_root=repository,
        )
        assert Path.cwd() == invocation_directory
        processed = run_file_jobs(
            jobs,
            _compute_digest,
            execution=FileJobExecution("process", 2, "test"),
            repository_root=repository,
        )
        assert Path.cwd() == invocation_directory

    assert processed == inline
    assert [result.marker for result in processed] == [0, 1, 2]
    assert {result.cwd for result in processed} == {str(repository)}


def test_inline_compute_uses_repository_root_and_restores_cwd(
    tmp_path,
    monkeypatch,
):
    """Direct execution should observe the same cwd as a process worker."""
    repository = tmp_path / "repository"
    repository.mkdir()
    invocation_directory = repository / "nested"
    invocation_directory.mkdir()
    monkeypatch.chdir(invocation_directory)

    results = run_file_jobs(
        [OrderedFileJob(0, "file.txt", 1, 1)],
        _current_directory,
        execution=FileJobExecution("inline", 1, "test"),
        repository_root=repository,
    )

    assert results == [str(repository)]
    assert Path.cwd() == invocation_directory


def test_validated_runner_pairs_results_in_ordinal_order(tmp_path, monkeypatch):
    """The shared pipeline should retain ordering and domain validation."""
    monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "1")
    observed = []

    def run_jobs(jobs, compute, *, execution, repository_root):
        assert [job.ordinal for job in jobs] == [1, 0]
        assert compute is _identity
        assert execution.transport == "inline"
        assert repository_root == tmp_path
        return ["zero", "one"]

    def validate(payload, result):
        observed.append((payload, result))

    jobs = [
        OrderedFileJob(1, "one.txt", 1, "one payload"),
        OrderedFileJob(0, "zero.txt", 1, "zero payload"),
    ]

    paired = run_validated_file_jobs(
        jobs,
        _identity,
        validate,
        repository_root=tmp_path,
        result_label="test jobs",
        run_jobs=run_jobs,
    )

    assert [(job.ordinal, result) for job, result in paired] == [
        (0, "zero"),
        (1, "one"),
    ]
    assert observed == [
        ("zero payload", "zero"),
        ("one payload", "one"),
    ]


def test_validated_runner_rejects_an_unexpected_result_count(
    tmp_path,
    monkeypatch,
):
    """A broken runner must not silently truncate job/result pairing."""
    monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "1")

    with pytest.raises(RuntimeError, match="unexpected result count"):
        run_validated_file_jobs(
            [OrderedFileJob(0, "file.txt", 1, "payload")],
            _identity,
            lambda _payload, _result: None,
            repository_root=tmp_path,
            result_label="test jobs",
            run_jobs=lambda *_args, **_kwargs: [],
        )


@_PROCESS_TEST
def test_forkserver_worker_does_not_inherit_the_parent_session_lock(
    tmp_path,
    monkeypatch,
):
    """A status worker must not keep the repository lock descriptor alive."""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(repository)

    with acquire_session_lock():
        lock_path = get_session_lock_file_path()
        assert _has_open_descriptor_for_path(lock_path)
        worker_observations = run_file_jobs(
            [OrderedFileJob(0, "file.txt", 1, lock_path)],
            _has_open_descriptor_for_path,
            execution=FileJobExecution("process", 1, "test"),
            repository_root=repository,
        )

    assert worker_observations == [False]


@_PROCESS_TEST
def test_compute_keyboard_interrupt_propagates_across_process_transport(
    tmp_path,
):
    """A compute interruption should retain inline control-flow semantics."""
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        root = workspace.root
        with pytest.raises(KeyboardInterrupt):
            run_file_jobs(
                [OrderedFileJob(0, "file.txt", 1, 1)],
                _interrupt_compute,
                execution=FileJobExecution("process", 1, "test"),
                repository_root=tmp_path,
            )

    assert not root.exists()


def test_process_scheduler_submits_largest_first_with_bounded_pending(
    tmp_path,
    monkeypatch,
):
    """Admission priority and pending bounds should be independent of reduction."""
    submitted = []
    max_pending = 0
    lifecycle = {"closed": False, "terminated": False}

    class FakeSupervisor:
        def __init__(self, _compute, *, max_workers, repository_root):
            self.capacity = max_workers * 2
            self.pending = []
            assert repository_root == tmp_path.resolve()

        @property
        def pending_count(self):
            return len(self.pending)

        @property
        def can_submit(self):
            return len(self.pending) < self.capacity

        def submit(self, job):
            nonlocal max_pending
            submitted.append(job.ordinal)
            self.pending.append(job)
            max_pending = max(max_pending, len(self.pending))

        def receive(self):
            job = self.pending.pop()
            return file_jobs_module._WorkerResponse(
                job.ordinal,
                result=job.payload,
            )

        def close(self):
            lifecycle["closed"] = True

        def terminate(self):
            lifecycle["terminated"] = True

    monkeypatch.setattr(
        file_jobs_module,
        "_ProcessFileJobSupervisor",
        FakeSupervisor,
    )
    jobs = [
        OrderedFileJob(ordinal, f"{ordinal}.txt", size, ordinal)
        for ordinal, size in enumerate((10, 80, 30, 80, 20, 50))
    ]

    results = run_file_jobs(
        jobs,
        _identity,
        execution=FileJobExecution("process", 2, "test"),
        repository_root=tmp_path,
    )

    assert submitted == [1, 3, 5, 2, 4, 0]
    assert max_pending <= 4
    assert results == [0, 1, 2, 3, 4, 5]
    assert lifecycle == {"closed": True, "terminated": False}


def test_process_scheduler_stops_admission_after_task_failure(
    tmp_path,
    monkeypatch,
):
    """A task failure should terminate pending and unadmitted work."""
    submitted = []
    lifecycle = {"closed": False, "terminated": False}

    class FailingSupervisor:
        def __init__(self, _compute, *, max_workers, repository_root):
            self.capacity = max_workers
            self.pending = []
            self.received = 0

        @property
        def pending_count(self):
            return len(self.pending)

        @property
        def can_submit(self):
            return len(self.pending) < self.capacity

        def submit(self, job):
            submitted.append(job.ordinal)
            self.pending.append(job)

        def receive(self):
            self.received += 1
            job = self.pending.pop(0)
            if self.received == 1:
                return file_jobs_module._WorkerResponse(
                    job.ordinal,
                    error_type="builtins.RuntimeError",
                    error_message="failed",
                )
            return file_jobs_module._WorkerResponse(
                job.ordinal,
                result=job.payload,
            )

        def close(self):
            lifecycle["closed"] = True

        def terminate(self):
            self.pending.clear()
            lifecycle["terminated"] = True

    monkeypatch.setattr(
        file_jobs_module,
        "_ProcessFileJobSupervisor",
        FailingSupervisor,
    )
    jobs = [
        OrderedFileJob(ordinal, f"{ordinal}.txt", 40 - ordinal, ordinal)
        for ordinal in range(4)
    ]

    with pytest.raises(FileJobError) as error:
        run_file_jobs(
            jobs,
            _identity,
            execution=FileJobExecution("process", 2, "test"),
            repository_root=tmp_path,
        )

    assert error.value.ordinal == 0
    assert submitted == [0, 1]
    assert lifecycle == {"closed": False, "terminated": True}

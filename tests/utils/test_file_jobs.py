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


def test_process_failure_waits_only_for_pending_lower_ordinals(
    tmp_path,
    monkeypatch,
):
    """Deterministic failure selection should not drain unrelated work."""
    received = []
    lifecycle = {"closed": False, "terminated": False}

    class FailingSupervisor:
        def __init__(self, _compute, *, max_workers, repository_root):
            self.pending = []

        @property
        def pending_count(self):
            return len(self.pending)

        @property
        def can_submit(self):
            return len(self.pending) < 3

        def submit(self, job):
            self.pending.append(job)

        def receive(self):
            if not received:
                job = next(job for job in self.pending if job.ordinal == 2)
                self.pending.remove(job)
                received.append(job.ordinal)
                return file_jobs_module._WorkerResponse(
                    job.ordinal,
                    error_type="builtins.RuntimeError",
                    error_message="two failed",
                )
            job = next(job for job in self.pending if job.ordinal == 1)
            self.pending.remove(job)
            received.append(job.ordinal)
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
        OrderedFileJob(1, "one.txt", 20, 1),
        OrderedFileJob(2, "two.txt", 30, 2),
        OrderedFileJob(3, "three.txt", 10, 3),
    ]

    with pytest.raises(FileJobError) as error:
        run_file_jobs(
            jobs,
            _identity,
            execution=FileJobExecution("process", 2, "test"),
            repository_root=tmp_path,
        )

    assert error.value.ordinal == 2
    assert received == [2, 1]
    assert lifecycle == {"closed": False, "terminated": True}


@_PROCESS_TEST
def test_lowest_ordinal_failure_wins(tmp_path):
    """Several task failures should reduce to the lowest input ordinal."""
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        artifact = workspace.write_buffer(0, "input.txt", (b"line\n",))
        scratch = workspace.scratch_directory(0)
        jobs = [
            OrderedFileJob(
                4,
                "four.txt",
                30,
                _DigestJob(artifact, scratch, 4, failure="four"),
            ),
            OrderedFileJob(
                1,
                "one.txt",
                10,
                _DigestJob(
                    artifact,
                    scratch,
                    1,
                    delay_seconds=0.04,
                    failure="one",
                ),
            ),
            OrderedFileJob(
                3,
                "three.txt",
                20,
                _DigestJob(artifact, scratch, 3, failure="three"),
            ),
        ]

        with pytest.raises(FileJobError) as error:
            run_file_jobs(
                jobs,
                _compute_digest,
                execution=FileJobExecution("process", 2, "test"),
                repository_root=tmp_path,
            )

    assert error.value.ordinal == 1
    assert error.value.file_path == "one.txt"
    assert "one" in error.value.original_message


def test_process_construction_failure_does_not_compute_inline(
    tmp_path,
    monkeypatch,
):
    """Supervisor startup errors must not switch to the inline transport."""
    global _PARENT_COMPUTE_CALLS
    _PARENT_COMPUTE_CALLS = 0

    monkeypatch.setattr(
        file_jobs_module,
        "_get_process_context",
        lambda *_args: (_ for _ in ()).throw(OSError("cannot start forkserver")),
    )

    with pytest.raises(FileJobError, match="cannot start forkserver"):
        run_file_jobs(
            [OrderedFileJob(0, "file.txt", 1, 7)],
            _record_parent_call,
            execution=FileJobExecution("process", 2, "test"),
            repository_root=tmp_path,
        )

    assert _PARENT_COMPUTE_CALLS == 0


def test_partially_started_worker_is_killed_and_connections_are_closed(
    tmp_path,
    monkeypatch,
):
    """A failed process start must not leave an unregistered child alive."""
    lifecycle = []

    class FakeConnection:
        def __init__(self, label):
            self.label = label

        def close(self):
            lifecycle.append(f"close {self.label}")

    class FakeProcess:
        pid = 123
        name = "partial-worker"
        exitcode = None

        def __init__(self):
            self.alive = True

        def start(self):
            raise OSError("start failed after launch")

        def is_alive(self):
            return self.alive

        def terminate(self):
            lifecycle.append("terminate")

        def kill(self):
            lifecycle.append("kill")
            self.alive = False

        def join(self, timeout=None):
            lifecycle.append(("join", timeout))

    class FakeContext:
        def Pipe(self, *, duplex):
            assert duplex is True
            return FakeConnection("parent"), FakeConnection("child")

        def Process(self, **_kwargs):
            return FakeProcess()

    monkeypatch.setattr(
        file_jobs_module,
        "_get_process_context",
        lambda: FakeContext(),
    )

    with pytest.raises(FileJobError, match="start failed after launch"):
        run_file_jobs(
            [OrderedFileJob(0, "file.txt", 1, 1)],
            _identity,
            execution=FileJobExecution("process", 1, "test"),
            repository_root=tmp_path,
        )

    assert lifecycle == [
        "terminate",
        ("join", 5.0),
        "kill",
        ("join", 5.0),
        "close parent",
        "close child",
    ]


def test_worker_initialization_failure_exits_abnormally(tmp_path, monkeypatch):
    """An initializer bug must not look like a clean worker shutdown."""

    class FakeConnection:
        def close(self):
            return None

    monkeypatch.setattr(
        file_jobs_module,
        "_initialize_file_job_worker",
        lambda _root: (_ for _ in ()).throw(OSError("cannot enter root")),
    )

    with pytest.raises(OSError, match="cannot enter root"):
        file_jobs_module._file_job_worker(
            FakeConnection(),
            _identity,
            tmp_path,
        )


@_PROCESS_TEST
def test_worker_death_does_not_retry_inline(tmp_path):
    """A dead worker should fail the selected transport without fallback."""
    global _PARENT_COMPUTE_CALLS
    _PARENT_COMPUTE_CALLS = 0

    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        artifact = workspace.write_buffer(0, "input.txt", (b"line\n",))
        job = OrderedFileJob(
            0,
            "file.txt",
            1,
            _DigestJob(
                artifact,
                workspace.scratch_directory(0),
                0,
                exit_code=7,
                parent_pid=os.getpid(),
            ),
        )

        with pytest.raises(FileJobError, match="worker"):
            run_file_jobs(
                [job],
                _compute_digest,
                execution=FileJobExecution("process", 1, "test"),
                repository_root=tmp_path,
            )

    assert _PARENT_COMPUTE_CALLS == 0


def test_keyboard_interrupt_terminates_workers_before_workspace_cleanup(
    tmp_path,
    monkeypatch,
):
    """Parent interruption should terminate transport before artifacts vanish."""
    lifecycle = {"terminated": False}

    class InterruptingSupervisor:
        def __init__(self, *_args, **_kwargs):
            self.pending = 0

        @property
        def pending_count(self):
            return self.pending

        @property
        def can_submit(self):
            return self.pending == 0

        def submit(self, _job):
            self.pending = 1

        def receive(self):
            raise KeyboardInterrupt

        def close(self):
            raise AssertionError("interrupted supervisor closed normally")

        def terminate(self):
            lifecycle["terminated"] = True

    monkeypatch.setattr(
        file_jobs_module,
        "_ProcessFileJobSupervisor",
        InterruptingSupervisor,
    )

    with pytest.raises(KeyboardInterrupt):
        with FileJobWorkspace(parent_directory=tmp_path) as workspace:
            root = workspace.root
            run_file_jobs(
                [OrderedFileJob(0, "file.txt", 1, 1)],
                _identity,
                execution=FileJobExecution("process", 1, "test"),
                repository_root=tmp_path,
            )

    assert lifecycle["terminated"] is True
    assert not root.exists()


@pytest.mark.parametrize("requested_jobs", ("", "0", "-1", "many", "1.5"))
def test_invalid_requested_job_values_raise_deterministic_error(requested_jobs):
    """Only auto and positive integer controls should reach selection."""
    with pytest.raises(
        CommandError,
        match=(r"GIT_STAGE_BATCH_JOBS must be 'auto' or a positive integer\."),
    ):
        select_file_job_execution(
            [OrderedFileJob(0, "file.txt", 1, None)],
            requested_jobs=requested_jobs,
            platform="linux",
            cpu_count=8,
        )


@pytest.mark.parametrize("requested_jobs", (None, "auto", "1"))
def test_non_linux_inline_selection_never_creates_context(
    tmp_path,
    monkeypatch,
    requested_jobs,
):
    """Darwin auto and inline controls must not touch multiprocessing."""
    monkeypatch.setattr(
        file_jobs_module,
        "_get_process_context",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("multiprocessing context requested")
        ),
    )
    jobs = [OrderedFileJob(0, "file.txt", 1, 5)]
    execution = select_file_job_execution(
        jobs,
        requested_jobs=requested_jobs,
        platform="darwin",
        cpu_count=8,
    )

    assert execution.transport == "inline"
    assert run_file_jobs(
        jobs,
        _identity,
        execution=execution,
        repository_root=tmp_path,
    ) == [5]


def test_non_linux_forced_process_selection_fails_before_execution():
    """Darwin must reject a forced process count instead of falling back."""
    with pytest.raises(
        CommandError,
        match="requires Linux",
    ):
        select_file_job_execution(
            [OrderedFileJob(0, "file.txt", 1, None)],
            requested_jobs="2",
            platform="darwin",
            cpu_count=8,
        )


def test_non_linux_forced_process_selection_fails_without_jobs():
    """A forced non-Linux process request should fail even with no work."""
    with pytest.raises(
        CommandError,
        match="requires Linux",
    ):
        select_file_job_execution(
            [],
            requested_jobs="2",
            platform="darwin",
            cpu_count=8,
        )


def test_linux_auto_keeps_work_just_below_the_round_heuristic_inline():
    """Automatic execution should avoid process startup below 256 KiB."""
    jobs = [
        OrderedFileJob(0, "a.txt", 128 * 1024 - 1, None),
        OrderedFileJob(1, "b.txt", 128 * 1024, None),
    ]

    execution = select_file_job_execution(
        jobs,
        requested_jobs="auto",
        platform="linux",
        cpu_count=8,
    )

    assert execution.transport == "inline"
    assert execution.max_workers == 1
    assert "below the" in execution.reason


def test_linux_auto_selects_processes_at_the_round_heuristic():
    """The 256 KiB heuristic should be inclusive."""
    jobs = [
        OrderedFileJob(0, "a.txt", 128 * 1024, None),
        OrderedFileJob(1, "b.txt", 128 * 1024, None),
    ]

    execution = select_file_job_execution(
        jobs,
        requested_jobs="auto",
        platform="linux",
        cpu_count=8,
    )

    assert execution.transport == "process"
    assert execution.max_workers == 2
    assert "262144 estimated bytes" in execution.reason


def test_linux_auto_selects_bounded_processes_above_the_heuristic():
    """Automatic execution should admit measured multi-file CPU wins."""
    jobs = [
        OrderedFileJob(ordinal, f"{ordinal}.txt", 125_000, None) for ordinal in range(5)
    ]

    execution = select_file_job_execution(
        jobs,
        requested_jobs=None,
        platform="linux",
        cpu_count=8,
    )

    assert execution.transport == "process"
    assert execution.max_workers == 4
    assert "625000 estimated bytes" in execution.reason


def test_linux_auto_respects_cpu_availability():
    """Automatic worker selection should not exceed available CPUs."""
    jobs = [
        OrderedFileJob(ordinal, f"{ordinal}.txt", 250_000, None) for ordinal in range(2)
    ]

    assert (
        select_file_job_execution(
            jobs,
            requested_jobs="auto",
            platform="linux",
            cpu_count=1,
        ).transport
        == "inline"
    )
    assert (
        select_file_job_execution(
            jobs,
            requested_jobs="auto",
            platform="linux",
            cpu_count=2,
        ).max_workers
        == 2
    )


def test_linux_auto_uses_process_affinity_when_cpu_count_is_unspecified(
    monkeypatch,
):
    """Production selection should honor a constrained CPU affinity mask."""
    jobs = [
        OrderedFileJob(ordinal, f"{ordinal}.txt", 125_000, None) for ordinal in range(4)
    ]
    monkeypatch.setattr(
        file_jobs_module.os,
        "sched_getaffinity",
        lambda _process_id: {0, 1},
    )

    execution = select_file_job_execution(
        jobs,
        requested_jobs="auto",
        platform="linux",
        cpu_count=None,
    )

    assert execution.transport == "process"
    assert execution.max_workers == 2


def test_forced_process_selection_respects_job_cpu_and_worker_caps():
    """Forced worker counts should remain bounded by every execution limit."""
    jobs = [OrderedFileJob(ordinal, f"{ordinal}.txt", 1, None) for ordinal in range(10)]

    assert select_file_job_execution(
        jobs,
        requested_jobs="9",
        platform="linux",
        cpu_count=3,
    ) == FileJobExecution(
        "process",
        3,
        "GIT_STAGE_BATCH_JOBS requests 9 processes",
    )


def test_process_execution_enforces_the_hard_worker_cap(
    tmp_path,
    monkeypatch,
):
    """Direct execution records must not bypass the generic worker ceiling."""
    observed_max_workers = []

    class FakeSupervisor:
        def __init__(self, _compute, *, max_workers, repository_root):
            observed_max_workers.append(max_workers)
            self.pending = []

        @property
        def pending_count(self):
            return len(self.pending)

        @property
        def can_submit(self):
            return len(self.pending) < 8

        def submit(self, job):
            self.pending.append(job)

        def receive(self):
            job = self.pending.pop()
            return file_jobs_module._WorkerResponse(
                job.ordinal,
                result=job.payload,
            )

        def close(self):
            return None

        def terminate(self):
            raise AssertionError("successful execution was terminated")

    monkeypatch.setattr(
        file_jobs_module,
        "_ProcessFileJobSupervisor",
        FakeSupervisor,
    )
    jobs = [
        OrderedFileJob(ordinal, f"{ordinal}.txt", 1, ordinal) for ordinal in range(10)
    ]

    results = run_file_jobs(
        jobs,
        _identity,
        execution=FileJobExecution("process", 99, "test"),
        repository_root=tmp_path,
    )

    assert results == list(range(10))
    assert observed_max_workers == [file_jobs_module._MAX_FILE_JOB_WORKERS]

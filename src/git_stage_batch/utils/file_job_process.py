"""Bounded process supervision for file-job execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar


if TYPE_CHECKING:
    from multiprocessing import Process
    from multiprocessing.connection import Connection
    from multiprocessing.context import BaseContext


JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")
_PENDING_JOBS_PER_WORKER = 2
_WORKER_EXIT_TIMEOUT_SECONDS = 5.0


class _OrderedJob(Protocol[JobT]):
    ordinal: int
    payload: JobT


@dataclass(frozen=True, slots=True)
class _WorkerResponse(Generic[ResultT]):
    """One value or failure returned by a file-job worker."""

    ordinal: int
    result: ResultT | None = None
    error_type: str | None = None
    error_message: str | None = None
    interrupted: bool = False


class _FileJobSupervisorError(Exception):
    """Failure of process construction, communication, or worker lifetime."""

    def __init__(self, message: str, *, ordinal: int | None = None) -> None:
        self.ordinal = ordinal
        super().__init__(message)


class _ProcessFileJobSupervisor(Generic[JobT, ResultT]):
    """Small forkserver supervisor with explicit public worker termination."""

    def __init__(
        self,
        compute: Callable[[JobT], ResultT],
        *,
        max_workers: int,
        repository_root: Path,
        worker_target: Callable[..., None],
        context_factory: Callable[[], BaseContext],
    ) -> None:
        self._workers: list[Process] = []
        self._connections: list[Connection] = []
        self._pending_ordinals: list[list[int]] = []
        self._closed = False
        try:
            context = context_factory()
            for worker_index in range(max_workers):
                self._start_worker(
                    context,
                    worker_target,
                    compute,
                    repository_root,
                    worker_index,
                )
        except BaseException:
            self.terminate()
            raise

    @property
    def pending_count(self) -> int:
        return sum(len(ordinals) for ordinals in self._pending_ordinals)

    @property
    def can_submit(self) -> bool:
        return any(
            len(ordinals) < _PENDING_JOBS_PER_WORKER
            for ordinals in self._pending_ordinals
        )

    def submit(self, job: _OrderedJob[JobT]) -> None:
        """Submit one job to the least-loaded live worker."""
        self._require_open()
        self._raise_for_dead_workers()
        worker_index = min(
            (
                index
                for index, ordinals in enumerate(self._pending_ordinals)
                if len(ordinals) < _PENDING_JOBS_PER_WORKER
            ),
            key=lambda index: (len(self._pending_ordinals[index]), index),
        )
        try:
            self._connections[worker_index].send(job)
        except Exception as error:
            raise _FileJobSupervisorError(
                f"could not submit job {job.ordinal}: {error}",
                ordinal=job.ordinal,
            ) from error
        self._pending_ordinals[worker_index].append(job.ordinal)

    def receive(self) -> _WorkerResponse[ResultT]:
        """Wait for one result while detecting unexpected worker exits."""
        from multiprocessing.connection import wait

        self._require_open()
        if not self.pending_count:
            raise _FileJobSupervisorError("no pending file job result")

        while True:
            pending_connections = [
                connection
                for connection, ordinals in zip(
                    self._connections,
                    self._pending_ordinals,
                )
                if ordinals
            ]
            ready_connections = wait(pending_connections, timeout=0.05)
            if ready_connections:
                connection = ready_connections[0]
                worker_index = self._connections.index(connection)
                expected_ordinal = self._pending_ordinals[worker_index].pop(0)
                try:
                    response = connection.recv()
                except Exception as error:
                    raise _FileJobSupervisorError(
                        f"worker exited while running job {expected_ordinal}",
                        ordinal=expected_ordinal,
                    ) from error
                if (
                    not isinstance(response, _WorkerResponse)
                    or response.ordinal != expected_ordinal
                ):
                    raise _FileJobSupervisorError(
                        f"worker returned an invalid result for job {expected_ordinal}",
                        ordinal=expected_ordinal,
                    )
                return response
            self._raise_for_dead_workers()

    def close(self) -> None:
        """Finish workers normally and wait for every process to exit."""
        if self._closed:
            return
        if self.pending_count:
            raise _FileJobSupervisorError(
                "cannot close file-job workers with pending work"
            )
        try:
            for connection, process in zip(
                self._connections,
                self._workers,
            ):
                if process.exitcode is None:
                    connection.send(None)
            for process in self._workers:
                process.join(_WORKER_EXIT_TIMEOUT_SECONDS)
                if process.is_alive():
                    raise _FileJobSupervisorError(f"worker {process.name} did not exit")
                if process.exitcode != 0:
                    raise _FileJobSupervisorError(
                        f"worker {process.name} exited with status {process.exitcode}"
                    )
        except BaseException:
            self.terminate()
            raise
        self._close_connections()
        self._closed = True

    def terminate(self) -> None:
        """Terminate running workers and wait before returning."""
        if self._closed:
            return
        try:
            for process in self._workers:
                try:
                    if process.is_alive():
                        process.terminate()
                except BaseException:
                    pass
            for process in self._workers:
                try:
                    process.join(_WORKER_EXIT_TIMEOUT_SECONDS)
                except BaseException:
                    pass
            for process in self._workers:
                try:
                    if process.is_alive():
                        process.kill()
                except BaseException:
                    pass
            for process in self._workers:
                try:
                    if process.is_alive():
                        process.join()
                except BaseException:
                    pass
        finally:
            self._close_connections()
            self._closed = True

    def _start_worker(
        self,
        context: BaseContext,
        worker_target: Callable[..., None],
        compute: Callable[[JobT], ResultT],
        repository_root: Path,
        worker_index: int,
    ) -> None:
        parent_connection: Connection | None = None
        child_connection: Connection | None = None
        process: Process | None = None
        try:
            parent_connection, child_connection = context.Pipe(duplex=True)
            process = context.Process(
                target=worker_target,
                args=(child_connection, compute, repository_root),
                name=f"git-stage-batch-file-job-{worker_index}",
            )
            process.start()
        except BaseException:
            self._stop_partially_started_worker(process)
            if parent_connection is not None:
                try:
                    parent_connection.close()
                except BaseException:
                    pass
            raise
        finally:
            if child_connection is not None:
                try:
                    child_connection.close()
                except BaseException:
                    pass
        assert process is not None
        assert parent_connection is not None
        self._workers.append(process)
        self._connections.append(parent_connection)
        self._pending_ordinals.append([])

    def _stop_partially_started_worker(self, process: Process | None) -> None:
        if process is None:
            return
        try:
            if process.pid is not None and process.is_alive():
                process.terminate()
        except BaseException:
            pass
        try:
            if process.pid is not None:
                process.join(_WORKER_EXIT_TIMEOUT_SECONDS)
        except BaseException:
            pass
        try:
            if process.is_alive():
                process.kill()
        except BaseException:
            pass
        try:
            if process.pid is not None:
                process.join(_WORKER_EXIT_TIMEOUT_SECONDS)
        except BaseException:
            pass

    def _raise_for_dead_workers(self) -> None:
        for worker_index, process in enumerate(self._workers):
            if process.exitcode is not None:
                pending_ordinals = self._pending_ordinals[worker_index]
                raise _FileJobSupervisorError(
                    f"worker {process.name} exited with status {process.exitcode}",
                    ordinal=pending_ordinals[0] if pending_ordinals else None,
                )

    def _close_connections(self) -> None:
        for connection in self._connections:
            try:
                connection.close()
            except BaseException:
                pass

    def _require_open(self) -> None:
        if self._closed:
            raise _FileJobSupervisorError("file-job supervisor is closed")

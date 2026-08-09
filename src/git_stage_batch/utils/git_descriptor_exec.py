"""Run Git against Darwin directories pinned by inherited descriptors."""

from __future__ import annotations

import fcntl
import os
import select
import signal
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn


DARWIN_OBJECT_DIRECTORY_DESCRIPTOR = (
    "GIT_STAGE_BATCH_DARWIN_OBJECT_DIRECTORY_DESCRIPTOR"
)
DARWIN_ALTERNATE_OBJECT_DIRECTORY_DESCRIPTOR = (
    "GIT_STAGE_BATCH_DARWIN_ALTERNATE_OBJECT_DIRECTORY_DESCRIPTOR"
)
_DARWIN_DESCRIPTOR_PATH_BYTES = 1024
_DESCRIPTOR_CHANGE_EXIT_CODE = 125


def prepare_darwin_descriptor_command(
    arguments: list[str],
    environment: Mapping[str, str] | None,
) -> list[str]:
    """Wrap one Darwin child whose Git object paths come from descriptors."""
    if (
        sys.platform != "darwin"
        or environment is None
        or DARWIN_OBJECT_DIRECTORY_DESCRIPTOR not in environment
    ):
        return arguments
    if not arguments or os.path.basename(arguments[0]) != "git":
        raise ValueError("Darwin object-directory descriptors require a Git command")
    return [
        sys.executable,
        "-B",
        "-m",
        "git_stage_batch.utils.git_descriptor_exec",
        "--",
        *arguments,
    ]


def _descriptor_number(environment: Mapping[str, str], name: str) -> int | None:
    value = environment.get(name)
    if value is None:
        return None
    try:
        descriptor = int(value)
    except ValueError as error:
        raise RuntimeError(f"Invalid inherited directory descriptor: {value}") from error
    if descriptor < 3:
        raise RuntimeError(f"Invalid inherited directory descriptor: {descriptor}")
    return descriptor


def _descriptor_path(descriptor: int) -> str:
    command = getattr(fcntl, "F_GETPATH", None)
    if command is None:
        raise RuntimeError("Darwin descriptor path lookup is unavailable")
    payload = fcntl.fcntl(
        descriptor,
        command,
        b"\0" * _DARWIN_DESCRIPTOR_PATH_BYTES,
    )
    if not isinstance(payload, bytes):
        raise RuntimeError("Darwin descriptor path lookup returned invalid data")
    encoded_path = payload.split(b"\0", 1)[0]
    if not encoded_path:
        raise RuntimeError("Darwin descriptor path lookup returned an empty path")
    path = os.fsdecode(encoded_path)
    opened = os.fstat(descriptor)
    visible = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(visible.st_mode)
        or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
    ):
        raise RuntimeError("The inherited Git object directory identity changed")
    return path


def _open_descriptor_path_chain(path: str, descriptor: int) -> tuple[int, ...]:
    """Pin every namespace component used to reach one descriptor."""
    if not os.path.isabs(path):
        raise RuntimeError("Darwin descriptor path lookup returned a relative path")
    flags = (
        getattr(
            os,
            "O_SEARCH",
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    opened: list[int] = []
    try:
        parent = os.open(os.sep, flags)
        opened.append(parent)
        for component in Path(path).parts[1:]:
            parent = os.open(component, flags, dir_fd=parent)
            opened.append(parent)
        expected = os.fstat(descriptor)
        reached = os.fstat(opened[-1])
        if (
            not stat.S_ISDIR(reached.st_mode)
            or (expected.st_dev, expected.st_ino) != (reached.st_dev, reached.st_ino)
        ):
            raise RuntimeError("The inherited Git object directory identity changed")
        return tuple(opened)
    except BaseException:
        for opened_descriptor in reversed(opened):
            os.close(opened_descriptor)
        raise


def _descriptor_events(descriptors: Sequence[int]) -> list[object]:
    event_flags = (
        getattr(select, "KQ_EV_ADD")
        | getattr(select, "KQ_EV_ENABLE")
        | getattr(select, "KQ_EV_CLEAR")
    )
    note_flags = (
        getattr(select, "KQ_NOTE_DELETE")
        | getattr(select, "KQ_NOTE_RENAME")
        | getattr(select, "KQ_NOTE_REVOKE")
    )
    event = getattr(select, "kevent")
    filter_vnode = getattr(select, "KQ_FILTER_VNODE")
    return [
        event(
            descriptor,
            filter=filter_vnode,
            flags=event_flags,
            fflags=note_flags,
        )
        for descriptor in descriptors
    ]


def _changed_descriptors(queue: object, maximum_events: int, timeout: float) -> bool:
    events = queue.control([], maximum_events, timeout)  # type: ignore[attr-defined]
    return bool(events)


def _terminate_child(process_id: int) -> None:
    try:
        os.kill(process_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    while True:
        try:
            os.waitpid(process_id, 0)
            return
        except InterruptedError:
            continue
        except ChildProcessError:
            return


def _wait_for_child(
    process_id: int,
    queue: object,
    descriptors: Sequence[int],
    watched_descriptor_count: int,
) -> int:
    while True:
        try:
            waited_process, status = os.waitpid(process_id, os.WNOHANG)
        except InterruptedError:
            continue
        if waited_process == process_id:
            if _changed_descriptors(queue, watched_descriptor_count, 0):
                raise RuntimeError("An inherited Git object directory moved")
            for descriptor in descriptors:
                _descriptor_path(descriptor)
            return os.waitstatus_to_exitcode(status)
        if _changed_descriptors(queue, watched_descriptor_count, 0.1):
            _terminate_child(process_id)
            raise RuntimeError("An inherited Git object directory moved")


def _forward_signal(process_id: int, signal_number: int) -> None:
    try:
        os.kill(process_id, signal_number)
    except ProcessLookupError:
        pass


def _run(arguments: list[str], environment: dict[str, str]) -> int:
    object_descriptor = _descriptor_number(
        environment,
        DARWIN_OBJECT_DIRECTORY_DESCRIPTOR,
    )
    if object_descriptor is None:
        raise RuntimeError("The inherited Git object directory is missing")
    alternate_descriptor = _descriptor_number(
        environment,
        DARWIN_ALTERNATE_OBJECT_DIRECTORY_DESCRIPTOR,
    )
    descriptors = tuple(
        descriptor
        for descriptor in (object_descriptor, alternate_descriptor)
        if descriptor is not None
    )
    queue = getattr(select, "kqueue")()
    process_id = 0
    path_descriptors: tuple[int, ...] = ()
    try:
        queue.control(_descriptor_events(descriptors), 0, 0)
        object_path = _descriptor_path(object_descriptor)
        alternate_path = (
            _descriptor_path(alternate_descriptor)
            if alternate_descriptor is not None
            else None
        )
        path_descriptors = _open_descriptor_path_chain(
            object_path,
            object_descriptor,
        )
        if alternate_path is not None and alternate_descriptor is not None:
            path_descriptors += _open_descriptor_path_chain(
                alternate_path,
                alternate_descriptor,
            )
        watched_descriptors = (*descriptors, *path_descriptors)
        queue.control(_descriptor_events(path_descriptors), 0, 0)
        if _changed_descriptors(queue, len(watched_descriptors), 0):
            raise RuntimeError("An inherited Git object directory moved")
        child_environment = environment.copy()
        child_environment.pop(DARWIN_OBJECT_DIRECTORY_DESCRIPTOR, None)
        child_environment.pop(DARWIN_ALTERNATE_OBJECT_DIRECTORY_DESCRIPTOR, None)
        child_environment["GIT_OBJECT_DIRECTORY"] = object_path
        if alternate_path is None:
            child_environment.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
        else:
            child_environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = alternate_path
        child_environment.pop("GIT_QUARANTINE_PATH", None)
        process_id = os.posix_spawnp(
            arguments[0],
            arguments,
            child_environment,
        )

        def forward_signal(received: int, _frame: object) -> None:
            _forward_signal(process_id, received)

        for signal_number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signal_number, forward_signal)
        return _wait_for_child(
            process_id,
            queue,
            descriptors,
            len(watched_descriptors),
        )
    finally:
        queue.close()
        for path_descriptor in reversed(path_descriptors):
            os.close(path_descriptor)


def _fail(message: str) -> NoReturn:
    print(f"git-stage-batch: {message}", file=sys.stderr)
    raise SystemExit(_DESCRIPTOR_CHANGE_EXIT_CODE)


def main() -> int:
    """Run the wrapped Git command and monitor both directory identities."""
    if sys.platform != "darwin":
        _fail("the Darwin descriptor helper ran on an unsupported platform")
    try:
        separator = sys.argv.index("--", 1)
    except ValueError:
        _fail("the Darwin descriptor helper received no Git command")
    arguments = sys.argv[separator + 1 :]
    if not arguments or os.path.basename(arguments[0]) != "git":
        _fail("the Darwin descriptor helper received an invalid Git command")
    try:
        return _run(arguments, os.environ.copy())
    except (OSError, RuntimeError, ValueError) as error:
        _fail(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

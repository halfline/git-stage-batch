"""Git ref update helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import subprocess

from .git_command import run_git_command, stream_git_command
from .git_repository import null_object_id


def _symbolic_expected_absence_error(
    ref_name: str,
) -> subprocess.CalledProcessError:
    """Return a Git-style error for an exact ref that became symbolic."""
    return subprocess.CalledProcessError(
        128,
        ["git", "update-ref", "--no-deref", "--stdin"],
        stderr=(
            f"fatal: cannot update ref '{ref_name}': "
            "the expected absent ref is symbolic\n"
        ),
    )


def _reject_symbolic_expected_absence(
    ref_names: Iterable[str],
    expected_old_values: Mapping[str, str | None],
) -> None:
    """Reject symbolic refs that older Git treats as absent with --no-deref."""
    checked: set[str] = set()
    for ref_name in ref_names:
        if (
            ref_name in checked
            or ref_name not in expected_old_values
            or expected_old_values[ref_name] is not None
        ):
            continue
        checked.add(ref_name)
        symbolic = run_git_command(
            ["symbolic-ref", "--quiet", ref_name],
            check=False,
            requires_index_lock=False,
        )
        if symbolic.returncode == 0:
            raise _symbolic_expected_absence_error(ref_name)


def _git_ref_exists(ref_name: str, *, no_deref: bool) -> bool:
    if no_deref:
        symbolic = run_git_command(
            ["symbolic-ref", "--quiet", ref_name],
            check=False,
            requires_index_lock=False,
        )
        if symbolic.returncode == 0:
            return True
    result = run_git_command(
        ["rev-parse", "--verify", ref_name],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 0


def update_git_refs(
    *,
    updates: Iterable[tuple[str, str]] = (),
    deletes: Iterable[str] = (),
    ignore_missing_deletes: bool = True,
    expected_old_values: Mapping[str, str | None] | None = None,
    durable: bool = False,
    no_deref: bool = False,
) -> None:
    """Update one or more Git refs in a single update-ref transaction.

    ``durable`` requests an fsync-backed reference publication boundary for
    callers whose own durable checkpoints depend on the ref surviving an
    unclean shutdown.

    ``no_deref`` updates each named ref itself, so a symbolic ref cannot
    redirect an exact-name compare-and-swap to another ref.
    """
    update_commands = list(updates)
    delete_commands = list(deletes)
    if ignore_missing_deletes:
        delete_commands = [
            ref_name
            for ref_name in delete_commands
            if _git_ref_exists(ref_name, no_deref=no_deref)
        ]
    if not update_commands and not delete_commands:
        return

    commands = ["start"]
    expected = expected_old_values or {}
    if no_deref:
        _reject_symbolic_expected_absence(
            (
                *(ref_name for ref_name, _object_name in update_commands),
                *delete_commands,
            ),
            expected,
        )
    commands.extend(
        " ".join(
            part
            for part in (
                "update",
                ref_name,
                object_name,
                (
                    expected[ref_name] or null_object_id()
                    if ref_name in expected
                    else ""
                ),
            )
            if part
        )
        for ref_name, object_name in update_commands
    )
    commands.extend(
        " ".join(
            part
            for part in (
                "delete",
                ref_name,
                (
                    expected[ref_name] or null_object_id()
                    if ref_name in expected
                    else ""
                ),
            )
            if part
        )
        for ref_name in delete_commands
    )
    commands.extend(["prepare", "commit"])
    payload = ("\n".join(commands) + "\n").encode("utf-8")
    arguments = ["update-ref"]
    if no_deref:
        arguments.append("--no-deref")
    arguments.append("--stdin")
    if durable:
        arguments = [
            "-c",
            "core.fsync=reference",
            "-c",
            "core.fsyncMethod=fsync",
            *arguments,
        ]
    for _chunk in stream_git_command(
        arguments,
        [payload],
        requires_index_lock=False,
    ):
        pass

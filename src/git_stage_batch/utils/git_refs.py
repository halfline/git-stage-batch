"""Git ref update helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .git_command import run_git_command, stream_git_command
from .git_repository import null_object_id


def _git_ref_exists(ref_name: str) -> bool:
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
) -> None:
    """Update one or more Git refs in a single update-ref transaction."""
    update_commands = list(updates)
    delete_commands = list(deletes)
    if ignore_missing_deletes:
        delete_commands = [
            ref_name for ref_name in delete_commands if _git_ref_exists(ref_name)
        ]
    if not update_commands and not delete_commands:
        return

    commands = ["start"]
    expected = expected_old_values or {}
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
    for _chunk in stream_git_command(
        ["update-ref", "--stdin"],
        [payload],
        requires_index_lock=False,
    ):
        pass

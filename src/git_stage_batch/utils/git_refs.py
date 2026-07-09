"""Git ref update helpers."""

from __future__ import annotations

from collections.abc import Iterable

from .git_command import run_git_command, stream_git_command


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
    commands.extend(
        f"update {ref_name} {object_name}"
        for ref_name, object_name in update_commands
    )
    commands.extend(f"delete {ref_name}" for ref_name in delete_commands)
    commands.extend(["prepare", "commit"])
    payload = ("\n".join(commands) + "\n").encode("utf-8")
    for _chunk in stream_git_command(
        ["update-ref", "--stdin"],
        [payload],
        requires_index_lock=False,
    ):
        pass

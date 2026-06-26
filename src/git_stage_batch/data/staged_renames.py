"""Session handling for staged renames normalized into the workflow."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import (
    GitIndexEntryUpdate,
    get_git_repository_root_path,
    git_reset_paths,
    git_update_index_entries,
    run_git_command,
)
from ..utils.journal import log_journal
from ..utils.paths import (
    get_abort_head_file_path,
    get_staged_deletions_file_path,
    get_staged_renames_file_path,
)


@dataclass(frozen=True)
class StagedChangeRecord:
    """One `git diff --name-status` record from the staged diff."""

    status: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class StagedRename:
    """A start-time staged rename and its exact staged destination entry."""

    old_path: str
    new_path: str
    new_mode: str
    new_blob: str


@dataclass(frozen=True)
class StagedDeletion:
    """A start-time staged text deletion and its original HEAD entry."""

    path: str
    old_mode: str
    old_blob: str


def _parse_name_status_z(data: bytes) -> list[StagedChangeRecord]:
    """Parse `git diff --name-status -z` records."""
    fields = data.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()

    records: list[StagedChangeRecord] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii", errors="replace")
        index += 1

        path_count = 2 if status.startswith(("R", "C")) else 1
        if index + path_count > len(fields):
            break
        paths = tuple(
            field.decode("utf-8", errors="surrogateescape")
            for field in fields[index:index + path_count]
        )
        index += path_count
        records.append(StagedChangeRecord(status=status, paths=paths))

    return records


def list_staged_change_records() -> list[StagedChangeRecord]:
    """Return staged change records using Git's normal rename detection."""
    result = run_git_command(
        ["diff", "--cached", "--name-status", "-M", "-z"],
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0 or not result.stdout:
        return []
    return _parse_name_status_z(result.stdout)


def list_normalizable_staged_renames() -> list[StagedRename]:
    """Return start-time staged renames that can be normalized."""
    staged_renames: list[StagedRename] = []
    for record in list_staged_change_records():
        if not record.status.startswith("R") or len(record.paths) != 2:
            continue
        old_path, new_path = record.paths
        entry = _index_entry_for_path(new_path)
        if entry is None:
            continue
        new_mode, new_blob = entry
        staged_renames.append(
            StagedRename(
                old_path=old_path,
                new_path=new_path,
                new_mode=new_mode,
                new_blob=new_blob,
            )
        )

    return staged_renames


def _head_entry_for_path(file_path: str) -> tuple[str, str] | None:
    result = run_git_command(
        ["ls-tree", "-z", "HEAD", "--", file_path],
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None

    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        if path_bytes.decode("utf-8", errors="surrogateescape") != file_path:
            continue
        parts = metadata.split()
        if len(parts) < 3 or parts[1] != b"blob":
            continue
        return (
            parts[0].decode("ascii", errors="replace"),
            parts[2].decode("ascii", errors="replace"),
        )
    return None


def _staged_deletion_is_text(file_path: str) -> bool:
    result = run_git_command(
        ["diff", "--cached", "--numstat", "-z", "--", file_path],
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0 or not result.stdout:
        return False

    record = result.stdout.split(b"\0", 1)[0]
    parts = record.split(b"\t", 2)
    return len(parts) == 3 and parts[0] != b"-" and parts[1] != b"-"


def list_normalizable_staged_deletions() -> list[StagedDeletion]:
    """Return start-time staged text deletions that can be normalized."""
    repo_root = get_git_repository_root_path()
    staged_deletions: list[StagedDeletion] = []
    for record in list_staged_change_records():
        if record.status != "D" or len(record.paths) != 1:
            continue
        file_path = record.paths[0]
        if os.path.lexists(repo_root / file_path):
            continue
        if not _staged_deletion_is_text(file_path):
            continue
        entry = _head_entry_for_path(file_path)
        if entry is None:
            continue
        old_mode, old_blob = entry
        if old_mode not in {"100644", "100755"}:
            continue
        staged_deletions.append(
            StagedDeletion(
                path=file_path,
                old_mode=old_mode,
                old_blob=old_blob,
            )
        )

    return staged_deletions


def staged_changes_are_only_normalizable_start_time_changes() -> bool:
    """Return whether all staged changes are start-time changes start can normalize."""
    records = list_staged_change_records()
    if not records:
        return False
    rename_records = [record for record in records if record.status.startswith("R") and len(record.paths) == 2]
    deletion_records = [record for record in records if record.status == "D" and len(record.paths) == 1]
    if len(rename_records) + len(deletion_records) != len(records):
        return False
    return (
        len(list_normalizable_staged_renames()) == len(rename_records)
        and len(list_normalizable_staged_deletions()) == len(deletion_records)
    )


def staged_changes_are_only_normalizable_renames() -> bool:
    """Return whether all staged changes are renames start can normalize."""
    records = list_staged_change_records()
    if not records:
        return False
    rename_records = [record for record in records if record.status.startswith("R") and len(record.paths) == 2]
    if len(rename_records) != len(records):
        return False
    return len(list_normalizable_staged_renames()) == len(rename_records)


def _index_entry_for_path(file_path: str) -> tuple[str, str] | None:
    result = run_git_command(
        ["ls-files", "--stage", "-z", "--", file_path],
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None

    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        if path_bytes.decode("utf-8", errors="surrogateescape") != file_path:
            continue
        parts = metadata.split()
        if len(parts) < 3 or parts[2] != b"0":
            continue
        return (
            parts[0].decode("ascii", errors="replace"),
            parts[1].decode("ascii", errors="replace"),
        )
    return None


def _serialize_renames(renames: list[StagedRename]) -> str:
    return json.dumps(
        [
            {
                "old_path": rename.old_path,
                "new_path": rename.new_path,
                "new_mode": rename.new_mode,
                "new_blob": rename.new_blob,
            }
            for rename in renames
        ],
        ensure_ascii=False,
        indent=0,
    )


def _serialize_deletions(deletions: list[StagedDeletion]) -> str:
    return json.dumps(
        [
            {
                "path": deletion.path,
                "old_mode": deletion.old_mode,
                "old_blob": deletion.old_blob,
            }
            for deletion in deletions
        ],
        ensure_ascii=False,
        indent=0,
    )


def read_staged_renames() -> list[StagedRename]:
    """Read start-time staged rename metadata."""
    path = get_staged_renames_file_path()
    if not path.exists():
        return []

    try:
        data = json.loads(read_text_file_contents(path))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    renames: list[StagedRename] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        old_path = item.get("old_path")
        new_path = item.get("new_path")
        new_mode = item.get("new_mode")
        new_blob = item.get("new_blob")
        if not all(isinstance(value, str) for value in (old_path, new_path, new_mode, new_blob)):
            continue
        renames.append(
            StagedRename(
                old_path=old_path,
                new_path=new_path,
                new_mode=new_mode,
                new_blob=new_blob,
            )
        )
    return renames


def read_staged_deletions() -> list[StagedDeletion]:
    """Read start-time staged text deletion metadata."""
    path = get_staged_deletions_file_path()
    if not path.exists():
        return []

    try:
        data = json.loads(read_text_file_contents(path))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    deletions: list[StagedDeletion] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        file_path = item.get("path")
        old_mode = item.get("old_mode")
        old_blob = item.get("old_blob")
        if not all(isinstance(value, str) for value in (file_path, old_mode, old_blob)):
            continue
        deletions.append(
            StagedDeletion(
                path=file_path,
                old_mode=old_mode,
                old_blob=old_blob,
            )
        )
    return deletions


def normalize_start_time_staged_renames() -> list[StagedRename]:
    """Move staged renames into the unstaged workflow for the active session.

    The abort stash is created before this function runs, so abort can restore
    the exact start state. Stop restoration uses the metadata recorded here
    when a normalized rename was never staged during the session.
    """
    staged_renames = list_normalizable_staged_renames()
    if not staged_renames:
        return []

    write_text_file_contents(get_staged_renames_file_path(), _serialize_renames(staged_renames))

    paths: list[str] = []
    for rename in staged_renames:
        paths.extend([rename.old_path, rename.new_path])

    log_journal(
        "session_normalizing_staged_renames",
        renames=[
            {
                "old_path": rename.old_path,
                "new_path": rename.new_path,
                "new_mode": rename.new_mode,
                "new_blob": rename.new_blob,
            }
            for rename in staged_renames
        ],
    )
    git_reset_paths(list(dict.fromkeys(paths)), check=False)
    return staged_renames


def normalize_start_time_staged_deletions() -> list[StagedDeletion]:
    """Move staged text deletions into the unstaged workflow for the active session."""
    staged_deletions = list_normalizable_staged_deletions()
    if not staged_deletions:
        return []

    write_text_file_contents(get_staged_deletions_file_path(), _serialize_deletions(staged_deletions))

    log_journal(
        "session_normalizing_staged_deletions",
        deletions=[
            {
                "path": deletion.path,
                "old_mode": deletion.old_mode,
                "old_blob": deletion.old_blob,
            }
            for deletion in staged_deletions
        ],
    )
    git_reset_paths(list(dict.fromkeys(deletion.path for deletion in staged_deletions)), check=False)
    return staged_deletions


def _paths_have_cached_diff(paths: list[str]) -> bool:
    result = run_git_command(
        ["diff", "--cached", "--quiet", "--no-renames", "--", *paths],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 1


def _head_changed_since_session_start(paths: list[str]) -> bool:
    abort_head_path = get_abort_head_file_path()
    if not abort_head_path.exists():
        return False

    abort_head = read_text_file_contents(abort_head_path).strip()
    if not abort_head:
        return False

    result = run_git_command(
        ["diff", "--quiet", abort_head, "HEAD", "--", *paths],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 1


def restore_unstaged_start_time_renames() -> list[StagedRename]:
    """Restore normalized start-time renames that were not staged by the user."""
    renames = read_staged_renames()
    if not renames:
        return []

    restored: list[StagedRename] = []
    updates: list[GitIndexEntryUpdate] = []
    for rename in renames:
        paths = [rename.old_path, rename.new_path]
        if _paths_have_cached_diff(paths):
            continue
        if _head_changed_since_session_start(paths):
            continue
        updates.extend(
            [
                GitIndexEntryUpdate(file_path=rename.old_path, force_remove=True),
                GitIndexEntryUpdate(
                    file_path=rename.new_path,
                    mode=rename.new_mode,
                    blob_sha=rename.new_blob,
                ),
            ]
        )
        restored.append(rename)

    if updates:
        git_update_index_entries(updates)
        log_journal(
            "session_restored_unstaged_start_renames",
            renames=[
                {
                    "old_path": rename.old_path,
                    "new_path": rename.new_path,
                    "new_mode": rename.new_mode,
                    "new_blob": rename.new_blob,
                }
                for rename in restored
            ],
        )

    return restored


def restore_unstaged_start_time_deletions() -> list[StagedDeletion]:
    """Restore normalized start-time deletions that were not changed by the user."""
    deletions = read_staged_deletions()
    if not deletions:
        return []

    repo_root = get_git_repository_root_path()
    restored: list[StagedDeletion] = []
    updates: list[GitIndexEntryUpdate] = []
    for deletion in deletions:
        if os.path.lexists(repo_root / deletion.path):
            continue
        if _paths_have_cached_diff([deletion.path]):
            continue
        if _head_changed_since_session_start([deletion.path]):
            continue
        updates.append(GitIndexEntryUpdate(file_path=deletion.path, force_remove=True))
        restored.append(deletion)

    if updates:
        git_update_index_entries(updates)
        log_journal(
            "session_restored_unstaged_start_deletions",
            deletions=[
                {
                    "path": deletion.path,
                    "old_mode": deletion.old_mode,
                    "old_blob": deletion.old_blob,
                }
                for deletion in restored
            ],
        )

    return restored

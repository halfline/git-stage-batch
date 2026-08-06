"""Session state management for iteration tracking and abort support."""

from __future__ import annotations

import json
import os
import shutil

from .batch_refs import batch_ref_snapshot_recovery_objects, snapshot_batch_refs
from .recovery_anchors import anchor_recovery_objects
from ..utils.session_start_point import (
    resolve_session_start_point,
    save_session_start_point,
    session_comparison_base,
)
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import (
    append_file_path_to_file,
    read_file_paths_file,
    read_text_file_contents,
    write_file_paths_file,
    write_text_file_contents,
)
from ..utils.git_command import run_git_command
from ..git_paths import decode_path, display_path, nul_records
from ..utils.git_worktree import git_remove_paths
from ..utils.git_index import git_restore_intent_to_add_paths
from ..utils.git_repository import get_git_repository_root_path
from ..utils.journal import journal_enabled, log_journal
from .index_entries import IndexEntry, read_index_entries
from ..utils.paths import (
    ensure_abort_snapshots_directory_exists,
    get_abort_head_file_path,
    get_abort_intent_to_add_entries_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_abort_recovery_anchors_file_path,
    get_iteration_count_file_path,
    get_state_directory_path,
)


# Iteration-specific state (cleared by again, stop, abort)
ITERATION_STATE_FILES = [
    "session/selected",
    "session/progress",
    "session/processed",
]

# Session-level state (cleared by stop and abort, preserved by again)
SESSION_STATE_FILES = [
    "session",
    "journal.jsonl",
]


def _journal_index_entries(
    file_paths: list[str],
    entries: dict[str, IndexEntry],
) -> list[dict[str, str | None]]:
    """Build structured index metadata while retaining input path order."""
    return [
        {
            "path": file_path,
            "mode": entries[file_path].mode if file_path in entries else None,
            "object_id": (
                entries[file_path].object_id if file_path in entries else None
            ),
        }
        for file_path in file_paths
    ]


def _diff_index_name_status(
    *,
    intent_to_add_visible: bool,
    file_paths: list[str] | None = None,
) -> dict[str, str]:
    """Return cached path statuses under one intent-to-add interpretation."""
    visibility_option = (
        "--ita-visible-in-index"
        if intent_to_add_visible
        else "--ita-invisible-in-index"
    )
    arguments = [
        "diff-index",
        "--cached",
        "--name-status",
        "-z",
        "--no-renames",
        visibility_option,
        session_comparison_base(),
        "--",
    ]
    if file_paths is not None:
        arguments.extend(file_paths)
    result = run_git_command(
        arguments,
        check=True,
        text_output=False,
        requires_index_lock=False,
        literal_pathspecs=file_paths is not None,
    )
    fields = nul_records(result.stdout)
    if len(fields) % 2 != 0:
        raise CommandError(_("Git returned malformed cached diff output."))
    return {
        decode_path(path): status.decode("ascii", errors="replace")
        for status, path in zip(fields[0::2], fields[1::2])
    }


def _intent_to_add_statuses(
    file_paths: list[str] | None = None,
) -> dict[str, str]:
    """Return visible cached statuses for paths carrying the intent flag."""
    selected_paths = None if file_paths is None else list(dict.fromkeys(file_paths))
    if selected_paths == []:
        return {}
    visible_statuses = _diff_index_name_status(
        intent_to_add_visible=True,
        file_paths=selected_paths,
    )
    invisible_statuses = _diff_index_name_status(
        intent_to_add_visible=False,
        file_paths=selected_paths,
    )
    candidate_paths = visible_statuses.keys() | invisible_statuses.keys()
    selected = None if selected_paths is None else set(selected_paths)
    return {
        file_path: visible_statuses.get(file_path, "")
        for file_path in sorted(candidate_paths)
        if visible_statuses.get(file_path) != invisible_statuses.get(file_path)
        and (selected is None or file_path in selected)
    }


def intent_to_add_files(file_paths: list[str] | None = None) -> list[str]:
    """Return paths whose cached status changes with Git's intent visibility."""
    return list(_intent_to_add_statuses(file_paths))


def path_is_intent_to_add(file_path: str) -> bool:
    """Return whether one path is represented by an intent-to-add entry."""
    return bool(intent_to_add_files([file_path]))


def _snapshot_intent_to_add_files() -> tuple[
    list[str],
    list[str],
    dict[str, IndexEntry],
]:
    """Snapshot all intent-to-add files so they survive git reset --hard on abort.

    Intent-to-add files (added with git add -N) won't be captured by git stash.
    Since git reset --hard will wipe them out, we need to snapshot them upfront
    and record them so we can restore their status.

    Returns:
        Tuple of (all paths, new paths, exact stage-zero entries)
        - all_intent_to_add_files: All files with intent-to-add
        - new_intent_to_add_files: Only files absent from HEAD
    """
    intent_to_add_statuses = _intent_to_add_statuses()
    all_intent_to_add_files = list(intent_to_add_statuses)
    intent_to_add_entries = read_index_entries(all_intent_to_add_files)
    missing_entries = [
        file_path
        for file_path in all_intent_to_add_files
        if file_path not in intent_to_add_entries
    ]
    if missing_entries:
        raise CommandError(
            _(
                "Could not capture intent-to-add index entries for: {paths}"
            ).format(
                paths=", ".join(display_path(path) for path in missing_entries)
            )
        )
    # With rename detection disabled, an ITA absent from HEAD is reported as
    # added in the visible view. A tracked path normalized through rm --cached
    # and add -N is instead reported as deleted. Reuse that bulk classification
    # rather than starting one cat-file process per path.
    new_intent_to_add_files = [
        file_path
        for file_path, status in intent_to_add_statuses.items()
        if status == "A"
    ]

    snapshot_files_if_untracked(
        all_intent_to_add_files,
        intent_to_add=True,
    )

    # Save list of intent-to-add files for abort restoration
    if all_intent_to_add_files:
        intent_to_add_file = (
            get_state_directory_path() / "session" / "abort" / "intent-to-add-files.txt"
        )
        write_file_paths_file(intent_to_add_file, all_intent_to_add_files)
        write_text_file_contents(
            get_abort_intent_to_add_entries_file_path(),
            json.dumps(
                {
                    file_path: {
                        "mode": intent_to_add_entries[file_path].mode,
                        "object_id": intent_to_add_entries[file_path].object_id,
                    }
                    for file_path in all_intent_to_add_files
                },
                indent=2,
                sort_keys=True,
            ),
        )

    return (
        all_intent_to_add_files,
        new_intent_to_add_files,
        intent_to_add_entries,
    )


def initialize_abort_state() -> None:
    """Save the recovery state required before publishing an active session."""
    try:
        _initialize_abort_state()
    except BaseException:
        clear_session_state()
        raise


def _initialize_abort_state() -> None:
    """Build abort state, writing the active-session marker last."""
    start_point = resolve_session_start_point()
    save_session_start_point(start_point)
    abort_head = start_point.head_commit or "UNBORN"

    if start_point.is_unborn:
        indexed_paths_result = run_git_command(
            ["ls-files", "-z"],
            text_output=False,
            requires_index_lock=False,
        )
        indexed_paths = [
            decode_path(path) for path in nul_records(indexed_paths_result.stdout)
        ]
        _snapshot_worktree_paths_for_unborn_abort(indexed_paths)

    # Snapshot all intent-to-add files upfront
    # These files won't survive git reset --hard but aren't in the stash
    # We need to snapshot them now so they can be restored on abort
    (
        all_intent_to_add_files,
        new_intent_to_add_files,
        intent_to_add_entries,
    ) = _snapshot_intent_to_add_files()
    tracked_intent_to_add_files = sorted(
        set(all_intent_to_add_files) - set(new_intent_to_add_files)
    )

    try:
        # Normalize intent-to-add entries so stash creation sees valid index state.
        if new_intent_to_add_files:
            log_journal(
                "session_removing_intent_to_add_files_for_stash",
                files=new_intent_to_add_files,
            )
            git_remove_paths(
                new_intent_to_add_files,
                cached=True,
                quiet=True,
                ignore_unmatch=True,
            )
        if tracked_intent_to_add_files:
            log_journal(
                "session_normalizing_tracked_intent_to_add_files_for_stash",
                files=tracked_intent_to_add_files,
            )
            run_git_command(
                ["reset", "-q", "HEAD", "--", *tracked_intent_to_add_files],
                requires_index_lock=True,
                literal_pathspecs=True,
            )

        # The stash covers tracked worktree and index changes. Untracked files
        # that the session may modify are handled by lazy snapshots.
        if start_point.is_unborn:
            stash_hash = ""
            stash_returncode = 0
            stash_stderr = ""
        else:
            log_journal("session_creating_stash")
            stash_result = run_git_command(
                ["stash", "create"],
                check=False,
                requires_index_lock=False,
            )
            # Git can refresh an ignored stat-only change and return 1 without
            # a diagnostic. Retry once after that refresh before treating the
            # result as a recovery-snapshot failure.
            if (
                stash_result.returncode == 1
                and not stash_result.stdout.strip()
                and not stash_result.stderr.strip()
            ):
                log_journal("session_retrying_silent_stash_failure")
                stash_result = run_git_command(
                    ["stash", "create"],
                    check=False,
                    requires_index_lock=False,
                )
            stash_hash = stash_result.stdout.strip()
            stash_returncode = stash_result.returncode
            stash_stderr = stash_result.stderr
    finally:
        # Restore all intent-to-add entries even when snapshot creation fails.
        if all_intent_to_add_files:
            log_journal(
                "session_re_adding_intent_to_add_files",
                files=all_intent_to_add_files,
            )
            index_before = (
                read_index_entries(all_intent_to_add_files) if journal_enabled() else {}
            )
            git_restore_intent_to_add_paths(
                all_intent_to_add_files,
                saved_entries={
                    file_path: (entry.mode, entry.object_id)
                    for file_path, entry in intent_to_add_entries.items()
                },
            )
            if journal_enabled():
                log_journal(
                    "session_re_add_intent_to_add_files",
                    file_count=len(all_intent_to_add_files),
                    index_entries_before=_journal_index_entries(
                        all_intent_to_add_files,
                        index_before,
                    ),
                    index_entries_after=_journal_index_entries(
                        all_intent_to_add_files,
                        read_index_entries(all_intent_to_add_files),
                    ),
                )

    if stash_returncode != 0:
        log_journal(
            "session_stash_failed",
            returncode=stash_returncode,
            stderr=stash_stderr,
        )
        raise CommandError(
            _(
                "Could not create the recovery snapshot required to start a session: {error}"
            ).format(error=stash_stderr.strip() or _("git stash create failed"))
        )

    write_text_file_contents(get_abort_stash_file_path(), stash_hash)
    if stash_hash:
        log_journal("session_stash_created", stash_hash=stash_hash)

    batch_snapshot = snapshot_batch_refs()
    recovery_objects: list[str | None] = [
        start_point.head_commit,
        stash_hash,
        start_point.index_tree,
        *batch_ref_snapshot_recovery_objects(batch_snapshot),
    ]
    recovery_anchors = anchor_recovery_objects(recovery_objects)
    write_text_file_contents(
        get_abort_recovery_anchors_file_path(),
        json.dumps(recovery_anchors, indent=2, sort_keys=True),
    )
    write_text_file_contents(get_abort_head_file_path(), abort_head)


def _snapshot_worktree_paths_for_unborn_abort(file_paths: list[str]) -> None:
    """Snapshot initially indexed paths that unborn abort may need to restore."""
    if not file_paths:
        return
    repo_root = get_git_repository_root_path()
    snapshot_dir = get_abort_snapshots_directory_path()
    existing = set(read_file_paths_file(get_abort_snapshot_list_file_path()))
    captured: list[str] = []
    for file_path in file_paths:
        source = repo_root / file_path
        if not os.path.lexists(source) or (source.is_dir() and not source.is_symlink()):
            continue
        ensure_abort_snapshots_directory_exists()
        target = snapshot_dir / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        captured.append(file_path)
    if captured:
        write_file_paths_file(
            get_abort_snapshot_list_file_path(),
            [*existing, *captured],
        )


def require_session_started() -> None:
    """Validate that a batch staging session is in progress.

    Raises:
        CommandError: If no session is active
    """
    from .session_ownership import require_current_session_owner

    require_current_session_owner()


def snapshot_file_if_untracked(
    file_path: str,
    *,
    intent_to_add: bool | None = None,
) -> None:
    """Snapshot an untracked file before modification for abort functionality.

    Args:
        file_path: Repository-relative path to the file
    """
    stage_result = run_git_command(
        ["ls-files", "--stage", "--", file_path],
        check=False,
        requires_index_lock=False,
        literal_pathspecs=True,
    )
    if not stage_result.stdout.strip():
        # File not in index at all - it's untracked
        pass  # Continue to snapshot
    else:
        is_intent_to_add = (
            path_is_intent_to_add(file_path) if intent_to_add is None else intent_to_add
        )
        if not is_intent_to_add:
            return  # File has real content in index, don't snapshot

    # Check if already snapshotted
    snapshotted_files = read_file_paths_file(get_abort_snapshot_list_file_path())
    if file_path in snapshotted_files:
        return  # Already snapshotted

    # Read selected file content
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    if not os.path.lexists(full_path):
        return  # File doesn't exist
    # Save a complete before-image for files and standalone repositories.
    snapshot_dir = get_abort_snapshots_directory_path()
    snapshot_path = snapshot_dir / file_path
    ensure_abort_snapshots_directory_exists()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    if full_path.is_dir() and not full_path.is_symlink():
        shutil.copytree(full_path, snapshot_path, symlinks=True)
    else:
        shutil.copy2(full_path, snapshot_path, follow_symlinks=False)

    # Record snapshot in list
    append_file_path_to_file(get_abort_snapshot_list_file_path(), file_path)


def snapshot_files_if_untracked(
    file_paths: list[str],
    *,
    intent_to_add: bool = False,
) -> None:
    """Snapshot untracked files before modifying several paths."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    if not unique_file_paths:
        return

    tracked_real_content: set[str] = set()
    if not intent_to_add:
        intent_to_add_paths = set(intent_to_add_files(unique_file_paths))
        stage_result = run_git_command(
            ["ls-files", "--stage", "-z", "--", *unique_file_paths],
            check=False,
            text_output=False,
            requires_index_lock=False,
            literal_pathspecs=True,
        )

        for record in nul_records(stage_result.stdout):
            if not record:
                continue
            try:
                metadata, path_bytes = record.split(b"\t", 1)
            except ValueError:
                continue
            parts = metadata.split()
            if len(parts) < 2:
                continue
            file_path = decode_path(path_bytes)
            if file_path not in intent_to_add_paths:
                tracked_real_content.add(file_path)

    snapshotted_files = set(read_file_paths_file(get_abort_snapshot_list_file_path()))
    repo_root = get_git_repository_root_path()
    snapshot_dir = get_abort_snapshots_directory_path()
    newly_snapshotted: list[str] = []
    for file_path in unique_file_paths:
        if file_path in tracked_real_content:
            continue
        if file_path in snapshotted_files:
            continue

        full_path = repo_root / file_path
        if not os.path.lexists(full_path):
            continue
        snapshot_path = snapshot_dir / file_path
        ensure_abort_snapshots_directory_exists()
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        if full_path.is_dir() and not full_path.is_symlink():
            shutil.copytree(full_path, snapshot_path, symlinks=True)
        else:
            shutil.copy2(full_path, snapshot_path, follow_symlinks=False)
        newly_snapshotted.append(file_path)

    if newly_snapshotted:
        write_file_paths_file(
            get_abort_snapshot_list_file_path(),
            [*snapshotted_files, *newly_snapshotted],
        )


def get_iteration_count() -> int:
    """Get selected iteration count, defaulting to 1.

    Returns:
        Current iteration number (1-based)
    """
    count_path = get_iteration_count_file_path()
    if not count_path.exists():
        return 1
    try:
        count = int(read_text_file_contents(count_path).strip())
    except ValueError as error:
        raise CommandError(
            _("Session iteration count is invalid: {path}").format(
                path=display_path(str(count_path)),
            )
        ) from error
    if count < 1:
        raise CommandError(
            _("Session iteration count is invalid: {path}").format(
                path=display_path(str(count_path)),
            )
        )
    return count


def increment_iteration_count(current_count: int | None = None) -> None:
    """Increment the iteration counter.

    Called when the user runs 'again' to restart from the beginning.

    Args:
        current_count: A count validated before iteration state was cleared.
    """
    selected = get_iteration_count() if current_count is None else current_count
    write_text_file_contents(get_iteration_count_file_path(), str(selected + 1))


def clear_iteration_state() -> None:
    """Clear iteration-specific state while preserving batches and session state.

    Deletes iteration-specific files (selected hunk, blocklist, snapshots, etc.)
    while preserving:
    - Batch metadata (batches/, batch-sources/)
    - Session state (abort state, context-lines, etc.)
    - Journal

    Called by: again command
    """
    state_dir = get_state_directory_path()

    for filename in ITERATION_STATE_FILES:
        file_path = state_dir / filename
        if file_path.exists():
            if file_path.is_dir():
                shutil.rmtree(file_path)
            else:
                file_path.unlink()


def clear_session_state() -> None:
    """Clear all session and iteration state while preserving permanent data.

    Deletes both session-level state and iteration state, while preserving:
    - Batch metadata (batches/, batch-sources/)

    Called by: stop and abort commands
    """
    state_dir = get_state_directory_path()

    from .undo.refs import clear_undo_history

    clear_undo_history()

    # Clear session state files
    for filename in SESSION_STATE_FILES:
        file_path = state_dir / filename
        if file_path.exists():
            if file_path.is_dir():
                shutil.rmtree(file_path)
            else:
                file_path.unlink()

    # Clear iteration state
    clear_iteration_state()

    # Clean up state directory if it's now empty (only permanent dirs remain)
    try:
        if state_dir.exists():
            remaining = set(item.name for item in state_dir.iterdir())
            if not remaining:
                # Completely empty, remove the directory itself
                state_dir.rmdir()
            # else: permanent dirs remain, leave them
    except OSError:
        # Directory doesn't exist or can't be removed, that's fine
        pass

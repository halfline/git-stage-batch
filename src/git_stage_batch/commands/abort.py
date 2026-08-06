"""Abort command implementation."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from ..data.batch_refs import (
    batch_ref_snapshot_recovery_objects,
    load_batch_refs_snapshot,
    restore_batch_refs,
)
from ..data.session import clear_session_state
from ..data.session_ownership import (
    release_session_ownership,
    require_current_session_owner,
    require_no_foreign_session_owner,
)
from ..data.recovery_anchors import validate_recovery_objects
from ..utils.session_start_point import load_session_start_point
from ..data.start_time_changes import read_staged_renames
from ..exceptions import CommandError, exit_with_error
from ..git_paths import display_path
from ..i18n import _
from ..utils.file_io import read_file_paths_file, read_text_file_contents
from ..utils.git_command import run_git_command
from ..utils.git_worktree import (
    git_apply_stash,
    git_reset_hard,
)
from ..utils.git_index import (
    git_read_tree,
    git_restore_intent_to_add_paths,
    git_reset_paths,
)
from ..utils.git_refs import update_git_refs
from ..utils.git_repository import (
    get_git_repository_root_path,
    object_id_hex_length,
    require_git_repository,
)
from ..utils.paths import (
    get_abort_head_file_path,
    get_abort_intent_to_add_entries_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_auto_added_files_file_path,
    get_abort_state_directory_path,
    get_abort_recovery_anchors_file_path,
)


def _recovery_relative_path(file_path: str) -> Path:
    """Return one validated repository-relative recovery path."""
    if not file_path or "\x00" in file_path or file_path.startswith("/"):
        raise CommandError(
            _(
                "Abort recovery metadata contains an invalid repository path: "
                "{file!r}. The session remains active."
            ).format(file=display_path(file_path))
        )

    components = file_path.split("/")
    if components[-1] == "":
        components = components[:-1]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise CommandError(
            _(
                "Abort recovery metadata contains an invalid repository path: "
                "{file!r}. The session remains active."
            ).format(file=display_path(file_path))
        )
    return Path(*components)


def _load_abort_snapshot_paths() -> list[str]:
    """Load and validate required untracked before-images before mutation."""
    snapshot_list_path = get_abort_snapshot_list_file_path()
    if not snapshot_list_path.exists():
        return []

    file_paths = read_file_paths_file(snapshot_list_path)
    snapshots_dir = get_abort_snapshots_directory_path()
    for file_path in file_paths:
        relative_path = _recovery_relative_path(file_path)
        if not os.path.lexists(snapshots_dir / relative_path):
            raise CommandError(
                _(
                    "Could not restore untracked path {file}: "
                    "its abort snapshot is unavailable. "
                    "The session remains active."
                ).format(file=display_path(file_path))
            )
    return file_paths


def _load_abort_intent_to_add_state() -> tuple[
    list[str],
    dict[str, tuple[str, str]] | None,
]:
    """Load and validate pre-session intent-to-add recovery metadata."""
    paths_file = get_abort_state_directory_path() / "intent-to-add-files.txt"
    file_paths = read_file_paths_file(paths_file) if paths_file.exists() else []
    entries_file = get_abort_intent_to_add_entries_file_path()
    for file_path in file_paths:
        if file_path.endswith("/"):
            raise CommandError(
                _(
                    "Intent-to-add recovery metadata contains an invalid path. "
                    "The session remains active."
                )
            )
        _recovery_relative_path(file_path)
    if not entries_file.exists():
        return file_paths, None

    try:
        raw_entries: object = json.loads(read_text_file_contents(entries_file))
    except json.JSONDecodeError as error:
        raise CommandError(
            _(
                "Intent-to-add recovery metadata is invalid. "
                "The session remains active."
            )
        ) from error
    if not isinstance(raw_entries, dict) or set(raw_entries) != set(file_paths):
        raise CommandError(
            _(
                "Intent-to-add recovery metadata does not match its path list. "
                "The session remains active."
            )
        )

    entries: dict[str, tuple[str, str]] = {}
    object_id_length = object_id_hex_length()
    for file_path in file_paths:
        raw_entry = raw_entries.get(file_path)
        if not isinstance(raw_entry, dict):
            break
        mode = raw_entry.get("mode")
        object_id = raw_entry.get("object_id")
        if (
            mode not in {"100644", "100755", "120000", "160000"}
            or not isinstance(object_id, str)
            or len(object_id) != object_id_length
            or any(
                character not in "0123456789abcdefABCDEF"
                for character in object_id
            )
        ):
            break
        entries[file_path] = (mode, object_id)
    if len(entries) != len(file_paths):
        raise CommandError(
            _(
                "Intent-to-add recovery metadata contains an invalid index entry. "
                "The session remains active."
            )
        )
    return file_paths, entries


def _require_safe_restore_parent(repo_root: Path, file_path: str) -> Path:
    """Refuse a restore whose existing parent escapes through a symlink."""
    relative_path = _recovery_relative_path(file_path)
    current_path = repo_root
    for component in relative_path.parts[:-1]:
        current_path /= component
        if not os.path.lexists(current_path):
            break
        try:
            metadata = current_path.lstat()
        except OSError as error:
            raise CommandError(
                _(
                    "Could not safely restore untracked path {file}: "
                    "a parent path cannot be inspected. The session remains active."
                ).format(file=display_path(file_path))
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise CommandError(
                _(
                    "Could not safely restore untracked path {file}: "
                    "a parent path is not a real directory. "
                    "The session remains active."
                ).format(file=display_path(file_path))
            )
    return relative_path


def _remove_normalized_rename_destinations_before_stash_apply() -> None:
    renames = read_staged_renames()
    if not renames:
        return

    repo_root = get_git_repository_root_path()
    for rename in renames:
        tracked_result = run_git_command(
            ["ls-files", "--error-unmatch", "--", rename.new_path],
            check=False,
            requires_index_lock=False,
            literal_pathspecs=True,
        )
        if tracked_result.returncode == 0:
            continue

        target_path = repo_root / rename.new_path
        if target_path.is_dir() and not target_path.is_symlink():
            shutil.rmtree(target_path)
        else:
            target_path.unlink(missing_ok=True)


def _regular_file_contents_match(snapshot_path: Path, target_path: Path) -> bool:
    """Compare two regular files without process-global result caching."""
    if snapshot_path.stat().st_size != target_path.stat().st_size:
        return False
    with (
        snapshot_path.open("rb") as snapshot_file,
        target_path.open("rb") as target_file,
    ):
        while True:
            snapshot_chunk = snapshot_file.read(64 * 1024)
            target_chunk = target_file.read(64 * 1024)
            if snapshot_chunk != target_chunk:
                return False
            if not snapshot_chunk:
                return True


def _snapshot_path_matches_target(snapshot_path: Path, target_path: Path) -> bool:
    """Return whether an abort snapshot has already been restored exactly."""
    try:
        if snapshot_path.is_symlink():
            return (
                target_path.is_symlink()
                and os.readlink(os.fsencode(snapshot_path))
                == os.readlink(os.fsencode(target_path))
            )

        if snapshot_path.is_dir():
            if target_path.is_symlink() or not target_path.is_dir():
                return False
            if stat.S_IMODE(snapshot_path.stat().st_mode) != stat.S_IMODE(
                target_path.stat().st_mode
            ):
                return False

            snapshot_child_count = 0
            with os.scandir(snapshot_path) as snapshot_entries:
                for snapshot_entry in snapshot_entries:
                    snapshot_child_count += 1
                    snapshot_child = Path(snapshot_entry.path)
                    target_child = target_path / snapshot_entry.name
                    if not os.path.lexists(target_child) or not (
                        _snapshot_path_matches_target(snapshot_child, target_child)
                    ):
                        return False

            target_child_count = 0
            with os.scandir(target_path) as target_entries:
                for _target_entry in target_entries:
                    target_child_count += 1
            return snapshot_child_count == target_child_count

        return (
            not target_path.is_symlink()
            and target_path.is_file()
            and stat.S_IMODE(snapshot_path.stat().st_mode)
            == stat.S_IMODE(target_path.stat().st_mode)
            and _regular_file_contents_match(snapshot_path, target_path)
        )
    except OSError:
        # A disappearing or unreadable destination cannot be trusted as an
        # already-complete restore.
        return False


def _restore_snapshot_directory(snapshot_path: Path, target_path: Path) -> None:
    """Copy a directory before atomically publishing its complete tree."""
    with tempfile.TemporaryDirectory(
        prefix=".git-stage-batch-abort-",
        dir=target_path.parent,
    ) as temporary_directory:
        staged_target = Path(temporary_directory) / "restored"
        shutil.copytree(snapshot_path, staged_target, symlinks=True)
        staged_target.rename(target_path)


def _restore_snapshot_leaf(snapshot_path: Path, target_path: Path) -> None:
    """Atomically publish one regular-file or symlink snapshot."""
    with tempfile.TemporaryDirectory(
        prefix=".git-stage-batch-abort-",
        dir=target_path.parent,
    ) as temporary_directory:
        staged_target = Path(temporary_directory) / "restored"
        if snapshot_path.is_symlink():
            link_target = os.readlink(os.fsencode(snapshot_path))
            os.symlink(link_target, os.fsencode(staged_target))
        else:
            shutil.copy2(snapshot_path, staged_target)
        os.replace(staged_target, target_path)


def command_abort(*, quiet: bool = False) -> None:
    """Abort the session and undo all changes including commits and discards."""
    require_git_repository()
    require_no_foreign_session_owner()

    # Check if abort state exists
    if not get_abort_head_file_path().exists():
        exit_with_error(_("No session to abort. Abort state not found."))
    require_current_session_owner()

    # Read abort state
    abort_head = read_text_file_contents(get_abort_head_file_path()).strip()
    start_point = load_session_start_point()
    abort_stash_path = get_abort_stash_file_path()
    abort_stash = (
        read_text_file_contents(abort_stash_path).strip()
        if abort_stash_path.exists()
        else None
    )
    batch_snapshot = load_batch_refs_snapshot()
    recovery_objects: list[str | None] = [
        start_point.head_commit,
        start_point.index_tree,
        abort_stash,
        *batch_ref_snapshot_recovery_objects(batch_snapshot),
    ]
    try:
        recovery_anchors = json.loads(
            read_text_file_contents(get_abort_recovery_anchors_file_path())
        )
    except json.JSONDecodeError:
        recovery_anchors = None
    try:
        validate_recovery_objects(recovery_objects, anchors=recovery_anchors)
    except CommandError as error:
        raise CommandError(
            _(
                "{error}\nThe session remains active; repair the recovery state "
                "and run 'git-stage-batch abort' again."
            ).format(error=error.message)
        ) from error
    intent_to_add_files, intent_to_add_entries = _load_abort_intent_to_add_state()
    snapshotted_files = _load_abort_snapshot_paths()

    # Reset auto-added files first
    if not start_point.is_unborn and get_auto_added_files_file_path().exists():
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        if auto_added:
            git_reset_paths(auto_added)

    # Reset to start HEAD (undoes commits, resets index and tracked files)
    # Set GIT_REFLOG_ACTION for clear reflog entries
    env = os.environ.copy()
    env["GIT_REFLOG_ACTION"] = "stage-batch abort"

    if start_point.is_unborn:
        if start_point.symbolic_head:
            update_git_refs(deletes=[start_point.symbolic_head])
        git_read_tree(start_point.index_tree)
    else:
        if not quiet:
            print(_("Resetting to {}...").format(abort_head[:7]), file=sys.stderr)
        git_reset_hard(abort_head, env=env)
        _remove_normalized_rename_destinations_before_stash_apply()

    # Apply original stash if it exists (with --index to restore staged state)
    if abort_stash:
        if not quiet:
            print(_("Applying original changes..."), file=sys.stderr)
        result = git_apply_stash(abort_stash, restore_index=True, env=env, check=False)
        if result.returncode != 0:
            raise CommandError(
                _(
                    "Could not restore the session's original changes: {error}\n"
                    "The session remains active. Resolve the obstruction and run "
                    "'git-stage-batch abort' again."
                ).format(error=result.stderr.strip())
            )

    # Restore snapshotted untracked files
    if snapshotted_files:
        repo_root = get_git_repository_root_path()
        snapshots_dir = get_abort_snapshots_directory_path()

        # Refuse every incompatible directory before restoring any snapshot so
        # a later conflict cannot leave an earlier restore half-published.
        for file_path in snapshotted_files:
            relative_path = _require_safe_restore_parent(repo_root, file_path)
            snapshot_path = snapshots_dir / relative_path
            if not os.path.lexists(snapshot_path):
                raise CommandError(
                    _(
                        "Could not restore untracked path {file}: "
                        "its abort snapshot is unavailable. "
                        "The session remains active."
                    ).format(file=display_path(file_path))
                )
            target_path = repo_root / relative_path
            snapshot_is_directory = (
                snapshot_path.is_dir() and not snapshot_path.is_symlink()
            )
            target_is_directory = (
                target_path.is_dir() and not target_path.is_symlink()
            )
            if snapshot_is_directory and os.path.lexists(target_path):
                if (
                    target_is_directory
                    and _snapshot_path_matches_target(snapshot_path, target_path)
                ):
                    continue
                raise CommandError(
                    _(
                        "Could not restore untracked directory {file}: "
                        "the path already exists. The session remains active."
                    ).format(file=display_path(file_path))
                )
            if not snapshot_is_directory and target_is_directory:
                snapshot_kind = (
                    _("symlink") if snapshot_path.is_symlink() else _("file")
                )
                raise CommandError(
                    _(
                        "Could not restore untracked {kind} {file}: "
                        "the path is now a directory. The session remains active."
                    ).format(kind=snapshot_kind, file=display_path(file_path))
                )

        for file_path in snapshotted_files:
            relative_path = _require_safe_restore_parent(repo_root, file_path)
            snapshot_path = snapshots_dir / relative_path
            target_path = repo_root / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if snapshot_path.is_dir() and not snapshot_path.is_symlink():
                if not _snapshot_path_matches_target(snapshot_path, target_path):
                    _restore_snapshot_directory(snapshot_path, target_path)
            elif snapshot_path.is_symlink():
                if target_path.is_dir() and not target_path.is_symlink():
                    raise CommandError(
                        _(
                            "Could not restore untracked symlink {file}: "
                            "the path is now a directory. The session remains active."
                        ).format(file=display_path(file_path))
                    )
                _restore_snapshot_leaf(snapshot_path, target_path)
            else:
                if target_path.is_dir() and not target_path.is_symlink():
                    raise CommandError(
                        _(
                            "Could not restore untracked file {file}: "
                            "the path is now a directory. The session remains active."
                        ).format(file=display_path(file_path))
                    )
                _restore_snapshot_leaf(snapshot_path, target_path)
            if not quiet:
                print(
                    _("Restored: {}").format(display_path(file_path)),
                    file=sys.stderr,
                )

    # Restore intent-to-add status for files that had it before session
    if intent_to_add_files:
        git_restore_intent_to_add_paths(
            intent_to_add_files,
            saved_entries=intent_to_add_entries,
        )

    # Restore batch refs to their original state
    # This recreates both git refs and metadata files from the snapshot
    restore_batch_refs(batch_snapshot)

    # Clear all session state (preserves batches and batch-sources)
    # Do this AFTER restore_batch_refs so snapshot file is available
    clear_session_state()
    release_session_ownership()

    if not quiet:
        print(_("✓ Session aborted. All changes reverted."), file=sys.stderr)

"""Undo checkpoint storage and restoration."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..batch.ref_names import BATCH_CONTENT_REF_PREFIX, BATCH_STATE_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
from ..editor import (
    EditorBuffer,
    load_git_blob_as_buffer,
    write_buffer_to_path,
    write_buffer_to_working_tree_path,
)
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import read_file_paths_file
from ..utils.git import (
    create_git_blob,
    GitIndexEntryUpdate,
    get_git_repository_root_path,
    git_commit_tree,
    git_read_tree,
    git_update_index_entries,
    git_write_tree,
    run_git_command,
    temp_git_index,
    update_git_refs,
)
from ..utils.paths import (
    get_auto_added_files_file_path,
    get_batches_directory_path,
    get_session_directory_path,
    get_state_directory_path,
)


SESSION_UNDO_STACK_REF = "refs/git-stage-batch/session/undo-stack"
SESSION_REDO_STACK_REF = "refs/git-stage-batch/session/redo-stack"
REF_PREFIXES = (
    LEGACY_BATCH_REF_PREFIX,
    BATCH_CONTENT_REF_PREFIX,
    BATCH_STATE_REF_PREFIX,
)
_PENDING_CHECKPOINT: str | None = None


def _list_refs() -> dict[str, str]:
    """List refs whose values are restored by undo."""
    refs: dict[str, str] = {}
    for prefix in REF_PREFIXES:
        result = run_git_command(["for-each-ref", "--format=%(objectname) %(refname)", prefix], check=False)
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            object_name, ref_name = line.split(None, 1)
            refs[ref_name] = object_name
    return refs


def _changed_worktree_paths() -> list[str]:
    """Return repository-relative paths whose worktree bytes may need undo."""
    paths: set[str] = set()
    commands = [
        ["diff", "--name-only", "HEAD"],
        ["diff", "--cached", "--name-only"],
        ["ls-files", "--others", "--exclude-standard"],
    ]
    for args in commands:
        result = run_git_command(args, check=False)
        if result.returncode == 0:
            paths.update(line for line in result.stdout.splitlines() if line)
    return sorted(paths)


def _snapshot_worktree_paths(paths: list[str]) -> list[dict[str, Any]]:
    repo_root = get_git_repository_root_path()
    worktree_paths: list[dict[str, Any]] = []
    for file_path in sorted(set(paths)):
        full_path = repo_root / file_path
        if os.path.lexists(full_path):
            mode = _file_mode_for_path(full_path)
            worktree_paths.append({
                "path": file_path,
                "exists": True,
                "mode": mode,
                "blob": _create_blob_from_worktree_path(
                    full_path,
                    mode=mode,
                ),
            })
        else:
            worktree_paths.append({
                "path": file_path,
                "exists": False,
                "mode": "100644",
                "blob": None,
            })

    return worktree_paths


def _create_blob_from_path(path: Path) -> str:
    with EditorBuffer.from_path(path) as buffer:
        return create_git_blob(buffer.byte_chunks())


def _create_blob_from_worktree_path(path: Path, *, mode: str) -> str:
    if mode == "120000":
        return create_git_blob([os.readlink(os.fsencode(path))])
    return _create_blob_from_path(path)


def _index_update_from_path(
    *,
    index_path: str,
    source_path: Path,
    mode: str,
) -> GitIndexEntryUpdate:
    return GitIndexEntryUpdate(
        file_path=index_path,
        mode=mode,
        blob_sha=_create_blob_from_worktree_path(source_path, mode=mode),
    )


def _snapshot_current_state(paths: list[str]) -> dict[str, Any]:
    index_result = run_git_command(["write-tree"], check=False)
    return {
        "index_tree": index_result.stdout.strip() if index_result.returncode == 0 else None,
        "refs": _list_refs(),
        "worktree_paths": _snapshot_worktree_paths(paths),
    }


def _add_blob_to_index(env: dict[str, str], path: str, data: bytes, mode: str = "100644") -> None:
    git_update_index_entries(
        [
            GitIndexEntryUpdate(
                file_path=path,
                mode=mode,
                blob_sha=create_git_blob([data]),
            )
        ],
        env=env,
    )


def _file_mode_for_path(path: Path) -> str:
    try:
        file_status = path.lstat()
    except OSError:
        return "100644"
    if stat.S_ISLNK(file_status.st_mode):
        return "120000"
    return "100755" if file_status.st_mode & stat.S_IXUSR else "100644"


def _restore_file_mode(path: Path, mode: str) -> None:
    if mode == "120000":
        return
    current_mode = path.stat().st_mode
    if mode == "100755":
        path.chmod(current_mode | stat.S_IXUSR)
    else:
        path.chmod(current_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)


def _add_directory_to_index(env: dict[str, str], *, source_dir: Path, tree_prefix: str) -> None:
    if not source_dir.exists():
        return
    updates: list[GitIndexEntryUpdate] = []
    for file_path in sorted(path for path in source_dir.rglob("*") if path.is_file()):
        relative_path = file_path.relative_to(source_dir).as_posix()
        tree_path = f"{tree_prefix}/{relative_path}"
        updates.append(
            _index_update_from_path(
                index_path=tree_path,
                source_path=file_path,
                mode=_file_mode_for_path(file_path),
            )
        )
    git_update_index_entries(updates, env=env)


def _current_stack_commit(ref_name: str) -> str | None:
    result = run_git_command(["rev-parse", "--verify", ref_name], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _current_undo_commit() -> str | None:
    return _current_stack_commit(SESSION_UNDO_STACK_REF)


def _current_redo_commit() -> str | None:
    return _current_stack_commit(SESSION_REDO_STACK_REF)


def _create_undo_checkpoint(operation: str, *, worktree_paths: list[str] | None = None) -> str | None:
    """Create a before-image checkpoint for an undoable operation."""
    session_dir = get_state_directory_path() / "session"
    if not session_dir.exists():
        return None

    _clear_redo_history()

    global _PENDING_CHECKPOINT

    tracked_worktree_paths = sorted(set(_changed_worktree_paths()) | set(worktree_paths or []))
    before = _snapshot_current_state(tracked_worktree_paths)

    manifest = {
        "operation": operation,
        "head": run_git_command(["rev-parse", "HEAD"], check=False).stdout.strip(),
        "index_tree": before["index_tree"],
        "refs": before["refs"],
        "worktree_paths": [
            {key: value for key, value in entry.items() if key != "blob"}
            for entry in before["worktree_paths"]
        ],
        "tracked_worktree_paths": tracked_worktree_paths,
    }

    with temp_git_index() as env:
        _add_blob_to_index(env, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        _add_directory_to_index(env, source_dir=session_dir, tree_prefix="session")
        _add_directory_to_index(env, source_dir=get_batches_directory_path(), tree_prefix="batches")

        git_update_index_entries(
            [
                GitIndexEntryUpdate(
                    file_path=f"worktree/{entry['path']}",
                    mode=entry["mode"],
                    blob_sha=entry["blob"],
                )
                for entry in before["worktree_paths"]
                if entry["exists"] and entry.get("blob")
            ],
            env=env,
        )

        tree_sha = git_write_tree(env=env)

    parent = _current_undo_commit()
    checkpoint_commit = git_commit_tree(
        tree_sha,
        parents=[parent] if parent else [],
        message=f"Undo checkpoint: {operation}",
    )
    run_git_command(["update-ref", SESSION_UNDO_STACK_REF, checkpoint_commit])
    _PENDING_CHECKPOINT = checkpoint_commit
    return checkpoint_commit


@contextmanager
def undo_checkpoint(operation: str, *, worktree_paths: list[str] | None = None) -> Iterator[None]:
    """Bracket an undoable operation with before and after snapshots."""
    if _PENDING_CHECKPOINT is not None:
        yield
        return

    checkpoint = _create_undo_checkpoint(operation, worktree_paths=worktree_paths)
    try:
        yield
    finally:
        if checkpoint is not None:
            finalize_pending_checkpoint()


def finalize_pending_checkpoint() -> None:
    """Record the post-operation state for conflict detection."""
    global _PENDING_CHECKPOINT
    checkpoint = _PENDING_CHECKPOINT
    if checkpoint is None:
        return
    _PENDING_CHECKPOINT = None

    current = _current_undo_commit()
    if current != checkpoint:
        return

    try:
        manifest = _read_json_from_commit(checkpoint, "manifest.json")
    except CommandError:
        return

    paths = sorted(set(manifest.get("tracked_worktree_paths", [])) | set(_changed_worktree_paths()))
    manifest["after"] = _snapshot_current_state(paths)
    manifest["after"]["worktree_paths"] = manifest["after"]["worktree_paths"]

    with temp_git_index() as env:
        git_read_tree(checkpoint, env=env)
        _add_blob_to_index(env, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        tree_sha = git_write_tree(env=env)

    parent = _checkpoint_parent(checkpoint)
    checkpoint_commit = git_commit_tree(
        tree_sha,
        parents=[parent] if parent else [],
        message=f"Undo checkpoint: {manifest.get('operation', 'operation')}",
    )
    run_git_command(["update-ref", SESSION_UNDO_STACK_REF, checkpoint_commit])


def _read_json_blob(blob_sha: str) -> dict[str, Any]:
    with load_git_blob_as_buffer(blob_sha) as buffer:
        return json.loads(buffer.to_bytes().decode("utf-8"))


def _write_blob_to_path(blob_sha: str, target_path: Path) -> None:
    with load_git_blob_as_buffer(blob_sha) as buffer:
        write_buffer_to_path(target_path, buffer)


def _write_blob_to_worktree_path(
    blob_sha: str,
    target_path: Path,
    *,
    mode: str,
) -> None:
    with load_git_blob_as_buffer(blob_sha) as buffer:
        write_buffer_to_working_tree_path(target_path, buffer, mode=mode)


def _tree_entries(commit: str, prefix: str) -> list[tuple[str, str, str]]:
    result = run_git_command(["ls-tree", "-r", "-z", commit, prefix], check=False, text_output=False)
    if result.returncode != 0 or not result.stdout:
        return []

    entries = []
    for record in result.stdout.rstrip(b"\0").split(b"\0"):
        if not record:
            continue
        meta, path_bytes = record.split(b"\t", 1)
        mode, object_type, object_sha = meta.decode("ascii").split()
        if object_type != "blob":
            continue
        entries.append((mode, object_sha, path_bytes.decode("utf-8", errors="surrogateescape")))
    return entries


def _read_json_from_commit(commit: str, path: str) -> dict[str, Any]:
    entries = _tree_entries(commit, path)
    if not entries:
        raise CommandError(_("Undo checkpoint is missing {path}").format(path=path))
    _mode, blob_sha, _entry_path = entries[0]
    return _read_json_blob(blob_sha)


def _restore_tree_prefix(commit: str, *, prefix: str, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    entries = _tree_entries(commit, prefix)
    if not entries:
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for mode, blob_sha, tree_path in entries:
        relative_path = Path(tree_path).relative_to(prefix)
        target_path = target_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _write_blob_to_path(blob_sha, target_path)
        _restore_file_mode(target_path, mode)


def _restore_refs(saved_refs: dict[str, str]) -> None:
    current_refs = _list_refs()
    update_git_refs(
        updates=sorted(saved_refs.items()),
        deletes=sorted(ref_name for ref_name in current_refs if ref_name not in saved_refs),
    )


def _restore_worktree(commit: str, manifest: dict[str, Any]) -> None:
    repo_root = get_git_repository_root_path()
    worktree_blobs = {
        Path(tree_path).relative_to("worktree").as_posix(): (mode, blob_sha)
        for mode, blob_sha, tree_path in _tree_entries(commit, "worktree")
    }

    for entry in manifest.get("worktree_paths", []):
        file_path = entry["path"]
        target_path = repo_root / file_path
        if not entry.get("exists", False):
            if os.path.lexists(target_path):
                target_path.unlink()
            continue

        blob_info = worktree_blobs.get(file_path)
        if blob_info is None:
            continue
        mode, blob_sha = blob_info
        _write_blob_to_worktree_path(blob_sha, target_path, mode=mode)


def _restore_intent_to_add_entries() -> None:
    repo_root = get_git_repository_root_path()
    for file_path in read_file_paths_file(get_auto_added_files_file_path()):
        full_path = repo_root / file_path
        if os.path.lexists(full_path):
            run_git_command(["add", "-N", "--", file_path], check=False)


def _worktree_state_by_path(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entry["path"]: entry for entry in entries}


def _detect_conflicts_against_state(expected_state: dict[str, Any]) -> list[str]:
    conflicts: list[str] = []
    current = _snapshot_current_state([entry["path"] for entry in expected_state.get("worktree_paths", [])])

    if current.get("index_tree") != expected_state.get("index_tree"):
        conflicts.append(_("index"))

    if current.get("refs") != expected_state.get("refs"):
        conflicts.append(_("batch refs"))

    expected_worktree = _worktree_state_by_path(expected_state.get("worktree_paths", []))
    current_worktree = _worktree_state_by_path(current.get("worktree_paths", []))
    for path, expected in sorted(expected_worktree.items()):
        actual = current_worktree.get(path)
        if actual != expected:
            conflicts.append(path)

    return conflicts


def _detect_conflicts(manifest: dict[str, Any]) -> list[str]:
    after = manifest.get("after")
    if not isinstance(after, dict):
        return []
    return _detect_conflicts_against_state(after)


def _detect_redo_conflicts(manifest: dict[str, Any]) -> list[str]:
    after_undo = manifest.get("after_undo")
    if not isinstance(after_undo, dict):
        return []
    return _detect_conflicts_against_state(after_undo)


def _checkpoint_parent(commit: str) -> str | None:
    result = run_git_command(["rev-parse", "--verify", f"{commit}^"], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _redo_relevant_paths(manifest: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    paths.update(manifest.get("tracked_worktree_paths", []))
    for entry in manifest.get("worktree_paths", []):
        paths.add(entry["path"])
    after = manifest.get("after")
    if isinstance(after, dict):
        for entry in after.get("worktree_paths", []):
            paths.add(entry["path"])
    paths.update(_changed_worktree_paths())
    return sorted(paths)


def _write_snapshot_commit(
    *,
    ref_name: str,
    message: str,
    manifest: dict[str, Any],
    session_dir: Path,
    batches_dir: Path,
    worktree_entries: list[dict[str, Any]],
    parent: str | None,
) -> str:
    with temp_git_index() as env:
        _add_blob_to_index(env, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        _add_directory_to_index(env, source_dir=session_dir, tree_prefix="session")
        _add_directory_to_index(env, source_dir=batches_dir, tree_prefix="batches")

        repo_root = get_git_repository_root_path()
        index_updates: list[GitIndexEntryUpdate] = []
        for entry in worktree_entries:
            if not entry.get("exists", False):
                continue
            blob_sha = entry.get("blob")
            if blob_sha:
                index_updates.append(
                    GitIndexEntryUpdate(
                        file_path=f"worktree/{entry['path']}",
                        mode=entry.get("mode", "100644"),
                        blob_sha=blob_sha,
                    )
                )
            else:
                full_path = repo_root / entry["path"]
                if os.path.lexists(full_path):
                    mode = _file_mode_for_path(full_path)
                    index_updates.append(
                        GitIndexEntryUpdate(
                            file_path=f"worktree/{entry['path']}",
                            mode=mode,
                            blob_sha=_create_blob_from_worktree_path(
                                full_path,
                                mode=mode,
                            ),
                        )
                    )
        git_update_index_entries(index_updates, env=env)

        tree_sha = git_write_tree(env=env)

    commit_sha = git_commit_tree(
        tree_sha,
        parents=[parent] if parent else [],
        message=message,
    )
    run_git_command(["update-ref", ref_name, commit_sha])
    return commit_sha


def _push_redo_node(
    *,
    operation: str,
    undo_checkpoint: str,
    target: dict[str, Any],
    target_session_dir: Path,
    target_batches_dir: Path,
    after_undo: dict[str, Any],
    worktree_entries: list[dict[str, Any]],
) -> str:
    manifest = {
        "operation": operation,
        "undo_checkpoint": undo_checkpoint,
        "head": target.get("head", run_git_command(["rev-parse", "HEAD"], check=False).stdout.strip()),
        "index_tree": target.get("index_tree"),
        "refs": target.get("refs", {}),
        "worktree_paths": [
            {key: value for key, value in entry.items() if key != "blob"}
            for entry in worktree_entries
        ],
        "after_undo": after_undo,
    }

    parent = _current_redo_commit()
    return _write_snapshot_commit(
        ref_name=SESSION_REDO_STACK_REF,
        message=f"Redo node: {operation}",
        manifest=manifest,
        session_dir=target_session_dir,
        batches_dir=target_batches_dir,
        worktree_entries=worktree_entries,
        parent=parent,
    )


def undo_last_checkpoint(*, force: bool = False) -> str:
    """Restore the latest undo checkpoint and pop it from the undo stack."""
    finalize_pending_checkpoint()
    checkpoint = _current_undo_commit()
    if checkpoint is None:
        raise CommandError(_("Nothing to undo."))

    manifest = _read_json_from_commit(checkpoint, "manifest.json")
    conflicts = _detect_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            preview = _("{preview}, and {count} more").format(preview=preview, count=len(conflicts) - 5)
        raise CommandError(
            _("Cannot undo because current state has changed since the checkpoint: {items}.\n"
              "Run 'git-stage-batch undo --force' to overwrite those changes.").format(items=preview)
        )

    operation = str(manifest.get("operation", "operation"))
    redo_paths = _redo_relevant_paths(manifest)
    redo_target = _snapshot_current_state(redo_paths)
    redo_worktree_entries = _snapshot_worktree_paths(redo_paths)

    redo_session_dir = tempfile.mkdtemp(prefix="gsb-redo-session-")
    redo_batches_dir = tempfile.mkdtemp(prefix="gsb-redo-batches-")
    try:
        live_session_dir = get_session_directory_path()
        live_batches_dir = get_batches_directory_path()
        if live_session_dir.exists():
            shutil.copytree(live_session_dir, redo_session_dir, dirs_exist_ok=True)
        if live_batches_dir.exists():
            shutil.copytree(live_batches_dir, redo_batches_dir, dirs_exist_ok=True)

        _restore_tree_prefix(checkpoint, prefix="session", target_dir=live_session_dir)
        _restore_tree_prefix(checkpoint, prefix="batches", target_dir=live_batches_dir)
        _restore_refs(manifest.get("refs", {}))

        index_tree = manifest.get("index_tree")
        if index_tree:
            git_read_tree(index_tree)

        _restore_worktree(checkpoint, manifest)
        _restore_intent_to_add_entries()

        after_undo = _snapshot_current_state(redo_paths)

        _push_redo_node(
            operation=operation,
            undo_checkpoint=checkpoint,
            target=redo_target,
            target_session_dir=Path(redo_session_dir),
            target_batches_dir=Path(redo_batches_dir),
            after_undo=after_undo,
            worktree_entries=redo_worktree_entries,
        )
    finally:
        shutil.rmtree(redo_session_dir, ignore_errors=True)
        shutil.rmtree(redo_batches_dir, ignore_errors=True)

    parent = _checkpoint_parent(checkpoint)
    if parent:
        run_git_command(["update-ref", SESSION_UNDO_STACK_REF, parent])
    else:
        run_git_command(["update-ref", "-d", SESSION_UNDO_STACK_REF], check=False)

    return operation


def redo_last_checkpoint(*, force: bool = False) -> str:
    """Reapply the most recently undone operation from the redo stack."""
    finalize_pending_checkpoint()
    redo_node = _current_redo_commit()
    if redo_node is None:
        raise CommandError(_("Nothing to redo."))

    manifest = _read_json_from_commit(redo_node, "manifest.json")
    conflicts = _detect_redo_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            preview = _("{preview}, and {count} more").format(preview=preview, count=len(conflicts) - 5)
        raise CommandError(
            _("Cannot redo because current state has changed since the undo: {items}.\n"
              "Run 'git-stage-batch redo --force' to overwrite those changes.").format(items=preview)
        )

    _restore_tree_prefix(redo_node, prefix="session", target_dir=get_session_directory_path())
    _restore_tree_prefix(redo_node, prefix="batches", target_dir=get_batches_directory_path())
    _restore_refs(manifest.get("refs", {}))

    index_tree = manifest.get("index_tree")
    if index_tree:
        git_read_tree(index_tree)

    _restore_worktree(redo_node, manifest)
    _restore_intent_to_add_entries()

    undo_checkpoint = manifest.get("undo_checkpoint")
    if undo_checkpoint:
        run_git_command(["update-ref", SESSION_UNDO_STACK_REF, undo_checkpoint])

    parent = _checkpoint_parent(redo_node)
    if parent:
        run_git_command(["update-ref", SESSION_REDO_STACK_REF, parent])
    else:
        run_git_command(["update-ref", "-d", SESSION_REDO_STACK_REF], check=False)

    return str(manifest.get("operation", "operation"))


def _clear_redo_history() -> None:
    run_git_command(["update-ref", "-d", SESSION_REDO_STACK_REF], check=False)


def clear_undo_history() -> None:
    """Clear all undo and redo checkpoints for the current session."""
    run_git_command(["update-ref", "-d", SESSION_UNDO_STACK_REF], check=False)
    _clear_redo_history()

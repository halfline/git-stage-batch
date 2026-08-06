"""Git index and tree plumbing helpers."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .git_command import run_git_command
from ..git_paths import decode_path, display_path, encode_path
from .git_repository import (
    get_git_object_format,
    get_git_repository_root_path,
    is_git_repository_root_path,
    null_object_id,
)


@dataclass(frozen=True)
class GitIndexEntryUpdate:
    """One index-info update for a temporary Git index."""

    file_path: str
    mode: str | None = None
    blob_sha: str | None = None
    force_remove: bool = False


@contextmanager
def temp_git_index() -> Iterator[dict[str, str]]:
    """Create a temporary Git index and yield an environment that uses it."""
    temp_index = tempfile.NamedTemporaryFile(delete=False, suffix=".index")
    temp_index_path = temp_index.name
    temp_index.close()
    os.unlink(temp_index_path)

    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = temp_index_path
    try:
        yield env
    finally:
        if os.path.exists(temp_index_path):
            os.unlink(temp_index_path)


def git_read_tree(treeish: str, *, env: dict[str, str] | None = None) -> None:
    """Read a Git tree into the current or provided index."""
    run_git_command(["read-tree", treeish], env=env, requires_index_lock=True)


def git_update_index(
    *,
    file_path: str,
    mode: str | None = None,
    blob_sha: str | None = None,
    force_remove: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Update one index entry from a blob, or force-remove it."""
    if force_remove:
        if mode is not None or blob_sha is not None:
            raise ValueError("mode and blob_sha cannot be used with force_remove=True")
        arguments = ["update-index", "--force-remove", "--", file_path]
    else:
        if mode is None or blob_sha is None:
            raise ValueError("mode and blob_sha are required unless force_remove=True")
        arguments = ["update-index", "--add", "--cacheinfo", mode, blob_sha, file_path]

    return run_git_command(
        arguments,
        check=check,
        env=env,
        requires_index_lock=True,
    )


def git_refresh_index(*, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Refresh cached index stat information from the working tree."""
    return run_git_command(
        ["update-index", "--refresh"],
        check=check,
        requires_index_lock=True,
    )


def git_update_gitlink(
    *,
    file_path: str,
    oid: str | None,
    remove: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Update one index entry that stores a submodule commit pointer."""
    if remove:
        if oid is not None:
            raise ValueError("oid cannot be used with remove=True")
        return git_update_index(
            file_path=file_path,
            force_remove=True,
            check=check,
            env=env,
        )

    if oid is None:
        raise ValueError("oid is required unless remove=True")

    return git_update_index(
        file_path=file_path,
        mode="160000",
        blob_sha=oid,
        check=check,
        env=env,
    )


def git_update_index_entries(
    entries: Iterable[GitIndexEntryUpdate],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Update several index entries through one update-index process."""
    payload_chunks: list[bytes] = []
    null_oid: bytes | None = None
    for entry in entries:
        path_bytes = encode_path(entry.file_path)
        if entry.force_remove:
            if entry.mode is not None or entry.blob_sha is not None:
                raise ValueError("mode and blob_sha cannot be used with force_remove=True")
            if null_oid is None:
                null_oid = null_object_id().encode("ascii")
            payload_chunks.extend([
                b"0 ",
                null_oid,
                b"\t",
                path_bytes,
                b"\0",
            ])
        else:
            if entry.mode is None or entry.blob_sha is None:
                raise ValueError("mode and blob_sha are required unless force_remove=True")
            payload_chunks.extend([
                entry.mode.encode("ascii"),
                b" ",
                entry.blob_sha.encode("ascii"),
                b"\t",
                path_bytes,
                b"\0",
            ])

    if not payload_chunks:
        return

    run_git_command(
        ["update-index", "-z", "--index-info"],
        stdin_chunks=payload_chunks,
        env=env,
        requires_index_lock=True,
    )


def git_write_tree(*, env: dict[str, str] | None = None) -> str:
    """Write the current or provided index as a Git tree."""
    return run_git_command(
        ["write-tree"],
        env=env,
        requires_index_lock=False,
    ).stdout.strip()


def git_commit_tree(
    tree_sha: str,
    *,
    parents: Iterable[str] = (),
    message: str,
    env: dict[str, str] | None = None,
) -> str:
    """Create a commit object from a tree and optional parents."""
    arguments = ["commit-tree", tree_sha]
    for parent in parents:
        arguments.extend(["-p", parent])
    arguments.extend(["-m", message])
    return run_git_command(arguments, env=env, requires_index_lock=False).stdout.strip()


def git_apply_to_index(
    patch_chunks: Iterable[bytes],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Apply patch chunks to the index."""
    return run_git_command(
        ["apply", "--cached", "--whitespace=nowarn"],
        stdin_chunks=patch_chunks,
        check=check,
        requires_index_lock=True,
    )


def git_add_paths(
    paths: Sequence[str],
    *,
    intent_to_add: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Stage paths, optionally as intent-to-add entries."""
    arguments = ["add"]
    if intent_to_add:
        arguments.append("-N")
    arguments.extend(["--", *paths])
    return run_git_command(
        arguments,
        check=check,
        requires_index_lock=True,
        literal_pathspecs=True,
    )


def _stage_zero_index_entries(
    file_paths: Sequence[str],
) -> dict[str, tuple[str, str]]:
    """Return current stage-zero modes and objects for literal paths."""
    unique_paths = list(dict.fromkeys(file_paths))
    if not unique_paths:
        return {}
    result = run_git_command(
        ["ls-files", "--stage", "-z", "--", *unique_paths],
        text_output=False,
        requires_index_lock=False,
        literal_pathspecs=True,
    )
    requested_paths = set(unique_paths)
    entries: dict[str, tuple[str, str]] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        parts = metadata.split()
        decoded_path = decode_path(path_bytes)
        if decoded_path in requested_paths and len(parts) >= 3 and parts[2] == b"0":
            entries[decoded_path] = (
                parts[0].decode("ascii", errors="replace"),
                parts[1].decode("ascii", errors="replace"),
            )
    return entries


def _isolated_git_environment() -> dict[str, str]:
    """Return an environment detached from the caller's Git repository."""
    isolated = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_")
    }
    isolated["GIT_CONFIG_NOSYSTEM"] = "1"
    isolated["GIT_CONFIG_GLOBAL"] = os.devnull
    return isolated


def _create_intent_placeholder(path: Path, mode: str) -> None:
    """Create a temporary worktree object whose Git mode matches an index entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "120000":
        path.symlink_to("git-stage-batch-intent-placeholder")
        return
    if mode == "160000":
        path.mkdir()
        isolated_env = _isolated_git_environment()
        run_git_command(
            [
                "init",
                "--quiet",
                f"--object-format={get_git_object_format()}",
            ],
            cwd=str(path),
            env=isolated_env,
            requires_index_lock=False,
        )
        run_git_command(
            [
                "-c",
                "user.name=git-stage-batch",
                "-c",
                "user.email=git-stage-batch.invalid",
                "commit",
                "--allow-empty",
                "--quiet",
                "-m",
                "intent-to-add placeholder",
            ],
            cwd=str(path),
            env=isolated_env,
        )
        return
    if mode not in {"100644", "100755"}:
        raise ValueError(f"Cannot restore intent-to-add mode {mode!r}")
    path.touch()
    path.chmod(0o755 if mode == "100755" else 0o644)


def _worktree_intent_mode(path: Path) -> str | None:
    """Return the Git mode an existing worktree path would contribute."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        return "120000"
    if stat.S_ISREG(metadata.st_mode):
        return "100755" if metadata.st_mode & 0o111 else "100644"
    if stat.S_ISDIR(metadata.st_mode) and is_git_repository_root_path(path):
        return "160000"
    return None


def _publish_intent_to_add_paths(
    file_paths: Sequence[str],
    *,
    temporary_worktree: str,
    repo_root: Path,
) -> None:
    """Publish intent-to-add entries from an isolated worktree."""
    payload: list[bytes] = []
    for file_path in file_paths:
        payload.extend((encode_path(file_path), b"\0"))
    run_git_command(
        [
            "--literal-pathspecs",
            f"--work-tree={temporary_worktree}",
            "-c",
            "advice.addEmbeddedRepo=false",
            "-c",
            "core.fileMode=true",
            "-c",
            "core.symlinks=true",
            "add",
            "-N",
            "--force",
            "--sparse",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
        ],
        stdin_chunks=payload,
        cwd=str(repo_root),
    )


def git_restore_intent_to_add_paths(
    file_paths: Sequence[str],
    *,
    saved_entries: Mapping[str, tuple[str, str]] | None = None,
) -> None:
    """Restore intent-to-add flags through one alternate-worktree add.

    ``saved_entries`` supplies the exact pre-normalization index entries when
    the current index no longer contains enough information to infer a path's
    mode. If publication fails, restoration is retried through ``git add -N``
    so rollback cannot publish ordinary staged entries without intent flags.
    """
    unique_paths = list(dict.fromkeys(file_paths))
    if not unique_paths:
        return

    relative_paths: dict[str, Path] = {}
    for file_path in unique_paths:
        relative_path = Path(file_path)
        if not file_path or relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("intent-to-add path must be repository-relative")
        relative_paths[file_path] = relative_path

    if saved_entries is not None:
        missing_entries = [
            file_path for file_path in unique_paths if file_path not in saved_entries
        ]
        if missing_entries:
            raise ValueError(
                "saved intent-to-add entries are missing paths: "
                + ", ".join(repr(path) for path in missing_entries)
            )
        current_entries: dict[str, tuple[str, str]] = {}
    else:
        current_entries = _stage_zero_index_entries(unique_paths)

    repo_root = get_git_repository_root_path()
    saved_modes: dict[str, str] = {}
    for file_path in unique_paths:
        saved_entry = (
            saved_entries[file_path]
            if saved_entries is not None
            else current_entries.get(file_path)
        )
        saved_mode = (
            saved_entry[0]
            if saved_entry is not None
            else _worktree_intent_mode(repo_root / relative_paths[file_path])
        )
        if saved_mode is None:
            raise ValueError(
                "Cannot restore intent-to-add mode for path "
                f"{display_path(file_path)!r}"
            )
        if saved_mode not in {"100644", "100755", "120000", "160000"}:
            raise ValueError(f"Cannot restore intent-to-add mode {saved_mode!r}")
        saved_modes[file_path] = saved_mode

    with tempfile.TemporaryDirectory(
        prefix="git-stage-batch-intent-to-add-"
    ) as temporary_worktree:
        temporary_root = Path(temporary_worktree)
        for file_path in unique_paths:
            _create_intent_placeholder(
                temporary_root / relative_paths[file_path],
                saved_modes[file_path],
            )

        git_update_index_entries(
            [
                GitIndexEntryUpdate(file_path=file_path, force_remove=True)
                for file_path in unique_paths
            ]
        )
        try:
            _publish_intent_to_add_paths(
                unique_paths,
                temporary_worktree=temporary_worktree,
                repo_root=repo_root,
            )
        except BaseException:
            removals = [
                GitIndexEntryUpdate(file_path=file_path, force_remove=True)
                for file_path in unique_paths
            ]
            git_update_index_entries(removals)
            try:
                _publish_intent_to_add_paths(
                    unique_paths,
                    temporary_worktree=temporary_worktree,
                    repo_root=repo_root,
                )
            except BaseException:
                git_update_index_entries(removals)
            raise


def git_add_paths_from_stdin(
    paths: Sequence[str],
    *,
    intent_to_add: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Add arbitrarily many NUL-delimited paths without an argv-sized list."""
    unique_paths = list(dict.fromkeys(paths))
    if not unique_paths:
        return subprocess.CompletedProcess(["git", "add"], 0, "", "")
    arguments = [
        "--literal-pathspecs",
        "add",
        "--pathspec-from-file=-",
        "--pathspec-file-nul",
    ]
    if intent_to_add:
        arguments.append("--intent-to-add")
    payload: list[bytes] = []
    for file_path in unique_paths:
        payload.extend((encode_path(file_path), b"\0"))
    return run_git_command(
        arguments,
        stdin_chunks=payload,
        check=check,
        requires_index_lock=True,
    )


def git_reset_paths(
    paths: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Reset paths in the index from HEAD."""
    return run_git_command(
        ["reset", "--", *paths],
        check=check,
        requires_index_lock=True,
        literal_pathspecs=True,
    )

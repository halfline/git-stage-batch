"""Session batch source management for batch operations."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass

from ..editor import (
    EditorBuffer,
    load_git_blob_as_buffer,
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import read_file_paths_file, read_text_file_contents, write_text_file_contents
from ..utils.command import run_command
from ..utils.git import (
    create_git_blob,
    get_git_repository_root_path,
    git_commit_tree,
    git_read_tree,
    git_update_index,
    git_write_tree,
    list_git_tree_blobs,
    run_git_command,
    temp_git_index,
    update_git_refs,
)
from ..utils.journal import log_journal
from ..utils.paths import (
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_session_batch_sources_file_path,
)


@dataclass(frozen=True)
class BatchSourceCommit:
    """A per-file batch source commit and the file buffer it stores."""

    commit_sha: str
    file_buffer: EditorBuffer


def _buffer_preview(buffer: EditorBuffer) -> bytes:
    """Return a short byte preview for journal logging."""
    for chunk in buffer.byte_chunks(200):
        return chunk
    return b"(empty)"


def load_saved_session_file_as_buffer(file_path: str) -> EditorBuffer:
    """Load a file buffer as it was at session start.

    For tracked files, extracts from the git stash created by
    initialize_abort_state(). For untracked files, reads from the lazy
    snapshot taken before first modification.

    Args:
        file_path: Repository-relative path to the file

    Returns:
        File buffer, preserving exact encoding and line endings

    Raises:
        CommandError: If the file buffer cannot be retrieved
    """
    # Check if file was untracked and snapshotted
    snapshot_list_path = get_abort_snapshot_list_file_path()
    if snapshot_list_path.exists():
        snapshotted_files = read_file_paths_file(snapshot_list_path)
        if file_path in snapshotted_files:
            # Read from snapshot directory
            snapshot_path = get_abort_snapshots_directory_path() / file_path
            if snapshot_path.exists():
                return EditorBuffer.from_path(snapshot_path)
            else:
                raise CommandError(
                    _("Snapshot for untracked file not found: {file}").format(file=file_path)
                )

    # File was tracked - extract from stash if it exists, otherwise from baseline
    stash_file_path = get_abort_stash_file_path()
    if stash_file_path.exists():
        stash_commit = read_text_file_contents(stash_file_path).strip()
        if stash_commit:
            # Extract file from stash commit
            # The stash commit contains the working tree state
            buffer = load_git_object_as_buffer(f"{stash_commit}:{file_path}")
            if buffer is not None:
                return buffer

    # No stash or file not in stash - file was unchanged at session start
    # Read from baseline (abort HEAD)
    abort_head_path = get_abort_head_file_path()
    if not abort_head_path.exists():
        raise CommandError(_("No session found"))

    baseline_commit = read_text_file_contents(abort_head_path).strip()
    buffer = load_git_object_as_buffer(f"{baseline_commit}:{file_path}")
    if buffer is None:
        # File might not exist in baseline (new file)
        return EditorBuffer.from_bytes(b"")

    return buffer


def _fast_import_quote_path(file_path: str) -> str:
    """Quote a repository path for fast-import commands."""
    raw_path = file_path.encode("utf-8")
    if raw_path and all(byte not in b' \t\r\n"\\' and 32 <= byte < 127 for byte in raw_path):
        return file_path

    escaped = []
    for byte in raw_path:
        if byte == ord("\\"):
            escaped.append("\\\\")
        elif byte == ord('"'):
            escaped.append('\\"')
        elif byte == ord("\n"):
            escaped.append("\\n")
        elif byte == ord("\r"):
            escaped.append("\\r")
        elif byte == ord("\t"):
            escaped.append("\\t")
        elif 32 <= byte < 127:
            escaped.append(chr(byte))
        else:
            escaped.append(f"\\{byte:03o}")
    return '"' + "".join(escaped) + '"'


def _read_session_file_buffers(
    file_paths: list[str],
    *,
    baseline_commit: str,
) -> tuple[dict[str, EditorBuffer], set[str]]:
    """Read session-start buffers for several files."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    baseline_blobs = list_git_tree_blobs(baseline_commit, unique_file_paths)
    baseline_existing_files = set(baseline_blobs)

    snapshot_list_path = get_abort_snapshot_list_file_path()
    snapshotted_files = (
        set(read_file_paths_file(snapshot_list_path))
        if snapshot_list_path.exists() else
        set()
    )

    buffers: dict[str, EditorBuffer] = {}
    remaining_paths: list[str] = []
    try:
        snapshot_directory = get_abort_snapshots_directory_path()
        for file_path in unique_file_paths:
            if file_path in snapshotted_files:
                snapshot_path = snapshot_directory / file_path
                if snapshot_path.exists():
                    buffers[file_path] = EditorBuffer.from_path(snapshot_path)
                    continue
                raise CommandError(
                    _("Snapshot for untracked file not found: {file}").format(file=file_path)
                )
            remaining_paths.append(file_path)

        stash_file_path = get_abort_stash_file_path()
        stash_blobs = {}
        if stash_file_path.exists():
            stash_commit = read_text_file_contents(stash_file_path).strip()
            if stash_commit:
                stash_blobs = list_git_tree_blobs(stash_commit, remaining_paths)

        repo_root = get_git_repository_root_path()
        for file_path in remaining_paths:
            stash_blob = stash_blobs.get(file_path)
            if stash_blob is not None:
                buffers[file_path] = load_git_blob_as_buffer(stash_blob.blob_sha)
                continue

            baseline_blob = baseline_blobs.get(file_path)
            if baseline_blob is not None:
                buffers[file_path] = load_git_blob_as_buffer(baseline_blob.blob_sha)
                continue

            file_full_path = repo_root / file_path
            if (
                file_path not in baseline_existing_files
                and os.path.lexists(file_full_path)
            ):
                buffers[file_path] = load_working_tree_file_as_buffer(file_path)
            else:
                buffers[file_path] = EditorBuffer.from_bytes(b"")
    except Exception:
        for buffer in buffers.values():
            buffer.close()
        raise

    return buffers, baseline_existing_files


def create_batch_source_commits(file_paths: list[str]) -> dict[str, BatchSourceCommit]:
    """Create per-file batch source commits in one fast-import transaction."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    if not unique_file_paths:
        return {}

    baseline_commit = read_text_file_contents(get_abort_head_file_path()).strip()
    file_buffers, files_existing_at_session_start = _read_session_file_buffers(
        unique_file_paths,
        baseline_commit=baseline_commit,
    )

    return_file_buffers = False
    try:
        repo_root = get_git_repository_root_path()
        file_modes: dict[str, str] = {}
        content_stats: dict[str, tuple[int, int]] = {}
        for file_path in unique_file_paths:
            full_path = repo_root / file_path
            if os.path.lexists(full_path):
                file_status = full_path.lstat()
                if stat.S_ISLNK(file_status.st_mode):
                    mode = "120000"
                else:
                    mode = "100755" if file_status.st_mode & stat.S_IXUSR else "100644"
            else:
                mode = "100644"
            file_modes[file_path] = mode

            buffer = file_buffers[file_path]
            buffer_len = buffer.byte_count
            buffer_lines = len(buffer) if buffer_len else 0
            content_stats[file_path] = (buffer_len, buffer_lines)
            log_journal(
                "batch_source_creating",
                file_path=file_path,
                baseline_commit=baseline_commit,
                file_existed_at_session_start=file_path in files_existing_at_session_start,
                content_len=buffer_len,
                content_lines=buffer_lines,
                buffer_preview=_buffer_preview(buffer),
            )

        import_id = uuid.uuid4().hex
        temp_refs = [
            f"refs/git-stage-batch/tmp/batch-source/{import_id}/{index}"
            for index, _file_path in enumerate(unique_file_paths)
        ]
        blob_mark_start = 1
        commit_mark_start = blob_mark_start + len(unique_file_paths)

        def fast_import_chunks():
            for index, file_path in enumerate(unique_file_paths):
                blob_mark = blob_mark_start + index
                buffer = file_buffers[file_path]
                buffer_len, _buffer_lines = content_stats[file_path]
                yield f"blob\nmark :{blob_mark}\ndata {buffer_len}\n".encode("ascii")
                yield from buffer.byte_chunks()
                yield b"\n"

            for index, file_path in enumerate(unique_file_paths):
                commit_mark = commit_mark_start + index
                blob_mark = blob_mark_start + index
                message = f"Batch source for {file_path}".encode("utf-8")
                quoted_path = _fast_import_quote_path(file_path)
                yield (
                    f"commit {temp_refs[index]}\n"
                    f"mark :{commit_mark}\n"
                    "committer Git Stage Batch <git-stage-batch@example.invalid> 0 +0000\n"
                    f"data {len(message)}\n"
                ).encode("utf-8")
                yield message
                yield (
                    f"\nfrom {baseline_commit}\n"
                    f"M {file_modes[file_path]} :{blob_mark} {quoted_path}\n"
                ).encode("utf-8")

        with tempfile.NamedTemporaryFile(delete=False) as marks_file:
            marks_path = marks_file.name

        import_succeeded = False
        try:
            run_command(
                [
                    "git",
                    "fast-import",
                    "--quiet",
                    "--date-format=raw",
                    f"--export-marks={marks_path}",
                ],
                fast_import_chunks(),
                capture_stdout=False,
            )
            import_succeeded = True
            marks: dict[int, str] = {}
            with open(marks_path, "r", encoding="ascii") as file:
                for line in file:
                    mark_text, object_sha = line.strip().split(" ", 1)
                    marks[int(mark_text[1:])] = object_sha
        finally:
            if import_succeeded:
                update_git_refs(deletes=temp_refs, ignore_missing_deletes=False)
            try:
                os.unlink(marks_path)
            except FileNotFoundError:
                pass

        results: dict[str, BatchSourceCommit] = {}
        for index, file_path in enumerate(unique_file_paths):
            commit_sha = marks[commit_mark_start + index]
            content_len, content_lines = content_stats[file_path]
            results[file_path] = BatchSourceCommit(
                commit_sha=commit_sha,
                file_buffer=file_buffers[file_path],
            )
            log_journal(
                "batch_source_created",
                file_path=file_path,
                batch_source_commit=commit_sha,
                blob_sha=marks[blob_mark_start + index],
                mode=file_modes[file_path],
                tree=None,
                verified_content_len=content_len,
                verified_lines=content_lines,
            )
        return_file_buffers = True
        return results
    finally:
        if not return_file_buffers:
            for buffer in file_buffers.values():
                buffer.close()


def create_batch_source_commit(
    file_path: str,
    *,
    file_buffer_override: bytes | EditorBuffer | None = None
) -> str:
    """Create batch source commit for a file.

    The batch source commit captures the working tree state of the file at session
    start. It serves as a stable reference point for batch operations.

    Args:
        file_path: Repository-relative path to the file
        file_buffer_override: Optional exact buffer bytes to store. Used by
            stale-source advancement, where the new source may be synthesized
            from current working tree content plus already-owned lines rather
            than the original session-start snapshot.

    Returns:
        Batch source commit SHA

    Raises:
        CommandError: If batch source commit cannot be created
    """
    # Get baseline commit (HEAD at session start)
    baseline_commit = read_text_file_contents(get_abort_head_file_path()).strip()

    # Check if file existed at session start
    baseline_result = run_git_command(["cat-file", "-e", f"{baseline_commit}:{file_path}"], check=False)
    file_existed_at_session_start = baseline_result.returncode == 0

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    file_buffer: EditorBuffer | None = None
    close_file_buffer = True
    content_len = 0
    content_lines = 0
    try:
        if file_buffer_override is not None:
            if isinstance(file_buffer_override, EditorBuffer):
                file_buffer = file_buffer_override
                close_file_buffer = False
            else:
                file_buffer = EditorBuffer.from_bytes(file_buffer_override)
        else:
            file_buffer = load_saved_session_file_as_buffer(file_path)

        # For new files (didn't exist at session start), use selected working tree content
        # This ensures the batch source has the lines we're actually claiming
        if file_buffer_override is None and not file_existed_at_session_start:
            if os.path.lexists(full_path):
                file_buffer.close()
                file_buffer = load_working_tree_file_as_buffer(file_path)

        content_len = file_buffer.byte_count
        content_lines = len(file_buffer) if content_len else 0
        log_journal(
            "batch_source_creating",
            file_path=file_path,
            baseline_commit=baseline_commit,
            file_existed_at_session_start=file_existed_at_session_start,
            content_len=content_len,
            content_lines=content_lines,
            buffer_preview=_buffer_preview(file_buffer)
        )

        blob_sha = create_git_blob(file_buffer.byte_chunks())
    finally:
        if file_buffer is not None and close_file_buffer:
            file_buffer.close()

    # Detect file mode
    if os.path.lexists(full_path):
        st = full_path.lstat()
        if stat.S_ISLNK(st.st_mode):
            mode = "120000"
        elif st.st_mode & stat.S_IXUSR:
            mode = "100755"
        else:
            mode = "100644"
    else:
        mode = "100644"

    with temp_git_index() as env:
        git_read_tree(baseline_commit, env=env)
        git_update_index(mode=mode, blob_sha=blob_sha, file_path=file_path, env=env)
        new_tree = git_write_tree(env=env)

    batch_source_commit = git_commit_tree(
        new_tree,
        parents=[baseline_commit],
        message=f"Batch source for {file_path}",
    )

    log_journal(
        "batch_source_created",
        file_path=file_path,
        batch_source_commit=batch_source_commit,
        blob_sha=blob_sha,
        mode=mode,
        tree=new_tree,
        verified_content_len=content_len,
        verified_lines=content_lines,
    )

    return batch_source_commit


def get_batch_source_for_file(file_path: str) -> str | None:
    """Retrieve existing batch source commit for a file from session cache.

    Args:
        file_path: Repository-relative path to the file

    Returns:
        Batch source commit SHA if found, None otherwise
    """
    batch_sources = load_session_batch_sources()
    return batch_sources.get(file_path)


def load_session_batch_sources() -> dict[str, str]:
    """Load session batch sources from session-batch-sources.json.

    Returns:
        Dictionary mapping file paths to batch source commit SHAs
    """
    batch_sources_path = get_session_batch_sources_file_path()
    if not batch_sources_path.exists():
        return {}

    try:
        content = read_text_file_contents(batch_sources_path)
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return {}


def save_session_batch_sources(batch_sources: dict[str, str]) -> None:
    """Save session batch sources to session-batch-sources.json.

    Args:
        batch_sources: Dictionary mapping file paths to batch source commit SHAs
    """
    batch_sources_path = get_session_batch_sources_file_path()
    content = json.dumps(batch_sources, indent=2)
    write_text_file_contents(batch_sources_path, content)

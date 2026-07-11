"""Batch source commit construction for batch operations."""

from __future__ import annotations

import os
import stat
import tempfile
import uuid
from dataclasses import dataclass, replace

from ...core.buffer import LineBuffer
from ...utils.session_start_point import load_session_start_point
from ...utils.repository_buffers import (
    load_working_tree_file_as_buffer,
)
from ...utils.file_io import read_text_file_contents
from ...utils.git_command import (
    run_git_command,
)
from ...git_paths import encode_path
from ...utils.git_refs import (
    update_git_refs,
)
from ...utils.git_index import (
    git_commit_tree,
    git_read_tree,
    git_update_index,
    git_write_tree,
    temp_git_index,
)
from ...utils.git_repository import get_git_repository_root_path
from ...utils.git_object_io import create_git_blob
from ...utils.journal import JournalLevel, journal_enabled, log_journal
from ...utils.paths import get_abort_head_file_path
from .buffers import (
    load_saved_session_file_as_buffer as _load_saved_session_file_as_buffer,
    read_session_file_buffers as _read_session_file_buffers,
)


@dataclass(frozen=True)
class BatchSourceCommit:
    """A per-file batch source commit and the file buffer it stores."""

    commit_sha: str
    file_buffer: LineBuffer


def _buffer_preview(buffer: LineBuffer) -> bytes:
    """Return a short byte preview for journal logging."""
    for chunk in buffer.byte_chunks(200):
        return chunk
    return b"(empty)"


def _fast_import_quote_path(file_path: str) -> str:
    """Quote a repository path for fast-import commands."""
    raw_path = encode_path(file_path)
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


def create_batch_source_commits(file_paths: list[str]) -> dict[str, BatchSourceCommit]:
    """Create per-file batch source commits in one fast-import transaction."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    if not unique_file_paths:
        return {}

    start_point = load_session_start_point()
    abort_head = read_text_file_contents(get_abort_head_file_path()).strip()
    if abort_head != "UNBORN":
        start_point = replace(
            start_point,
            head_commit=abort_head,
            symbolic_head=None,
        )
    baseline_commit = start_point.head_commit or start_point.index_tree
    file_buffers, files_existing_at_session_start = _read_session_file_buffers(
        unique_file_paths,
        baseline_commit=baseline_commit,
    )

    if start_point.is_unborn:
        try:
            return {
                file_path: BatchSourceCommit(
                    commit_sha=create_batch_source_commit(
                        file_path,
                        file_buffer_override=file_buffers[file_path],
                    ),
                    file_buffer=file_buffers[file_path],
                )
                for file_path in unique_file_paths
            }
        except Exception:
            for buffer in file_buffers.values():
                buffer.close()
            raise

    return_file_buffers = False
    try:
        repo_root = get_git_repository_root_path()
        file_modes: dict[str, str] = {}
        content_sizes: dict[str, int] = {}
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
            content_sizes[file_path] = buffer_len
            if journal_enabled():
                fields = {
                    "file_path": file_path,
                    "baseline_commit": baseline_commit,
                    "file_existed_at_session_start": file_path in files_existing_at_session_start,
                    "content_len": buffer_len,
                }
                if journal_enabled(JournalLevel.CONTENT_DEBUG):
                    fields["content_lines"] = len(buffer) if buffer_len else 0
                    fields["buffer_preview"] = _buffer_preview(buffer)
                log_journal("batch_source_creating", **fields)

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
                buffer_len = content_sizes[file_path]
                yield f"blob\nmark :{blob_mark}\ndata {buffer_len}\n".encode("ascii")
                yield from buffer.byte_chunks()
                yield b"\n"

            for index, file_path in enumerate(unique_file_paths):
                commit_mark = commit_mark_start + index
                blob_mark = blob_mark_start + index
                message = b"Batch source for " + encode_path(file_path)
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
            run_git_command(
                [
                    "fast-import",
                    "--quiet",
                    "--date-format=raw",
                    f"--export-marks={marks_path}",
                ],
                stdin_chunks=fast_import_chunks(),
                capture_stdout=False,
                requires_index_lock=False,
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
            content_len = content_sizes[file_path]
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
    file_buffer_override: LineBuffer | None = None
) -> str:
    """Create batch source commit for a file.

    The batch source commit captures the working tree state of the file at session
    start. It serves as a stable reference point for batch operations.

    Args:
        file_path: Repository-relative path to the file
        file_buffer_override: Optional exact file buffer to store. Used by
            stale-source advancement, where the new source may be synthesized
            from current working tree content plus already-owned lines rather
            than the original session-start snapshot.

    Returns:
        Batch source commit SHA

    Raises:
        CommandError: If batch source commit cannot be created
    """
    start_point = load_session_start_point()
    abort_head = read_text_file_contents(get_abort_head_file_path()).strip()
    if abort_head != "UNBORN":
        start_point = replace(
            start_point,
            head_commit=abort_head,
            symbolic_head=None,
        )
    baseline_commit = start_point.head_commit
    baseline_treeish = baseline_commit or start_point.index_tree

    # Check if file existed at session start
    baseline_result = run_git_command(
        ["cat-file", "-e", f"{baseline_treeish}:{file_path}"],
        check=False,
        requires_index_lock=False,
    )
    file_existed_at_session_start = baseline_result.returncode == 0

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    file_buffer: LineBuffer | None = None
    close_file_buffer = True
    content_len = 0
    try:
        if file_buffer_override is not None:
            file_buffer = file_buffer_override
            close_file_buffer = False
        else:
            file_buffer = _load_saved_session_file_as_buffer(file_path)

        # For new files (didn't exist at session start), use selected working tree content
        # This ensures the batch source has the lines we're actually claiming
        if file_buffer_override is None and not file_existed_at_session_start:
            if os.path.lexists(full_path):
                file_buffer.close()
                file_buffer = load_working_tree_file_as_buffer(file_path)

        content_len = file_buffer.byte_count
        if journal_enabled():
            fields = {
                "file_path": file_path,
                "baseline_commit": baseline_commit,
                "file_existed_at_session_start": file_existed_at_session_start,
                "content_len": content_len,
            }
            if journal_enabled(JournalLevel.CONTENT_DEBUG):
                fields["content_lines"] = len(file_buffer) if content_len else 0
                fields["buffer_preview"] = _buffer_preview(file_buffer)
            log_journal("batch_source_creating", **fields)

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
        git_read_tree(baseline_treeish, env=env)
        git_update_index(mode=mode, blob_sha=blob_sha, file_path=file_path, env=env)
        new_tree = git_write_tree(env=env)

    batch_source_commit = git_commit_tree(
        new_tree,
        parents=[baseline_commit] if baseline_commit else [],
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
    )

    return batch_source_commit

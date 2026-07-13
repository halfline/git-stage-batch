"""Tests for batch-source line annotation."""

from __future__ import annotations

from git_stage_batch.batch.source import annotation as source_annotation_module
from git_stage_batch.batch.source.annotation import (
    annotate_with_batch_source_lines,
    annotate_with_batch_source_working_lines,
)
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange


def test_annotate_with_batch_source_lines_accepts_non_list_byte_sequences(
    line_sequence,
):
    """Batch source annotation accepts indexed byte-line sequences."""
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=2),
        lines=[
            LineEntry(
                id=None,
                kind=" ",
                old_line_number=1,
                new_line_number=1,
                text_bytes=b"line 1\n",
                text="line 1\n",
            ),
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=2,
                text_bytes=b"inserted\n",
                text="inserted\n",
            ),
            LineEntry(
                id=None,
                kind=" ",
                old_line_number=2,
                new_line_number=3,
                text_bytes=b"line 2\n",
                text="line 2\n",
            ),
        ],
    )

    annotated = annotate_with_batch_source_lines(
        line_changes,
        batch_source_lines=line_sequence([b"line 1\n", b"line 2\n"]),
        working_lines=line_sequence([
            b"line 1\n",
            b"inserted\n",
            b"line 2\n",
        ]),
    )

    assert [line.source_line for line in annotated.lines] == [1, None, 2]


def test_annotate_with_batch_source_working_lines_accepts_sequences(
    monkeypatch,
    line_sequence,
):
    """Batch source lookup can annotate indexed working content lines."""
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=2),
        lines=[
            LineEntry(
                id=None,
                kind=" ",
                old_line_number=1,
                new_line_number=1,
                text_bytes=b"line 1\n",
                text="line 1\n",
            ),
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=2,
                text_bytes=b"inserted\n",
                text="inserted\n",
            ),
            LineEntry(
                id=None,
                kind=" ",
                old_line_number=2,
                new_line_number=3,
                text_bytes=b"line 2\n",
                text="line 2\n",
            ),
        ],
    )

    monkeypatch.setattr(
        source_annotation_module,
        "get_batch_source_for_file",
        lambda path: "source-commit",
    )
    monkeypatch.setattr(
        source_annotation_module,
        "read_git_object_buffer_or_none",
        lambda revision_path: LineBuffer.from_chunks(
            iter([b"line 1\n", b"line 2\n"])
        ),
    )

    annotated = annotate_with_batch_source_working_lines(
        "file.txt",
        line_changes,
        line_sequence([
            b"line 1\n",
            b"inserted\n",
            b"line 2\n",
        ]),
    )

    assert [line.source_line for line in annotated.lines] == [1, None, 2]


def test_annotate_with_batch_source_loads_indexed_buffers(monkeypatch, tmp_path):
    """Batch source annotation loads source and working tree buffers."""
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=2),
        lines=[
            LineEntry(
                id=None,
                kind=" ",
                old_line_number=1,
                new_line_number=1,
                text_bytes=b"line 1\n",
                text="line 1\n",
            ),
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=2,
                text_bytes=b"inserted\n",
                text="inserted\n",
            ),
            LineEntry(
                id=None,
                kind=" ",
                old_line_number=2,
                new_line_number=3,
                text_bytes=b"line 2\n",
                text="line 2\n",
            ),
        ],
    )
    loaded_revisions = []
    loaded_working_paths = []
    (tmp_path / "file.txt").write_bytes(b"line 1\ninserted\nline 2\n")

    monkeypatch.setattr(
        source_annotation_module,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    monkeypatch.setattr(
        source_annotation_module,
        "get_batch_source_for_file",
        lambda path: "source-commit",
    )

    def fake_read_git_object_buffer_or_none(revision_path):
        loaded_revisions.append(revision_path)
        return LineBuffer.from_chunks(iter([b"line 1\n", b"line 2\n"]))

    monkeypatch.setattr(
        source_annotation_module,
        "read_git_object_buffer_or_none",
        fake_read_git_object_buffer_or_none,
    )

    def fake_load_working_tree_file_as_buffer(path):
        loaded_working_paths.append(path)
        return LineBuffer.from_chunks(
            iter([b"line 1\n", b"inserted\n", b"line 2\n"])
        )

    monkeypatch.setattr(
        source_annotation_module,
        "load_working_tree_file_as_buffer",
        fake_load_working_tree_file_as_buffer,
    )

    annotated = source_annotation_module.annotate_with_batch_source(
        "file.txt",
        line_changes,
    )

    assert loaded_revisions == ["source-commit:file.txt"]
    assert loaded_working_paths == ["file.txt"]
    assert [line.source_line for line in annotated.lines] == [1, None, 2]

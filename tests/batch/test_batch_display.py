"""Tests for batch display helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import overload

import git_stage_batch.batch.display as display_module
from git_stage_batch.batch.display import (
    annotate_with_batch_source_lines,
    annotate_with_batch_source_working_lines,
    build_display_lines_from_batch_source_lines,
)
from git_stage_batch.batch.ownership import BatchOwnership, DeletionClaim
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.editor import EditorBuffer


class _NoLenByteLines(Sequence[bytes]):
    """Byte-line sequence that fails if display construction asks for length."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = tuple(lines)
        self.accessed_indexes: list[int] = []

    def __len__(self) -> int:
        raise AssertionError("display construction should not require len()")

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[bytes, ...]: ...

    def __getitem__(self, index: int | slice) -> bytes | tuple[bytes, ...]:
        if isinstance(index, slice):
            raise AssertionError("display construction should use indexed reads")
        self.accessed_indexes.append(index)
        return self._lines[index]


def test_display_builder_accepts_non_list_byte_line_sequences(line_sequence):
    """Batch display construction accepts indexed byte-line sequences."""
    source_lines = line_sequence([
        b"line 1\n",
        b"line 2\n",
        b"line 3\n",
    ])
    ownership = BatchOwnership.from_presence_lines(
        ["1,3"],
        [
            DeletionClaim(
                anchor_line=1,
                content_lines=[b"deleted\n"],
            ),
        ],
    )

    display_lines = build_display_lines_from_batch_source_lines(
        source_lines,
        ownership,
        context_lines=0,
    )

    assert [line["content"] for line in display_lines] == [
        "line 1\n",
        "deleted\n",
        "... 1 more line ...\n",
        "line 3\n",
    ]
    assert [line["type"] for line in display_lines] == [
        "claimed",
        "deletion",
        "gap",
        "claimed",
    ]
    assert [line["id"] for line in display_lines] == [1, 2, None, 3]


def test_display_builder_does_not_require_source_line_count():
    """Display construction reads the requested source indexes directly."""
    source_lines = _NoLenByteLines(
        f"line {line_number}\n".encode("utf-8")
        for line_number in range(1, 1001)
    )
    ownership = BatchOwnership.from_presence_lines(["500"], [])

    display_lines = build_display_lines_from_batch_source_lines(
        source_lines,
        ownership,
        context_lines=1,
    )

    assert [line["content"] for line in display_lines] == [
        "line 499\n",
        "line 500\n",
        "line 501\n",
    ]
    assert [line["id"] for line in display_lines] == [None, 1, None]
    assert source_lines.accessed_indexes == [498, 499, 500]


def test_annotate_with_batch_source_lines_accepts_non_list_byte_sequences(line_sequence):
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
        display_module,
        "get_batch_source_for_file",
        lambda path: "source-commit",
    )
    monkeypatch.setattr(
        display_module,
        "load_git_object_as_buffer",
        lambda revision_path: EditorBuffer.from_chunks(
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
        display_module,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    monkeypatch.setattr(
        display_module,
        "get_batch_source_for_file",
        lambda path: "source-commit",
    )

    def fake_load_git_object_as_buffer(revision_path):
        loaded_revisions.append(revision_path)
        return EditorBuffer.from_chunks(iter([b"line 1\n", b"line 2\n"]))

    monkeypatch.setattr(
        display_module,
        "load_git_object_as_buffer",
        fake_load_git_object_as_buffer,
    )

    def fake_load_working_tree_file_as_buffer(path):
        loaded_working_paths.append(path)
        return EditorBuffer.from_chunks(
            iter([b"line 1\n", b"inserted\n", b"line 2\n"])
        )

    monkeypatch.setattr(
        display_module,
        "load_working_tree_file_as_buffer",
        fake_load_working_tree_file_as_buffer,
    )

    annotated = display_module.annotate_with_batch_source(
        "file.txt",
        line_changes,
    )

    assert loaded_revisions == ["source-commit:file.txt"]
    assert loaded_working_paths == ["file.txt"]
    assert [line.source_line for line in annotated.lines] == [1, None, 2]

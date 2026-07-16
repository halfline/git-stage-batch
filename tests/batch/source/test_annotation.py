"""Tests for batch-source line annotation."""

from __future__ import annotations

from git_stage_batch.batch.source import annotation as source_annotation_module
from git_stage_batch.batch.source.annotation import (
    acquire_batch_source_mapping,
    annotate_with_batch_source_mapping,
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
        lambda revision_path, **_kwargs: LineBuffer.from_chunks(
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

    def fake_read_git_object_buffer_or_none(revision_path, **_kwargs):
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


def test_acquired_mapping_annotates_multiple_hunks_with_one_match(monkeypatch):
    """One file-scoped mapping should be reusable across several hunks."""
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
            ),
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=2,
                text_bytes=b"inserted\n",
            ),
        ],
    )
    calls = []
    original_match_lines = source_annotation_module.match_lines

    monkeypatch.setattr(
        source_annotation_module,
        "read_git_object_buffer_or_none",
        lambda _refspec, **_kwargs: LineBuffer.from_chunks([b"line 1\n"]),
    )

    def counting_match_lines(source_lines, working_lines, **kwargs):
        calls.append((source_lines, working_lines))
        return original_match_lines(source_lines, working_lines, **kwargs)

    monkeypatch.setattr(
        source_annotation_module,
        "match_lines",
        counting_match_lines,
    )

    with LineBuffer.from_chunks([b"line 1\n", b"inserted\n"]) as working_lines:
        with acquire_batch_source_mapping(
            "file.txt",
            batch_source_commit="source-commit",
            working_lines=working_lines,
        ) as mapping:
            first = annotate_with_batch_source_mapping(line_changes, mapping)
            second = annotate_with_batch_source_mapping(line_changes, mapping)

    assert len(calls) == 1
    assert [line.source_line for line in first.lines] == [1, None]
    assert [line.source_line for line in second.lines] == [1, None]


def test_acquired_mapping_propagates_invocation_spool(tmp_path, monkeypatch):
    """Source loading and matching should share the job scratch directory."""
    loaded_spool_directories = []
    matching_spool_directories = []
    original_match_lines = source_annotation_module.match_lines

    def load_source(_refspec, *, spool_dir=None):
        loaded_spool_directories.append(spool_dir)
        return LineBuffer.from_chunks(
            [b"line 1\n"],
            spool_dir=spool_dir,
        )

    def record_match(source_lines, working_lines, *, spool_dir=None):
        matching_spool_directories.append(spool_dir)
        return original_match_lines(
            source_lines,
            working_lines,
            spool_dir=spool_dir,
        )

    monkeypatch.setattr(
        source_annotation_module,
        "read_git_object_buffer_or_none",
        load_source,
    )
    monkeypatch.setattr(
        source_annotation_module,
        "match_lines",
        record_match,
    )
    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()

    with LineBuffer.from_chunks(
        [b"line 1\n"],
        spool_dir=spool_dir,
    ) as working_lines:
        with acquire_batch_source_mapping(
            "file.txt",
            batch_source_commit="source-commit",
            working_lines=working_lines,
            spool_dir=spool_dir,
        ):
            pass

    assert loaded_spool_directories == [spool_dir]
    assert matching_spool_directories == [spool_dir]

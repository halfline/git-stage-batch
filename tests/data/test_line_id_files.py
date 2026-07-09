"""Tests for persisted processed line ID files."""

from git_stage_batch.data.line_id_files import (
    read_line_ids_file,
    write_line_ids_file,
)


def test_read_line_ids_file_returns_empty_list_for_missing_path(tmp_path):
    line_ids = read_line_ids_file(tmp_path / "missing")

    assert line_ids == []


def test_read_line_ids_file_ignores_non_numeric_lines(tmp_path):
    path = tmp_path / "line-ids"
    path.write_text("3\nnot-a-number\n 5 \n1-2\n")

    line_ids = read_line_ids_file(path)

    assert line_ids == [3, 5]


def test_write_line_ids_file_sorts_and_deduplicates_ids(tmp_path):
    path = tmp_path / "line-ids"

    write_line_ids_file(path, [3, 1, 3, 2])

    assert path.read_text() == "1\n2\n3\n"


def test_write_line_ids_file_writes_empty_file_for_no_ids(tmp_path):
    path = tmp_path / "line-ids"

    write_line_ids_file(path, [])

    assert path.read_text() == ""

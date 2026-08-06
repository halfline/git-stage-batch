"""Tests for terminal-safe path rendering in file reviews."""

from git_stage_batch.data.file_review.records import ReviewSource
from git_stage_batch.git_paths import display_path, terminal_safe_shell_quote
from git_stage_batch.output import file_review
from git_stage_batch.output.file_review_list import (
    FileReviewListEntry,
    print_file_review_list,
)


def test_file_review_list_quotes_control_paths_and_rename_details(capsys):
    """List rows, rename details, and open commands must stay on safe lines."""
    old_path = "old\rname.txt"
    new_path = "evil\x1b[2Jname\nnext.txt"
    entry = FileReviewListEntry(
        path=new_path,
        change_count=1,
        changed_line_count=0,
        addition_count=0,
        deletion_count=0,
        page_count=1,
        rename_old_path=old_path,
        rename_new_path=new_path,
    )

    print_file_review_list(source_label="Changes", entries=[entry])

    output = capsys.readouterr().out
    assert old_path not in output
    assert new_path not in output
    assert "\x1b" not in output
    assert "\r" not in output
    assert display_path(old_path) in output
    assert display_path(new_path) in output
    assert terminal_safe_shell_quote(new_path) in output


def test_file_review_header_quotes_control_path(monkeypatch, capsys):
    """The single-file review header must not execute pathname controls."""
    path = "evil\x1b[2Jname\nnext.txt"
    monkeypatch.setattr(file_review.Colors, "enabled", lambda: False)

    file_review._print_header(
        path,
        source_label="Changes",
        source=ReviewSource.FILE_VS_HEAD,
        batch_name=None,
        note=None,
        shown_pages=(1,),
        page_count=1,
        shown_change_spec="1",
        shown_change_count=1,
        shown_line_spec="1-2",
        total_changes=1,
        opened_near_selected_hunk=False,
    )

    output = capsys.readouterr().out
    assert path not in output
    assert "\x1b" not in output
    assert display_path(path) in output

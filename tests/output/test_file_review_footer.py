"""Tests for terminal-safe file review footer output."""

import re
import shlex

from git_stage_batch.data.file_review.records import ReviewSource
from git_stage_batch.git_paths import display_path, terminal_safe_shell_quote
from git_stage_batch.output import file_review_footer
from git_stage_batch.output.colors import Colors
from git_stage_batch.output.file_review_footer_hints import FileReviewFooterHint


def test_file_review_footer_aligns_actions_by_terminal_width(monkeypatch, capsys):
    """Wide action labels should align with narrow action labels."""
    monkeypatch.setattr(
        file_review_footer.file_review_footer_hints,
        "build_file_review_footer_hints",
        lambda *args, **kwargs: (
            FileReviewFooterHint("界", "wide-command"),
            FileReviewFooterHint("a", "narrow-command"),
        ),
    )
    monkeypatch.setattr(file_review_footer.Colors, "enabled", lambda: False)

    file_review_footer.print_file_review_footer(
        "file.txt",
        shown_pages=(1,),
        page_count=1,
        shown_change_spec="1",
        shown_change_count=1,
        shown_line_spec="1",
        complete_line_action_selections=[],
        total_changes=1,
        command_source_args="",
        source=ReviewSource.FILE_VS_HEAD,
        batch_name=None,
    )

    output_lines = capsys.readouterr().out.splitlines()
    assert "界  wide-command" in output_lines
    assert "a   narrow-command" in output_lines


def test_file_review_footer_quotes_control_paths_in_status_and_commands(
    monkeypatch,
    capsys,
):
    """A pathname must not inject terminal controls through either footer field."""
    path = "evil\x1b[2Jname\nnext.txt"
    command = f"git-stage-batch include --file {shlex.quote(path)}"
    monkeypatch.setattr(
        file_review_footer.file_review_footer_hints,
        "build_file_review_footer_hints",
        lambda *args, **kwargs: (FileReviewFooterHint("include", command),),
    )
    monkeypatch.setattr(file_review_footer.Colors, "enabled", lambda: False)

    file_review_footer.print_file_review_footer(
        path,
        shown_pages=(1,),
        page_count=1,
        shown_change_spec="1",
        shown_change_count=1,
        shown_line_spec="1",
        complete_line_action_selections=[],
        total_changes=1,
        command_source_args="",
        source=ReviewSource.FILE_VS_HEAD,
        batch_name=None,
    )

    output = capsys.readouterr().out
    assert path not in output
    assert "\x1b" not in output
    assert display_path(path) in output
    assert terminal_safe_shell_quote(path) in output


def test_styled_footer_command_preserves_apostrophe_path_as_one_argument():
    """Styling must not split an already shell-quoted pathname."""
    path = "a'b c"
    command = f"git-stage-batch show --file {shlex.quote(path)}"

    styled = file_review_footer._style_footer_command(command)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", styled)

    assert Colors.BOLD in styled
    assert shlex.split(plain) == ["git-stage-batch", "show", "--file", path]

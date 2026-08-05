"""Tests for terminal-safe file review footer output."""

from git_stage_batch.data.file_review.records import ReviewSource
from git_stage_batch.output import file_review_footer
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

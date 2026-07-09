import git_stage_batch.commands.selection.selected_hunk_refresh as selected_hunk_refresh
from git_stage_batch.data.selected_change.hunk_recalculation import (
    RecalculateSelectedHunkResult,
)


def test_recalculate_selected_hunk_for_command_displays_refreshed_change(monkeypatch):
    calls = []
    display_calls = []
    next_change_calls = []

    def fake_recalculate(file_path, *, auto_advance=None):
        calls.append((file_path, auto_advance))
        return RecalculateSelectedHunkResult.RECALCULATED

    monkeypatch.setattr(
        selected_hunk_refresh,
        "recalculate_selected_hunk_for_file",
        fake_recalculate,
    )
    monkeypatch.setattr(
        selected_hunk_refresh,
        "show_selected_change",
        lambda: display_calls.append(True),
    )
    monkeypatch.setattr(
        selected_hunk_refresh,
        "show_next_unprocessed_change",
        lambda: next_change_calls.append(True),
    )

    selected_hunk_refresh.recalculate_selected_hunk_for_command(
        "file.txt",
        auto_advance=False,
    )

    assert calls == [("file.txt", False)]
    assert display_calls == [True]
    assert next_change_calls == []


def test_recalculate_selected_hunk_for_command_shows_next_change(monkeypatch):
    next_change_calls = []

    def fake_recalculate(file_path, *, auto_advance=None):
        assert file_path == "file.txt"
        assert auto_advance is True
        return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE

    monkeypatch.setattr(
        selected_hunk_refresh,
        "recalculate_selected_hunk_for_file",
        fake_recalculate,
    )
    monkeypatch.setattr(
        selected_hunk_refresh,
        "show_next_unprocessed_change",
        lambda: next_change_calls.append(True),
    )

    selected_hunk_refresh.recalculate_selected_hunk_for_command(
        "file.txt",
        auto_advance=True,
    )

    assert next_change_calls == [True]


def test_recalculate_selected_hunk_for_command_reports_empty_hunk(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        selected_hunk_refresh,
        "recalculate_selected_hunk_for_file",
        lambda _file_path, *, auto_advance=None: (
            RecalculateSelectedHunkResult.NO_MORE_LINES
        ),
    )

    selected_hunk_refresh.recalculate_selected_hunk_for_command("file.txt")

    assert "No more lines in this hunk." in capsys.readouterr().err


def test_recalculate_selected_hunk_for_command_reports_pending_diff_failure(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        selected_hunk_refresh,
        "recalculate_selected_hunk_for_file",
        lambda _file_path, *, auto_advance=None: (
            RecalculateSelectedHunkResult.NO_PENDING_HUNKS
        ),
    )

    selected_hunk_refresh.recalculate_selected_hunk_for_command("file.txt")

    assert "No pending hunks." in capsys.readouterr().err


def test_refresh_selected_hunk_after_line_action_prints_header_before_refresh(
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(
        selected_hunk_refresh,
        "print_remaining_line_changes_header",
        lambda file_path: calls.append(("header", file_path)),
    )
    monkeypatch.setattr(
        selected_hunk_refresh,
        "recalculate_selected_hunk_for_command",
        lambda file_path, *, auto_advance=None: calls.append(
            ("refresh", file_path, auto_advance)
        ),
    )

    selected_hunk_refresh.refresh_selected_hunk_after_line_action(
        "file.txt",
        auto_advance=True,
    )

    assert calls == [
        ("header", "file.txt"),
        ("refresh", "file.txt", True),
    ]

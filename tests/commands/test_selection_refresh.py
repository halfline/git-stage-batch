import git_stage_batch.commands.selection.selected_hunk_refresh as selected_hunk_refresh
from git_stage_batch.data.hunk_tracking import RecalculateSelectedHunkResult


def test_recalculate_selected_hunk_for_command_delegates_without_show(monkeypatch):
    calls = []
    show_calls = []

    def fake_recalculate(file_path, *, auto_advance=None):
        calls.append((file_path, auto_advance))
        return RecalculateSelectedHunkResult.RECALCULATED

    monkeypatch.setattr(
        selected_hunk_refresh,
        "recalculate_selected_hunk_for_file",
        fake_recalculate,
    )
    monkeypatch.setattr(
        "git_stage_batch.commands.show.command_show",
        lambda: show_calls.append(True),
    )

    selected_hunk_refresh.recalculate_selected_hunk_for_command(
        "file.txt",
        auto_advance=False,
    )

    assert calls == [("file.txt", False)]
    assert show_calls == []


def test_recalculate_selected_hunk_for_command_shows_next_change(monkeypatch):
    show_calls = []

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
        "git_stage_batch.commands.show.command_show",
        lambda: show_calls.append(True),
    )

    selected_hunk_refresh.recalculate_selected_hunk_for_command(
        "file.txt",
        auto_advance=True,
    )

    assert show_calls == [True]

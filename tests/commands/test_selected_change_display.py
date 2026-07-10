from __future__ import annotations

import git_stage_batch.commands.selection.selected_change_display as selected_change_display


def _stub_atomic_selected_changes(monkeypatch):
    monkeypatch.setattr(
        selected_change_display._selected_store,
        "load_selected_rename_change",
        lambda: None,
    )
    monkeypatch.setattr(
        selected_change_display._selected_store,
        "load_selected_text_deletion_change",
        lambda: None,
    )
    monkeypatch.setattr(
        selected_change_display._selected_store,
        "load_selected_gitlink_change",
        lambda: None,
    )
    monkeypatch.setattr(
        selected_change_display._selected_store,
        "load_selected_binary_file",
        lambda: None,
    )


def test_show_selected_change_prefers_line_state(monkeypatch, tmp_path):
    _stub_atomic_selected_changes(monkeypatch)
    patch_path = tmp_path / "selected.patch"
    patch_path.write_text("raw patch\n")
    saved_line_changes = object()
    printed = []

    monkeypatch.setattr(
        selected_change_display,
        "get_selected_hunk_patch_file_path",
        lambda: patch_path,
    )
    monkeypatch.setattr(
        selected_change_display._line_state,
        "load_line_changes_from_state",
        lambda: saved_line_changes,
    )
    monkeypatch.setattr(
        selected_change_display._selected_store,
        "load_line_changes_from_patch_path",
        lambda _patch_path: (_ for _ in ()).throw(
            AssertionError("raw patch fallback should not run")
        ),
    )
    monkeypatch.setattr(
        selected_change_display,
        "print_line_level_changes",
        printed.append,
    )

    selected_change_display.show_selected_change()

    assert printed == [saved_line_changes]


def test_show_selected_change_falls_back_to_patch(monkeypatch, tmp_path):
    _stub_atomic_selected_changes(monkeypatch)
    patch_path = tmp_path / "selected.patch"
    patch_path.write_text("raw patch\n")
    fallback_line_changes = object()
    printed = []

    monkeypatch.setattr(
        selected_change_display,
        "get_selected_hunk_patch_file_path",
        lambda: patch_path,
    )
    monkeypatch.setattr(
        selected_change_display._line_state,
        "load_line_changes_from_state",
        lambda: None,
    )
    monkeypatch.setattr(
        selected_change_display._selected_store,
        "load_line_changes_from_patch_path",
        lambda called_patch_path: (
            fallback_line_changes
            if called_patch_path == patch_path else
            None
        ),
    )
    monkeypatch.setattr(
        selected_change_display,
        "print_line_level_changes",
        printed.append,
    )

    selected_change_display.show_selected_change()

    assert printed == [fallback_line_changes]

"""Tests for batch-source replacement previews."""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest

from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.data.file_review.records import FileReviewAction
from git_stage_batch.exceptions import CommandError
import git_stage_batch.commands.batch_source.replacement_previews as previews


class _OwnershipContext(AbstractContextManager):
    def __init__(self, ownership: object) -> None:
        self.ownership = ownership

    def __enter__(self) -> object:
        return self.ownership

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _ReplacementView(AbstractContextManager):
    def __init__(self, source_buffer: LineBuffer, ownership: object) -> None:
        self.source_buffer = source_buffer
        self.ownership = ownership

    def __enter__(self) -> "_ReplacementView":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.source_buffer.close()
        return None


def test_print_batch_source_replacement_preview_prints_diff(
    monkeypatch,
    capsys,
):
    """Replacement preview printing should wire selection, ownership, and diff."""
    ownership = object()
    replacement_ownership = object()
    batch_buffer = LineBuffer.from_bytes(b"old\n")
    replacement_buffer = LineBuffer.from_bytes(b"new\n")
    calls = {}

    def load_git_object(spec: str):
        calls["git_object"] = spec
        return batch_buffer

    def translate_selection_ids(batch_name, file_path, selected_ids, action):
        calls["translation"] = (batch_name, file_path, selected_ids, action)
        return {7}, None

    def acquire_ownership(file_meta, source_lines, selection_ids):
        calls["ownership"] = (file_meta, source_lines, selection_ids)
        return _OwnershipContext(ownership)

    def build_replacement_view(source_lines, acquired_ownership, payload):
        calls["replacement"] = (source_lines, acquired_ownership, payload)
        return _ReplacementView(replacement_buffer, replacement_ownership)

    def render_diff(
        file_path,
        before,
        after,
        *,
        label_before,
        label_after,
        context_lines,
    ):
        calls["render"] = (
            file_path,
            before.to_bytes(),
            after.to_bytes(),
            label_before,
            label_after,
            context_lines,
        )
        return "diff\n"

    monkeypatch.setattr(previews, "load_git_object_as_buffer", load_git_object)
    monkeypatch.setattr(
        previews,
        "acquire_batch_ownership_for_display_ids_from_lines",
        acquire_ownership,
    )
    monkeypatch.setattr(
        previews,
        "build_replacement_batch_view_from_lines",
        build_replacement_view,
    )
    monkeypatch.setattr(previews, "render_candidate_buffer_diff", render_diff)
    monkeypatch.setattr(previews, "get_context_lines", lambda: 8)

    previews.print_batch_source_replacement_preview(
        batch_name="cleanup",
        files={
            "notes.txt": {
                "batch_source_commit": "commit",
                "change_type": "modified",
                "mode": "100644",
            },
        },
        file_path="notes.txt",
        selected_ids={3},
        replacement_text="new\n",
        translate_selection_ids=translate_selection_ids,
    )

    captured = capsys.readouterr()
    assert captured.out == "diff\n"
    assert calls["git_object"] == "commit:notes.txt"
    assert calls["translation"] == (
        "cleanup",
        "notes.txt",
        {3},
        FileReviewAction.INCLUDE_FROM_BATCH,
    )
    assert calls["ownership"][2] == {7}
    assert calls["replacement"][1] is ownership
    assert calls["replacement"][2].as_text() == "new\n"
    assert calls["render"] == (
        "notes.txt",
        b"old\n",
        b"new\n",
        "batch",
        "replacement-preview",
        8,
    )


def test_print_batch_source_replacement_preview_rejects_binary_entries():
    """Replacement previews should reject binary batch entries."""
    with pytest.raises(CommandError) as exc_info:
        previews.print_batch_source_replacement_preview(
            batch_name="cleanup",
            files={
                "notes.bin": {
                    "file_type": "binary",
                    "batch_source_commit": "commit",
                    "change_type": "modified",
                    "mode": "100644",
                },
            },
            file_path="notes.bin",
            selected_ids={1},
            replacement_text="new\n",
        )

    assert "binary files" in exc_info.value.message


def test_print_batch_source_replacement_preview_reports_missing_source(
    monkeypatch,
):
    """Replacement previews should report missing batch source content."""
    monkeypatch.setattr(previews, "load_git_object_as_buffer", lambda spec: None)

    with pytest.raises(CommandError) as exc_info:
        previews.print_batch_source_replacement_preview(
            batch_name="cleanup",
            files={
                "notes.txt": {
                    "batch_source_commit": "commit",
                    "change_type": "modified",
                    "mode": "100644",
                },
            },
            file_path="notes.txt",
            selected_ids={1},
            replacement_text="new\n",
        )

    assert "Batch source content is missing for notes.txt." in exc_info.value.message

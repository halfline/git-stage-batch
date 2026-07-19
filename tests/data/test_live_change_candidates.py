"""Resource ownership tests for prepared live-change candidates."""

from __future__ import annotations

import pytest

import git_stage_batch.commands.selection.next_change_display as display_module
import git_stage_batch.data.hunk_tracking as hunk_tracking_module
import git_stage_batch.data.live_change_candidates as candidates_module
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.diff_parser import build_line_changes_from_patch_lines
from git_stage_batch.core.models import SingleHunkPatch
from git_stage_batch.data.live_change_candidates import (
    EligibleLiveChange,
    prepare_live_change,
)


PATCH_LINES = (
    b"--- a/file.txt\n",
    b"+++ b/file.txt\n",
    b"@@ -1 +1 @@\n",
    b"-old\n",
    b"+new\n",
)


class _ScanContext:
    blocked_paths: set[str] = set()
    blocked_hashes: set[str] = set()

    def metadata_for_path(self, _file_path: str) -> dict[str, dict]:
        return {}


def _prepare_without_repository_io(monkeypatch, patch: SingleHunkPatch):
    monkeypatch.setattr(
        candidates_module,
        "build_line_changes_from_patch_lines",
        lambda lines, annotator: build_line_changes_from_patch_lines(
            lines,
            annotator=None,
        ),
    )
    monkeypatch.setattr(
        candidates_module,
        "filter_line_level_change_for_batches",
        lambda line_changes, **_kwargs: line_changes,
    )
    candidate, reason = prepare_live_change(patch, _ScanContext())
    assert reason is None
    assert candidate is not None
    return candidate


def test_prepared_raw_patch_outlives_parser_buffer(monkeypatch):
    parser_buffer = LineBuffer.from_chunks(PATCH_LINES)
    candidate = _prepare_without_repository_io(
        monkeypatch,
        SingleHunkPatch("file.txt", "file.txt", parser_buffer),
    )

    parser_buffer.close()

    assert b"".join(candidate.raw_patch.lines.byte_chunks()) == b"".join(PATCH_LINES)
    candidate.close()
    with pytest.raises(ValueError, match="closed"):
        candidate.raw_patch.lines.byte_chunks().__next__()


def test_prepare_does_not_call_tuple_or_list_on_patch_lines(monkeypatch):
    def forbidden_materialization(*_args, **_kwargs):
        raise AssertionError("patch sequence was materialized")

    monkeypatch.setattr(candidates_module, "tuple", forbidden_materialization, raising=False)
    monkeypatch.setattr(candidates_module, "list", forbidden_materialization, raising=False)

    candidate = _prepare_without_repository_io(
        monkeypatch,
        SingleHunkPatch("file.txt", "file.txt", PATCH_LINES),
    )

    candidate.close()


def _candidate_with_owned_patch() -> EligibleLiveChange:
    patch_buffer = LineBuffer.from_chunks(PATCH_LINES)
    change = build_line_changes_from_patch_lines(PATCH_LINES, annotator=None)
    return EligibleLiveChange(
        change=change,
        stable_hash="hash",
        raw_patch=SingleHunkPatch("file.txt", "file.txt", patch_buffer),
    )


@pytest.mark.parametrize("fail_cache", [False, True])
def test_fetch_next_change_closes_candidate_after_cache(monkeypatch, fail_cache):
    candidate = _candidate_with_owned_patch()
    monkeypatch.setattr(
        hunk_tracking_module,
        "next_eligible_live_change",
        lambda: candidate,
    )

    def cache(*_args):
        if fail_cache:
            raise RuntimeError("cache failed")

    monkeypatch.setattr(hunk_tracking_module._selected_store, "cache_hunk_change", cache)

    if fail_cache:
        with pytest.raises(RuntimeError, match="cache failed"):
            hunk_tracking_module.fetch_next_change()
    else:
        assert hunk_tracking_module.fetch_next_change() is candidate.change

    with pytest.raises(ValueError, match="closed"):
        candidate.raw_patch.lines.byte_chunks().__next__()


@pytest.mark.parametrize("fail_print", [False, True])
def test_show_next_change_closes_candidate_after_display(monkeypatch, fail_print):
    candidate = _candidate_with_owned_patch()
    monkeypatch.setattr(display_module, "next_eligible_live_change", lambda: candidate)
    monkeypatch.setattr(display_module, "_cache_candidate", lambda _candidate: None)
    monkeypatch.setattr(display_module, "clear_last_file_review_state", lambda: None)

    def display(_candidate, *, selectable):
        assert selectable is True
        if fail_print:
            raise RuntimeError("display failed")

    monkeypatch.setattr(display_module, "_print_candidate", display)

    if fail_print:
        with pytest.raises(RuntimeError, match="display failed"):
            display_module.show_next_unprocessed_change()
    else:
        display_module.show_next_unprocessed_change()

    with pytest.raises(ValueError, match="closed"):
        candidate.raw_patch.lines.byte_chunks().__next__()

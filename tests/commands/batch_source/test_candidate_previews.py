"""Tests for batch-source candidate preview helpers."""

from __future__ import annotations

import pytest

import git_stage_batch.commands.batch_source.candidate_previews as candidate_previews


class _Preview:
    def __init__(
        self,
        *,
        candidate_id: str = "candidate-1",
        target_fingerprints: tuple[str, ...] = ("target-before",),
        target_result_fingerprints: tuple[str, ...] = ("target-after",),
    ) -> None:
        self.candidate_id = candidate_id
        self.target_fingerprints = target_fingerprints
        self.target_result_fingerprints = target_result_fingerprints
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _matching_state(preview: _Preview, ordinal: int = 1) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "candidate_id": preview.candidate_id,
        "target_fingerprints": preview.target_fingerprints,
        "target_result_fingerprints": preview.target_result_fingerprints,
    }


def test_candidate_preview_for_ordinal_returns_one_based_preview():
    """Ordinal lookup should use candidate selector numbering."""
    previews = [_Preview(candidate_id="first"), _Preview(candidate_id="second")]

    preview = candidate_previews.candidate_preview_for_ordinal(previews, 2)

    assert preview is previews[1]


@pytest.mark.parametrize("ordinal", [0, 3])
def test_candidate_preview_for_ordinal_returns_none_for_missing_ordinal(
    ordinal: int,
):
    """Ordinal lookup should report out-of-range selectors."""
    previews = [_Preview(candidate_id="first"), _Preview(candidate_id="second")]

    preview = candidate_previews.candidate_preview_for_ordinal(previews, ordinal)

    assert preview is None


def test_candidate_preview_state_matches_stored_state(monkeypatch):
    """Stored preview state should match all selector identity fields."""
    preview = _Preview()
    monkeypatch.setattr(
        candidate_previews,
        "load_candidate_preview_state",
        lambda loaded_preview: _matching_state(loaded_preview),
    )

    assert candidate_previews.candidate_preview_state_matches(preview, 1)


def test_candidate_preview_state_rejects_missing_state(monkeypatch):
    """Missing preview state should not match candidate execution."""
    preview = _Preview()
    monkeypatch.setattr(
        candidate_previews,
        "load_candidate_preview_state",
        lambda _preview: None,
    )

    assert not candidate_previews.candidate_preview_state_matches(preview, 1)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("ordinal", 2),
        ("candidate_id", "other"),
        ("target_fingerprints", ("other-before",)),
        ("target_result_fingerprints", ("other-after",)),
    ],
)
def test_candidate_preview_state_rejects_mismatched_state(
    monkeypatch,
    field_name: str,
    value: object,
):
    """Preview state should reject stale selector identity fields."""
    preview = _Preview()
    state = _matching_state(preview)
    state[field_name] = value
    monkeypatch.setattr(
        candidate_previews,
        "load_candidate_preview_state",
        lambda _preview: state,
    )

    assert not candidate_previews.candidate_preview_state_matches(preview, 1)


def test_close_candidate_previews_closes_every_preview():
    """Preview cleanup should close every preview in order."""
    previews = [_Preview(), _Preview()]

    candidate_previews.close_candidate_previews(previews)

    assert [preview.closed for preview in previews] == [True, True]

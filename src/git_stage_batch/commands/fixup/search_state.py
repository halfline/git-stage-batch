"""Canonical identity and reset handling for fixup suggestions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ...data.suggest_fixup_state import (
    SUGGEST_FIXUP_STATE_SCHEMA_VERSION,
    SuggestFixupSearchState,
    SuggestFixupState,
    clear_suggest_fixup_state,
    suggest_fixup_state_matches_search,
)
from ...fixup.models import FixupRange, FixupUnit
from ...fixup.lineage import lineage_query_ranges


def _range_fingerprint(commit_range: FixupRange) -> str:
    digest = hashlib.sha256(b"git-stage-batch-fixup-range-v1\0")
    for commit in commit_range.commits_newest_first:
        digest.update(commit.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _serialized_ranges(
    ranges: tuple[tuple[int, int], ...],
) -> list[list[int]]:
    return [[start, end] for start, end in ranges]


@dataclass(frozen=True, slots=True)
class SuggestFixupSearchTarget:
    """Exact selected unit and canonical history range for one search."""

    hunk_hash: str
    line_id_ranges: tuple[tuple[int, int], ...] | None
    commit_range: FixupRange
    unit: FixupUnit

    def persisted_search(self) -> SuggestFixupSearchState:
        """Return the canonical state identity persisted between invocations."""
        return {
            "schema_version": SUGGEST_FIXUP_STATE_SCHEMA_VERSION,
            "object_format": self.commit_range.object_format,
            "hunk_hash": self.hunk_hash,
            "line_id_ranges": (
                _serialized_ranges(self.line_id_ranges)
                if self.line_id_ranges is not None
                else None
            ),
            "base_commit": self.commit_range.base_commit,
            "head_commit": self.commit_range.head_commit,
            "range_fingerprint": _range_fingerprint(self.commit_range),
            "file_path": self.unit.path,
            "unit_id": self.unit.unit_id,
            "queried_ranges": _serialized_ranges(
                lineage_query_ranges(self.unit)
            ),
        }


def reset_suggest_fixup_state_for_search(
    *,
    state: SuggestFixupState | None,
    target: SuggestFixupSearchTarget,
) -> SuggestFixupState | None:
    """Return state after clearing data for a different frozen search."""
    if state is not None and not suggest_fixup_state_matches_search(
        state,
        target.persisted_search(),
    ):
        clear_suggest_fixup_state()
        return None
    return state

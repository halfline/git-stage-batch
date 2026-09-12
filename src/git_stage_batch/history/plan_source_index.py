"""Source metadata indexes borrowed by history-plan validation phases."""

from __future__ import annotations

from dataclasses import dataclass

from .models import HistoryCommitSnapshot, HistoryPatchUnit, HistorySnapshot


@dataclass(frozen=True, slots=True)
class PlanSourceIndex:
    """Lookup tables for existing frozen commits and units; no patch content."""

    source_by_id: dict[str, HistoryCommitSnapshot]
    source_positions: dict[str, int]
    unit_by_id: dict[str, HistoryPatchUnit]
    unit_positions: dict[str, dict[str, int]]

    @classmethod
    def build(cls, snapshot: HistorySnapshot) -> PlanSourceIndex:
        return cls(
            source_by_id={commit.commit_id: commit for commit in snapshot.commits},
            source_positions={
                commit.commit_id: index for index, commit in enumerate(snapshot.commits)
            },
            unit_by_id={
                unit.unit_id: unit
                for commit in snapshot.commits
                for unit in commit.units
            },
            unit_positions={
                commit.commit_id: {
                    unit.unit_id: index for index, unit in enumerate(commit.units)
                }
                for commit in snapshot.commits
            },
        )

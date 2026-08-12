"""Bounded acquisition and application of exact history patch units."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Iterator

from ..exceptions import CommandError
from ..fixup.commutation import (
    PatchApplicationResult,
    apply_patch_to_tree_result,
)
from ..fixup.models import FixupUnit
from ..fixup.staged_units import acquire_tree_fixup_units
from ..i18n import _
from .models import HistoryPatchUnit, HistorySnapshot
from .unit_ids import history_unit_id


@dataclass(frozen=True, slots=True)
class HistoryReplayUnit:
    """One source-bound patch unit with its bounded exact patch storage."""

    snapshot: HistoryPatchUnit
    patch: FixupUnit

    @property
    def individually_replayable(self) -> bool:
        """Return whether the unit has an unambiguous standalone patch."""
        return (
            self.patch.patch_buffer is not None
            and self.patch.unsupported_reason
            not in {
                "rename-with-content",
                "file-type-with-content",
            }
        )


@contextmanager
def acquire_history_replay_units(
    snapshot: HistorySnapshot,
    *,
    env: dict[str, str] | None = None,
) -> Iterator[tuple[HistoryReplayUnit, ...]]:
    """Reacquire every exact unit while retaining only bounded patch buffers."""
    acquired: list[HistoryReplayUnit] = []
    with ExitStack() as stack:
        for commit in snapshot.commits:
            patches = stack.enter_context(
                acquire_tree_fixup_units(
                    commit.parent_tree,
                    commit.tree,
                    env=env,
                )
            )
            patch_ids = tuple(
                history_unit_id(commit.commit_id, patch.unit_id) for patch in patches
            )
            expected_ids = tuple(unit.unit_id for unit in commit.units)
            if patch_ids != expected_ids:
                raise CommandError(
                    _(
                        "History source {commit} produced different patch units "
                        "during replay."
                    ).format(commit=commit.commit_id)
                )
            acquired.extend(
                HistoryReplayUnit(snapshot=unit, patch=patch)
                for unit, patch in zip(commit.units, patches, strict=True)
            )
        yield tuple(acquired)


def apply_history_replay_unit(
    tree: str,
    unit: HistoryReplayUnit,
    *,
    env: dict[str, str] | None,
) -> PatchApplicationResult:
    """Apply one independently replayable unit to an isolated tree."""
    if not unit.individually_replayable or unit.patch.patch_buffer is None:
        return PatchApplicationResult(
            status="UNKNOWN",
            tree=None,
            detail=unit.patch.unsupported_reason or "unit-has-no-exact-patch",
        )
    return apply_patch_to_tree_result(
        tree,
        unit.patch.patch_buffer.byte_chunks(),
        three_way=False,
        unidiff_zero=True,
        env=env,
    )

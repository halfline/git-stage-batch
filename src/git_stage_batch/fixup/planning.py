"""Build conservative staged fixup creation plans."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import run_git_command
from ..utils.git_index import git_write_tree
from ..utils.git_repository import get_git_object_format
from .commutation import analyze_placement, tree_for_commit
from .lineage import analyze_lineage
from .models import (
    FixupCreatePlan,
    FixupTargetGroup,
    FixupUnitAnalysis,
    LineageEvidence,
    PlacementEvidence,
    StagedFixupUnit,
)
from .ranges import resolve_fixup_range
from .staged_units import acquire_staged_fixup_units


_AUTOSQUASH_PREFIXES = ("fixup! ", "amend! ", "squash! ")


def _unsupported_analysis(unit: StagedFixupUnit) -> FixupUnitAnalysis:
    return FixupUnitAnalysis(
        unit=unit,
        status="unsupported",
        target=None,
        eligible=False,
        reason_code=unit.unsupported_reason or "unsupported-change",
        lineage=LineageEvidence(
            candidates=(),
            queried_ranges=(),
            queried_line_count=0,
            resolved_line_count=0,
            in_range_line_count=0,
            conclusive=False,
        ),
        placement=PlacementEvidence(
            status="unknown",
            barrier=None,
            commuted_across=(),
            detail=unit.unsupported_reason,
        ),
    )


def _combine_evidence(
    unit: StagedFixupUnit,
    lineage: LineageEvidence,
    placement: PlacementEvidence,
) -> FixupUnitAnalysis:
    if placement.status == "unknown":
        return FixupUnitAnalysis(
            unit=unit,
            status="unknown",
            target=None,
            eligible=False,
            reason_code=placement.detail or "placement-analysis-failed",
            lineage=lineage,
            placement=placement,
        )

    if len(lineage.candidates) > 1:
        return FixupUnitAnalysis(
            unit=unit,
            status="ambiguous",
            target=None,
            eligible=False,
            reason_code="multiple-lineage-candidates",
            lineage=lineage,
            placement=placement,
        )

    lineage_target = lineage.unique_target
    barrier = placement.barrier
    if lineage_target is not None and barrier is not None:
        if lineage_target == barrier:
            return FixupUnitAnalysis(
                unit=unit,
                status="agreed",
                target=barrier,
                eligible=True,
                reason_code="lineage-and-placement-agree",
                lineage=lineage,
                placement=placement,
            )
        return FixupUnitAnalysis(
            unit=unit,
            status="disagreement",
            target=None,
            eligible=False,
            reason_code="lineage-and-placement-disagree",
            lineage=lineage,
            placement=placement,
        )

    if lineage_target is not None and placement.status == "commutes-through":
        return FixupUnitAnalysis(
            unit=unit,
            status="lineage-only",
            target=lineage_target,
            eligible=True,
            reason_code="unique-lineage-and-free-placement",
            lineage=lineage,
            placement=placement,
        )

    if barrier is not None:
        return FixupUnitAnalysis(
            unit=unit,
            status="placement-only",
            target=barrier,
            eligible=False,
            reason_code="placement-barrier-without-lineage",
            lineage=lineage,
            placement=placement,
        )

    return FixupUnitAnalysis(
        unit=unit,
        status="unresolved",
        target=None,
        eligible=False,
        reason_code="no-target-evidence",
        lineage=lineage,
        placement=placement,
    )


def _commit_subject(commit: str) -> str:
    return run_git_command(
        ["show", "-s", "--format=%s", commit],
        requires_index_lock=False,
    ).stdout.rstrip("\n")


def _target_groups(
    analyses: tuple[FixupUnitAnalysis, ...],
    commits_newest_first: tuple[str, ...],
) -> tuple[FixupTargetGroup, ...]:
    unit_ids_by_target: dict[str, list[str]] = defaultdict(list)
    for analysis in analyses:
        if analysis.eligible and analysis.target is not None:
            unit_ids_by_target[analysis.target].append(analysis.unit.unit_id)

    if not unit_ids_by_target:
        return ()

    subjects_by_commit: dict[str, str] = {}
    first_commit_by_subject: dict[str, str] = {}
    # Autosquash retains the first commit for an exact subject while scanning
    # the todo list from oldest to newest.
    for commit in reversed(commits_newest_first):
        subject = _commit_subject(commit)
        subjects_by_commit[commit] = subject
        first_commit_by_subject.setdefault(subject, commit)

    def target_group(commit: str) -> FixupTargetGroup:
        subject = subjects_by_commit[commit]
        subject_selects_target = (
            bool(subject)
            and subject == subject.lstrip()
            and not subject.startswith(_AUTOSQUASH_PREFIXES)
            and first_commit_by_subject[subject] == commit
        )
        hash_qualified = not subject_selects_target
        if hash_qualified:
            fixup_subject = f"fixup! {commit}"
        else:
            fixup_subject = f"fixup! {subject}"
        return FixupTargetGroup(
            target=commit,
            subject=subject,
            fixup_subject=fixup_subject,
            hash_qualified=hash_qualified,
            unit_ids=tuple(unit_ids_by_target[commit]),
        )

    return tuple(
        target_group(commit)
        for commit in reversed(commits_newest_first)
        if commit in unit_ids_by_target
    )


@contextmanager
def acquire_fixup_create_plan(
    boundary: str | None,
) -> Iterator[FixupCreatePlan]:
    """Acquire a frozen plan and its bounded staged-patch buffers."""
    commit_range = resolve_fixup_range(boundary)
    head_tree = tree_for_commit(commit_range.head_commit)
    index_tree = git_write_tree()
    with acquire_staged_fixup_units() as units:
        if not units:
            raise CommandError(_("No staged changes are available for fixup creation."))

        analyses: list[FixupUnitAnalysis] = []
        for unit in units:
            if not unit.is_supported_text:
                analyses.append(_unsupported_analysis(unit))
                continue
            lineage = analyze_lineage(unit, commit_range)
            placement = analyze_placement(unit, commit_range)
            analyses.append(_combine_evidence(unit, lineage, placement))

        current_head = run_git_command(
            ["rev-parse", "--verify", "HEAD^{commit}"],
            requires_index_lock=False,
        ).stdout.strip()
        current_index_tree = git_write_tree()
        if current_head != commit_range.head_commit or current_index_tree != index_tree:
            raise CommandError(
                _(
                    "HEAD or the staged index changed during fixup analysis. "
                    "Run the command again."
                )
            )

        frozen_analyses = tuple(analyses)
        yield FixupCreatePlan(
            schema_version=1,
            object_format=get_git_object_format(),
            commit_range=commit_range,
            head_tree=head_tree,
            index_tree=index_tree,
            units=frozen_analyses,
            groups=_target_groups(
                frozen_analyses,
                commit_range.commits_newest_first,
            ),
        )

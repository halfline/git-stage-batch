"""Exact-evidence fixup-suggestion execution flow."""

from __future__ import annotations

from typing import NoReturn

from ...data.suggest_fixup_state import (
    SuggestFixupState,
    clear_suggest_fixup_state,
)
from ...exceptions import CommandError, exit_with_error
from ...fixup.analysis import (
    combine_fixup_evidence,
    unsupported_fixup_unit_analysis,
)
from ...fixup.commutation import analyze_placement
from ...fixup.lineage import analyze_lineage, analyze_lineage_history
from ...fixup.models import FixupUnitAnalysis, LineageHistoryEvidence
from ...i18n import _
from ...output.fixup_suggest import print_fixup_suggestion
from ...utils.git_command import run_git_command
from .candidate_iteration import (
    advance_suggest_fixup_candidate,
    last_suggest_fixup_candidate,
    suggest_fixup_candidate_commits,
)
from .search_state import reset_suggest_fixup_state_for_search
from .search_targets import (
    SuggestFixupResolvedTarget,
    require_suggest_fixup_target_fresh,
)


def _require_frozen_head(expected_head: str) -> None:
    current_head = run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        requires_index_lock=False,
    ).stdout.strip()
    if current_head != expected_head:
        raise CommandError(
            _("HEAD changed during fixup suggestion analysis. Run the command again.")
        )


def _finish_without_candidate(
    *,
    resolved_target: SuggestFixupResolvedTarget,
    analysis: FixupUnitAnalysis,
    history: LineageHistoryEvidence,
    candidate_commits: tuple[str, ...],
    result: str,
    message: str,
    porcelain: bool,
) -> NoReturn:
    target = resolved_target.search_target
    print_fixup_suggestion(
        commit_range=target.commit_range,
        hunk_hash=target.hunk_hash,
        line_id_ranges=target.line_id_ranges,
        analysis=analysis,
        history=history,
        candidate_commits=candidate_commits,
        candidate=None,
        result=result,
        porcelain=porcelain,
    )
    if porcelain:
        raise SystemExit(1)
    exit_with_error(message)


def run_suggest_fixup_search(
    *,
    state: SuggestFixupState | None,
    resolved_target: SuggestFixupResolvedTarget,
    show_last: bool,
    porcelain: bool,
) -> None:
    """Analyze the exact selected unit and display one candidate."""
    target = resolved_target.search_target
    unit = target.unit

    if unit.is_supported_text:
        lineage = analyze_lineage(unit, target.commit_range)
        placement = analyze_placement(unit, target.commit_range)
        analysis = combine_fixup_evidence(unit, lineage, placement)
    else:
        analysis = unsupported_fixup_unit_analysis(unit)

    history = analyze_lineage_history(unit, target.commit_range)
    candidate_commits = suggest_fixup_candidate_commits(
        target=target,
        history=history,
        placement=analysis.placement,
    )
    _require_frozen_head(target.commit_range.head_commit)
    require_suggest_fixup_target_fresh(resolved_target)

    state = reset_suggest_fixup_state_for_search(
        state=state,
        target=target,
    )

    if show_last:
        candidate = last_suggest_fixup_candidate(
            state=state,
            candidates=candidate_commits,
            history=history,
            placement=analysis.placement,
        )
        if candidate is None:
            _finish_without_candidate(
                resolved_target=resolved_target,
                analysis=analysis,
                history=history,
                candidate_commits=candidate_commits,
                result="no-previous-candidate",
                message=_(
                    "No previous candidate to show.\n"
                    "Run fixup suggest without --last to find a candidate."
                ),
                porcelain=porcelain,
            )
    else:
        candidate = advance_suggest_fixup_candidate(
            state=state,
            target=target,
            candidates=candidate_commits,
            history=history,
            placement=analysis.placement,
        )
        if candidate is None:
            exhausted = state is not None and "last_shown_commit" in state
            if exhausted:
                clear_suggest_fixup_state()
            _finish_without_candidate(
                resolved_target=resolved_target,
                analysis=analysis,
                history=history,
                candidate_commits=candidate_commits,
                result="exhausted" if exhausted else "no-candidates",
                message=(
                    _("No more candidates found.")
                    if exhausted
                    else _(
                        "No commit in the selected range has exact lineage or "
                        "placement evidence for this unit."
                    )
                ),
                porcelain=porcelain,
            )

    print_fixup_suggestion(
        commit_range=target.commit_range,
        hunk_hash=target.hunk_hash,
        line_id_ranges=target.line_id_ranges,
        analysis=analysis,
        history=history,
        candidate_commits=candidate_commits,
        candidate=candidate,
        result="candidate",
        porcelain=porcelain,
    )

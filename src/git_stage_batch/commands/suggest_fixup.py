"""suggest-fixup command entry points."""

from __future__ import annotations

from ..data.session import require_session_started
from ..data.selected_change.loading import require_selected_hunk
from ..fixup.ranges import resolve_fixup_range
from ..utils.session_start_point import require_repository_history
from ..utils.git_repository import require_git_repository
from ..utils.paths import ensure_state_directory_exists
from .fixup.iteration_state import prepare_suggest_fixup_iteration
from .fixup.search_flow import run_suggest_fixup_search
from .fixup.search_targets import (
    acquire_suggest_fixup_hunk_target,
    acquire_suggest_fixup_line_target,
    parse_suggest_fixup_line_selection,
)


def command_suggest_fixup(
    boundary: str | None = None,
    reset: bool = False,
    abort: bool = False,
    show_last: bool = False,
    *,
    porcelain: bool = False
) -> None:
    """Suggest which commit the selected hunk should be fixed up to.

    Iteratively suggests commits supported by exact source-line history or
    patch-placement evidence, starting with the most recent. State is
    automatically reset when the unit or canonical history range changes.

    Args:
        boundary: Commit excluded from the search (default: fork point with
                  upstream, or the canonical base from the prior invocation)
        reset: If True, reset state and start search over from most recent
        abort: If True, clear state and exit without showing candidates
        show_last: If True, re-show the last candidate without advancing
        porcelain: If True, output JSON for scripting instead of human-readable text
    """
    require_git_repository()
    ensure_state_directory_exists()
    require_repository_history()

    iteration_context = prepare_suggest_fixup_iteration(
        boundary=boundary,
        reset=reset,
        abort=abort,
        porcelain=porcelain,
    )
    if iteration_context is None:
        return
    state = iteration_context.state
    effective_boundary = iteration_context.effective_boundary

    require_selected_hunk()
    commit_range = resolve_fixup_range(effective_boundary)
    with acquire_suggest_fixup_hunk_target(
        commit_range,
        porcelain=porcelain,
    ) as resolved_target:
        run_suggest_fixup_search(
            state=state,
            resolved_target=resolved_target,
            show_last=show_last,
            porcelain=porcelain,
        )


def command_suggest_fixup_line(
    line_id_specification: str,
    boundary: str | None = None,
    reset: bool = False,
    abort: bool = False,
    show_last: bool = False,
    *,
    file: str | None = None,
    porcelain: bool = False
) -> None:
    """Suggest which commit specific lines should be fixed up to.

    Iteratively suggests commits supported by the specified exact lines or
    their patch-placement evidence, starting with the most recent. Disjoint
    source ranges remain disjoint throughout analysis.

    Args:
        line_id_specification: Line IDs to analyze (e.g., "1,3,5-7")
        boundary: Commit excluded from the search (default: fork point with
                  upstream, or the canonical base from the prior invocation)
        reset: If True, reset state and start search over from most recent
        abort: If True, clear state and exit without showing candidates
        show_last: If True, re-show the last candidate without advancing
        file: Optional file path whose file-review line IDs should be used
        porcelain: If True, output JSON for scripting instead of human-readable text
    """
    require_git_repository()
    ensure_state_directory_exists()
    require_session_started()
    require_repository_history()

    iteration_context = prepare_suggest_fixup_iteration(
        boundary=boundary,
        reset=reset,
        abort=abort,
        porcelain=porcelain,
    )
    if iteration_context is None:
        return
    state = iteration_context.state
    effective_boundary = iteration_context.effective_boundary

    requested_ids = parse_suggest_fixup_line_selection(line_id_specification)
    commit_range = resolve_fixup_range(effective_boundary)
    with acquire_suggest_fixup_line_target(
        requested_ids,
        commit_range=commit_range,
        file=file,
    ) as resolved_target:
        run_suggest_fixup_search(
            state=state,
            resolved_target=resolved_target,
            show_last=show_last,
            porcelain=porcelain,
        )

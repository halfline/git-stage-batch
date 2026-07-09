"""suggest-fixup command entry points."""

from __future__ import annotations

from ..data.session import require_session_started
from ..utils.git_repository import require_git_repository
from ..utils.paths import ensure_state_directory_exists
from .fixup.boundary import require_suggest_fixup_boundary_range
from .fixup.candidate_iteration import advance_suggest_fixup_candidate
from .fixup.candidate_display import (
    display_suggest_fixup_candidate,
    show_last_suggest_fixup_candidate,
)
from .fixup.iteration_state import prepare_suggest_fixup_iteration
from .fixup.search_targets import (
    require_suggest_fixup_hunk_target,
    require_suggest_fixup_line_target,
)
from .fixup.search_state import reset_suggest_fixup_state_for_search


def command_suggest_fixup(
    boundary: str | None = None,
    reset: bool = False,
    abort: bool = False,
    show_last: bool = False,
    *,
    porcelain: bool = False
) -> None:
    """Suggest which commit the selected hunk should be fixed up to.

    Iteratively suggests commits that modified lines from the selected
    hunk, starting with the most recent and progressing backwards through
    history with each invocation. State is automatically reset when the
    hunk changes or when a different boundary is specified.

    Args:
        boundary: Git ref to use as the lower bound for commit search
                 (default: @{upstream}, or uses boundary from previous
                 invocation)
        reset: If True, reset state and start search over from most recent
        abort: If True, clear state and exit without showing candidates
        show_last: If True, re-show the last candidate without advancing
        porcelain: If True, output JSON for scripting instead of human-readable text
    """
    require_git_repository()
    ensure_state_directory_exists()

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

    resolved_target = require_suggest_fixup_hunk_target(
        effective_boundary,
        porcelain=porcelain,
    )
    line_changes = resolved_target.line_changes
    search_target = resolved_target.search_target

    require_suggest_fixup_boundary_range(effective_boundary)

    state = reset_suggest_fixup_state_for_search(
        state=state,
        target=search_target,
    )

    if show_last:
        show_last_suggest_fixup_candidate(
            state=state,
            effective_boundary=effective_boundary,
            file_path=line_changes.path,
            porcelain=porcelain,
        )
        return

    candidate = advance_suggest_fixup_candidate(
        state=state,
        target=search_target,
    )

    display_suggest_fixup_candidate(
        candidate_commit=candidate.commit,
        iteration=candidate.iteration,
        boundary=effective_boundary,
        file_path=line_changes.path,
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

    Iteratively suggests commits that modified the specified lines from
    the selected hunk, starting with the most recent and progressing
    backwards through history with each invocation. State is
    automatically reset when the hunk changes or when a different
    boundary is specified.

    Args:
        line_id_specification: Line IDs to analyze (e.g., "1,3,5-7")
        boundary: Git ref to use as the lower bound for commit search
                 (default: @{upstream}, or uses boundary from previous
                 invocation)
        reset: If True, reset state and start search over from most recent
        abort: If True, clear state and exit without showing candidates
        show_last: If True, re-show the last candidate without advancing
        file: Optional file path whose file-review line IDs should be used
        porcelain: If True, output JSON for scripting instead of human-readable text
    """
    require_git_repository()
    ensure_state_directory_exists()
    require_session_started()

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

    resolved_target = require_suggest_fixup_line_target(
        line_id_specification,
        boundary=effective_boundary,
        file=file,
    )
    line_changes = resolved_target.line_changes
    search_target = resolved_target.search_target

    require_suggest_fixup_boundary_range(effective_boundary)

    state = reset_suggest_fixup_state_for_search(
        state=state,
        target=search_target,
    )

    if show_last:
        show_last_suggest_fixup_candidate(
            state=state,
            effective_boundary=effective_boundary,
            file_path=line_changes.path,
            porcelain=porcelain,
        )
        return

    candidate = advance_suggest_fixup_candidate(
        state=state,
        target=search_target,
    )

    display_suggest_fixup_candidate(
        candidate_commit=candidate.commit,
        iteration=candidate.iteration,
        boundary=effective_boundary,
        file_path=line_changes.path,
        porcelain=porcelain,
    )

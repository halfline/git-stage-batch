"""suggest-fixup command entry points."""

from __future__ import annotations

from ..core.line_selection import parse_line_selection
from ..data.file_review.fingerprints import compute_current_file_review_diff_fingerprint
from ..data.selected_change.loading import require_selected_hunk
from ..data.file_hunk_display import render_file_as_single_hunk
from ..data.line_state import load_line_changes_from_state
from ..data.selected_change.paths import get_selected_change_file_path
from ..data.session import require_session_started
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import read_text_file_contents
from ..utils.git_repository import require_git_repository
from ..utils.paths import (
    ensure_state_directory_exists,
    get_selected_hunk_hash_file_path,
)
from .fixup.boundary import require_suggest_fixup_boundary_range
from .fixup.candidate_iteration import advance_suggest_fixup_candidate
from .fixup.candidate_display import (
    display_suggest_fixup_candidate,
    show_last_suggest_fixup_candidate,
)
from .fixup.iteration_state import prepare_suggest_fixup_iteration
from .fixup.line_ranges import (
    require_selected_old_line_range,
)
from .fixup.search_targets import require_suggest_fixup_hunk_target
from .fixup.search_state import (
    SuggestFixupSearchTarget,
    reset_suggest_fixup_state_for_search,
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

    if file is None:
        require_selected_hunk()
        line_changes = load_line_changes_from_state()
        if line_changes is None:
            exit_with_error(_("Full hunk state not available. Run 'show' to select a hunk."))

        # Get hunk hash for state tracking
        hunk_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
    else:
        if file == "":
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            target_file = file

        line_changes = render_file_as_single_hunk(target_file)
        if line_changes is None:
            exit_with_error(_("No changes in file '{file}'.").format(file=target_file))

        hunk_hash = "file:" + compute_current_file_review_diff_fingerprint(
            target_file,
            line_changes=line_changes,
        )

    # Parse the line IDs
    requested_ids = parse_line_selection(line_id_specification)
    requested_ids_sorted = sorted(requested_ids)

    line_range = require_selected_old_line_range(line_changes, requested_ids)
    min_line = line_range.min_line
    max_line = line_range.max_line

    require_suggest_fixup_boundary_range(effective_boundary)

    search_target = SuggestFixupSearchTarget(
        hunk_hash=hunk_hash,
        line_ids=requested_ids_sorted,
        boundary=effective_boundary,
        file_path=line_changes.path,
        min_line=min_line,
        max_line=max_line,
    )
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

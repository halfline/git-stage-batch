"""suggest-fixup command infrastructure and helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..core.line_selection import parse_line_selection
from ..data.hunk_tracking import require_current_hunk_and_check_stale
from ..data.line_state import load_current_lines_from_state
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import require_git_repository, run_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_current_hunk_hash_file_path,
    get_suggest_fixup_state_file_path,
)


def _load_suggest_fixup_state() -> dict[str, Any] | None:
    """Load suggest-fixup state from disk, or None if doesn't exist."""
    state_path = get_suggest_fixup_state_file_path()
    if not state_path.exists():
        return None
    try:
        return json.loads(read_text_file_contents(state_path))
    except (json.JSONDecodeError, KeyError):
        return None


def _save_suggest_fixup_state(state: dict[str, Any]) -> None:
    """Save suggest-fixup state to disk."""
    write_text_file_contents(
        get_suggest_fixup_state_file_path(),
        json.dumps(state, indent=2)
    )


def _reset_suggest_fixup_state() -> None:
    """Clear suggest-fixup state."""
    get_suggest_fixup_state_file_path().unlink(missing_ok=True)


def _get_commit_details(commit_hash: str) -> dict[str, str]:
    """Get detailed information about a commit for JSON output.

    Args:
        commit_hash: Full or short commit hash

    Returns:
        Dictionary with hash, full_hash, subject, author, date, relative_date
    """
    try:
        # Get commit details in a structured format
        show_result = run_git_command([
            "show", "--no-patch",
            "--format=%h%n%H%n%s%n%an%n%ai%n%ar",
            commit_hash
        ], check=True)
        lines = show_result.stdout.strip().split('\n')
        if len(lines) >= 6:
            return {
                "hash": lines[0],
                "full_hash": lines[1],
                "subject": lines[2],
                "author": lines[3],
                "date": lines[4],
                "relative_date": lines[5]
            }
    except subprocess.CalledProcessError:
        pass

    # Fallback for errors
    return {
        "hash": commit_hash[:7] if len(commit_hash) > 7 else commit_hash,
        "full_hash": commit_hash,
        "subject": "",
        "author": "",
        "date": "",
        "relative_date": ""
    }


def _should_reset_suggest_fixup_state(
    current_hunk_hash: str,
    line_ids: list[int] | None,
    boundary: str,
    file_path: str,
    min_line: int,
    max_line: int
) -> bool:
    """Check if suggest-fixup state should be reset due to context change."""
    state = _load_suggest_fixup_state()
    if state is None:
        return True

    # Check if any search parameters changed
    return (
        state.get("hunk_hash") != current_hunk_hash or
        state.get("line_ids") != line_ids or
        state.get("boundary") != boundary or
        state.get("file_path") != file_path or
        state.get("min_line") != min_line or
        state.get("max_line") != max_line
    )


def _find_next_fixup_candidate(
    file_path: str,
    min_line: int,
    max_line: int,
    boundary: str,
    last_shown_commit: str | None
) -> str | None:
    """Find the next commit that modified the given line range.

    Returns the commit hash, or None if no more candidates found.
    """
    # Build the git log command
    # If we have a last_shown_commit, search before it
    if last_shown_commit:
        commit_range = f"{boundary}..{last_shown_commit}^"
    else:
        commit_range = f"{boundary}..HEAD"

    try:
        log_result = run_git_command(
            ["log", "-L", f"{min_line},{max_line}:{file_path}", commit_range, "--format=%H", "--max-count=1"],
            check=True
        )
    except subprocess.CalledProcessError:
        return None

    # Parse the first commit (should only be one due to --max-count=1)
    commits = [line.strip() for line in log_result.stdout.splitlines() if line.strip()]
    return commits[0] if commits else None


def _show_commit_diff_for_file(commit_hash: str, file_path: str) -> None:
    """Show the diff from a specific commit for a specific file."""
    try:
        # Show what this commit changed in the file
        show_result = run_git_command(
            ["show", "--format=", "--color=always" if sys.stdout.isatty() else "--color=never", commit_hash, "--", file_path],
            check=True
        )
        if show_result.stdout.strip():
            print()
            print(show_result.stdout.rstrip())
            print()
    except subprocess.CalledProcessError:
        # File might not have been modified in this commit, or other error
        pass


def command_suggest_fixup(
    boundary: str | None = None,
    reset: bool = False,
    abort: bool = False,
    show_last: bool = False,
    *,
    porcelain: bool = False
) -> None:
    """Suggest which commit the current hunk should be fixed up to.

    Iteratively suggests commits that modified lines from the current
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

    # Handle abort flag
    if abort:
        _reset_suggest_fixup_state()
        if not porcelain:
            print(_("Suggest-fixup iteration cleared."), file=sys.stderr)
        return

    # Load current state early to determine effective boundary
    state = _load_suggest_fixup_state()

    # Determine effective boundary
    if boundary is None:
        # No boundary provided - use state's boundary or default
        effective_boundary = state.get("boundary") if state else "@{upstream}"
    else:
        # Boundary was explicitly provided
        effective_boundary = boundary
        # If state exists and boundary changed, auto-reset
        if state and state.get("boundary") != boundary:
            _reset_suggest_fixup_state()
            state = None

    # Handle reset flag
    if reset:
        _reset_suggest_fixup_state()
        state = None

    require_current_hunk_and_check_stale()
    current_lines = load_current_lines_from_state()

    # Get hunk hash for state tracking
    hunk_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()

    # Extract old line numbers from all changed lines
    old_line_numbers = []
    for entry in current_lines.lines:
        if entry.old_line_number is not None:
            old_line_numbers.append(entry.old_line_number)

    if not old_line_numbers:
        if porcelain:
            sys.exit(1)
        exit_with_error(_("No old line numbers found in hunk (all lines may be additions)."))

    # Get the range of old lines
    min_line = min(old_line_numbers)
    max_line = max(old_line_numbers)

    # Validate boundary ref
    try:
        run_git_command(["rev-parse", "--verify", effective_boundary], check=True)
    except subprocess.CalledProcessError:
        exit_with_error(_("Invalid boundary ref: {boundary}").format(boundary=effective_boundary))

    # Check if there are any commits in the range
    try:
        rev_list_result = run_git_command(
            ["rev-list", f"{effective_boundary}..HEAD"],
            check=True
        )
    except subprocess.CalledProcessError:
        exit_with_error(_("Failed to get commit range {boundary}..HEAD").format(boundary=effective_boundary))

    if not rev_list_result.stdout.strip():
        exit_with_error(_("No commits found in range {boundary}..HEAD").format(boundary=effective_boundary))

    # Check if we should reset state due to context change
    if state and _should_reset_suggest_fixup_state(
        hunk_hash, None, effective_boundary, current_lines.path, min_line, max_line
    ):
        _reset_suggest_fixup_state()
        state = None

    # Handle show_last flag
    if show_last:
        if not state or not state.get("last_shown_commit"):
            exit_with_error(
                "No previous candidate to show.\n" +
                "Run suggest-fixup without --last to find a candidate."
            )

        # Re-display the last candidate
        candidate_commit = state["last_shown_commit"]
        iteration = state["iteration"]

        if porcelain:
            # JSON output
            commit_details = _get_commit_details(candidate_commit)
            output = {
                "candidate": commit_details,
                "iteration": iteration,
                "boundary": state.get("boundary", effective_boundary)
            }
            print(json.dumps(output, indent=2))
        else:
            # Display the candidate
            try:
                show_result = run_git_command(
                    ["show", "--no-patch", "--format=%h %s", candidate_commit],
                    check=True
                )
                commit_info = show_result.stdout.strip()
            except subprocess.CalledProcessError:
                commit_info = candidate_commit[:7]

            print(_("Candidate {iteration}: {info}").format(iteration=iteration, info=commit_info))
            _show_commit_diff_for_file(candidate_commit, current_lines.path)
            print(_("Run: git commit --fixup={commit}").format(commit=candidate_commit[:7]))
        return

    # Determine last shown commit and iteration
    last_shown = state["last_shown_commit"] if state else None
    iteration = state["iteration"] + 1 if state else 1

    # Find next candidate
    candidate_commit = _find_next_fixup_candidate(
        current_lines.path,
        min_line,
        max_line,
        effective_boundary,
        last_shown
    )

    if not candidate_commit:
        if iteration == 1:
            exit_with_error(
                f"No commits in range {effective_boundary}..HEAD modified these lines.\n" +
                "The changes may be fixing code from before the boundary."
            )
        else:
            _reset_suggest_fixup_state()
            exit_with_error(_("No more candidates found."))

    # Save state for next invocation
    _save_suggest_fixup_state({
        "hunk_hash": hunk_hash,
        "line_ids": None,
        "boundary": effective_boundary,
        "file_path": current_lines.path,
        "min_line": min_line,
        "max_line": max_line,
        "last_shown_commit": candidate_commit,
        "iteration": iteration
    })

    # Display the candidate
    if porcelain:
        # JSON output
        commit_details = _get_commit_details(candidate_commit)
        output = {
            "candidate": commit_details,
            "iteration": iteration,
            "boundary": effective_boundary
        }
        print(json.dumps(output, indent=2))
    else:
        try:
            show_result = run_git_command(
                ["show", "--no-patch", "--format=%h %s", candidate_commit],
                check=True
            )
            commit_info = show_result.stdout.strip()
        except subprocess.CalledProcessError:
            commit_info = candidate_commit[:7]

        print(_("Candidate {iteration}: {info}").format(iteration=iteration, info=commit_info))
        _show_commit_diff_for_file(candidate_commit, current_lines.path)
        print(_("Run: git commit --fixup={commit}").format(commit=candidate_commit[:7]))


def command_suggest_fixup_line(
    line_id_specification: str,
    boundary: str | None = None,
    reset: bool = False,
    abort: bool = False,
    show_last: bool = False,
    *,
    porcelain: bool = False
) -> None:
    """Suggest which commit specific lines should be fixed up to.

    Iteratively suggests commits that modified the specified lines from
    the current hunk, starting with the most recent and progressing
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
        porcelain: If True, output JSON for scripting instead of human-readable text
    """
    require_git_repository()
    ensure_state_directory_exists()

    # Handle abort flag
    if abort:
        _reset_suggest_fixup_state()
        if not porcelain:
            print(_("Suggest-fixup iteration cleared."), file=sys.stderr)
        return

    # Load current state early to determine effective boundary
    state = _load_suggest_fixup_state()

    # Determine effective boundary
    if boundary is None:
        # No boundary provided - use state's boundary or default
        effective_boundary = state.get("boundary") if state else "@{upstream}"
    else:
        # Boundary was explicitly provided
        effective_boundary = boundary
        # If state exists and boundary changed, auto-reset
        if state and state.get("boundary") != boundary:
            _reset_suggest_fixup_state()
            state = None

    # Handle reset flag
    if reset:
        _reset_suggest_fixup_state()
        state = None

    require_current_hunk_and_check_stale()
    current_lines = load_current_lines_from_state()

    # Get hunk hash for state tracking
    hunk_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()

    # Parse the line IDs
    requested_ids = parse_line_selection(line_id_specification)
    requested_ids_sorted = sorted(requested_ids)

    # Extract old line numbers only for the specified line IDs
    old_line_numbers = []
    for entry in current_lines.lines:
        if entry.id in requested_ids and entry.old_line_number is not None:
            old_line_numbers.append(entry.old_line_number)

    if not old_line_numbers:
        exit_with_error(_("No old line numbers found for specified lines (they may be newly added lines)."))

    # Get the range of old lines
    min_line = min(old_line_numbers)
    max_line = max(old_line_numbers)

    # Validate boundary ref
    try:
        run_git_command(["rev-parse", "--verify", effective_boundary], check=True)
    except subprocess.CalledProcessError:
        exit_with_error(_("Invalid boundary ref: {boundary}").format(boundary=effective_boundary))

    # Check if there are any commits in the range
    try:
        rev_list_result = run_git_command(
            ["rev-list", f"{effective_boundary}..HEAD"],
            check=True
        )
    except subprocess.CalledProcessError:
        exit_with_error(_("Failed to get commit range {boundary}..HEAD").format(boundary=effective_boundary))

    if not rev_list_result.stdout.strip():
        exit_with_error(_("No commits found in range {boundary}..HEAD").format(boundary=effective_boundary))

    # Check if we should reset state due to context change
    if state and _should_reset_suggest_fixup_state(
        hunk_hash, requested_ids_sorted, effective_boundary, current_lines.path, min_line, max_line
    ):
        _reset_suggest_fixup_state()
        state = None

    # Handle show_last flag
    if show_last:
        if not state or not state.get("last_shown_commit"):
            exit_with_error(
                "No previous candidate to show.\n" +
                "Run suggest-fixup without --last to find a candidate."
            )

        # Re-display the last candidate
        candidate_commit = state["last_shown_commit"]
        iteration = state["iteration"]

        if porcelain:
            # JSON output
            commit_details = _get_commit_details(candidate_commit)
            output = {
                "candidate": commit_details,
                "iteration": iteration,
                "boundary": state.get("boundary", effective_boundary)
            }
            print(json.dumps(output, indent=2))
        else:
            # Display the candidate
            try:
                show_result = run_git_command(
                    ["show", "--no-patch", "--format=%h %s", candidate_commit],
                    check=True
                )
                commit_info = show_result.stdout.strip()
            except subprocess.CalledProcessError:
                commit_info = candidate_commit[:7]

            print(_("Candidate {iteration}: {info}").format(iteration=iteration, info=commit_info))
            _show_commit_diff_for_file(candidate_commit, current_lines.path)
            print(_("Run: git commit --fixup={commit}").format(commit=candidate_commit[:7]))
        return

    # Determine last shown commit and iteration
    last_shown = state["last_shown_commit"] if state else None
    iteration = state["iteration"] + 1 if state else 1

    # Find next candidate
    candidate_commit = _find_next_fixup_candidate(
        current_lines.path,
        min_line,
        max_line,
        effective_boundary,
        last_shown
    )

    if not candidate_commit:
        if iteration == 1:
            exit_with_error(
                f"No commits in range {effective_boundary}..HEAD modified these lines.\n" +
                "The changes may be fixing code from before the boundary."
            )
        else:
            _reset_suggest_fixup_state()
            exit_with_error(_("No more candidates found."))

    # Save state for next invocation
    _save_suggest_fixup_state({
        "hunk_hash": hunk_hash,
        "line_ids": requested_ids_sorted,
        "boundary": effective_boundary,
        "file_path": current_lines.path,
        "min_line": min_line,
        "max_line": max_line,
        "last_shown_commit": candidate_commit,
        "iteration": iteration
    })

    # Display the candidate
    if porcelain:
        # JSON output
        commit_details = _get_commit_details(candidate_commit)
        output = {
            "candidate": commit_details,
            "iteration": iteration,
            "boundary": effective_boundary
        }
        print(json.dumps(output, indent=2))
    else:
        try:
            show_result = run_git_command(
                ["show", "--no-patch", "--format=%h %s", candidate_commit],
                check=True
            )
            commit_info = show_result.stdout.strip()
        except subprocess.CalledProcessError:
            commit_info = candidate_commit[:7]

        print(_("Candidate {iteration}: {info}").format(iteration=iteration, info=commit_info))
        _show_commit_diff_for_file(candidate_commit, current_lines.path)
        print(_("Run: git commit --fixup={commit}").format(commit=candidate_commit[:7]))

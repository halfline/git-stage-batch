"""Suggest-fixup search target resolution."""

from __future__ import annotations

from dataclasses import dataclass
import sys

from ...core.line_selection import parse_line_selection
from ...core.models import LineLevelChange
from ...data.file_hunk_display import render_file_as_single_hunk
from ...data.file_review.fingerprints import compute_current_file_review_diff_fingerprint
from ...data.line_state import load_line_changes_from_state
from ...data.selected_change.loading import require_selected_hunk
from ...data.selected_change.paths import get_selected_change_file_path
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.file_io import read_text_file_contents
from ...utils.paths import get_selected_hunk_hash_file_path
from .line_ranges import (
    require_hunk_old_line_range,
    require_selected_old_line_range,
)
from .search_state import SuggestFixupSearchTarget


@dataclass(frozen=True)
class SuggestFixupResolvedTarget:
    """Line changes and persisted search target for suggest-fixup."""

    line_changes: LineLevelChange
    search_target: SuggestFixupSearchTarget


def require_suggest_fixup_hunk_target(
    boundary: str,
    *,
    porcelain: bool,
) -> SuggestFixupResolvedTarget:
    """Return the selected-hunk suggest-fixup search target."""
    require_selected_hunk()
    line_changes = load_line_changes_from_state()

    if line_changes is None:
        if porcelain:
            sys.exit(1)
        exit_with_error(_("Full hunk state not available. Run 'show' to select a hunk."))

    hunk_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
    line_range = require_hunk_old_line_range(
        line_changes,
        porcelain=porcelain,
    )

    return SuggestFixupResolvedTarget(
        line_changes=line_changes,
        search_target=SuggestFixupSearchTarget(
            hunk_hash=hunk_hash,
            line_ids=None,
            boundary=boundary,
            file_path=line_changes.path,
            min_line=line_range.min_line,
            max_line=line_range.max_line,
        ),
    )


def require_suggest_fixup_line_target(
    line_id_specification: str,
    *,
    boundary: str,
    file: str | None,
) -> SuggestFixupResolvedTarget:
    """Return the line-scoped suggest-fixup search target."""
    line_changes, hunk_hash = _load_line_target_source(file)
    requested_ids = parse_line_selection(line_id_specification)
    requested_ids_sorted = sorted(requested_ids)
    line_range = require_selected_old_line_range(line_changes, requested_ids)

    return SuggestFixupResolvedTarget(
        line_changes=line_changes,
        search_target=SuggestFixupSearchTarget(
            hunk_hash=hunk_hash,
            line_ids=requested_ids_sorted,
            boundary=boundary,
            file_path=line_changes.path,
            min_line=line_range.min_line,
            max_line=line_range.max_line,
        ),
    )


def _load_line_target_source(
    file: str | None,
) -> tuple[LineLevelChange, str]:
    if file is None:
        require_selected_hunk()
        line_changes = load_line_changes_from_state()
        if line_changes is None:
            exit_with_error(_("Full hunk state not available. Run 'show' to select a hunk."))

        hunk_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
        return line_changes, hunk_hash

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
    return line_changes, hunk_hash

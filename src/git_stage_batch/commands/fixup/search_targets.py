"""Resolve selected hunks and lines into exact fixup units."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import sys

from ...core.line_selection import LineRanges, parse_line_selection_ranges
from ...core.models import LineLevelChange
from ...data.file_hunk_display import render_file_as_single_hunk
from ...data.file_review.fingerprints import compute_current_file_review_diff_fingerprint
from ...data.index_entries import read_index_entry
from ...data.line_state import load_line_changes_from_state
from ...data.selected_change.loading import require_selected_hunk
from ...data.selected_change.paths import get_selected_change_file_path
from ...exceptions import CommandError, exit_with_error
from ...fixup.models import FixupRange
from ...fixup.commutation import tree_for_commit
from ...fixup.selected_units import acquire_selected_fixup_unit
from ...git_paths import display_path
from ...i18n import _
from ...utils.file_io import read_text_file_contents
from ...utils.git_object_io import list_git_tree_blobs
from ...utils.paths import get_selected_hunk_hash_file_path
from .search_state import SuggestFixupSearchTarget


@dataclass(frozen=True, slots=True)
class SuggestFixupResolvedTarget:
    """Line display and exact canonical search target."""

    line_changes: LineLevelChange
    search_target: SuggestFixupSearchTarget
    live_file_source: bool


def _require_hunk_index_matches_head(
    line_changes: LineLevelChange,
    head_tree: str,
) -> None:
    """Reject index-relative selections that no longer describe HEAD."""
    source_entry = list_git_tree_blobs(
        head_tree,
        (line_changes.path,),
    ).get(line_changes.path)
    if source_entry is None:
        return
    index_entry = read_index_entry(line_changes.path)
    if (
        index_entry is None
        or index_entry.mode != source_entry.mode
        or index_entry.object_id != source_entry.blob_sha
    ):
        raise CommandError(
            _("Index content no longer matches the selected line view")
        )


def require_suggest_fixup_target_fresh(
    resolved_target: SuggestFixupResolvedTarget,
) -> None:
    """Reject a selected view that changed while history was analyzed."""
    if not resolved_target.live_file_source:
        require_selected_hunk()
        return

    target = resolved_target.search_target
    current_line_changes = render_file_as_single_hunk(target.unit.path)
    current_hash = (
        "file:"
        + compute_current_file_review_diff_fingerprint(
            target.unit.path,
            line_changes=current_line_changes,
        )
        if current_line_changes is not None
        else None
    )
    if current_hash != target.hunk_hash:
        raise CommandError(
            _("Index content no longer matches the selected line view")
        )


@contextmanager
def acquire_suggest_fixup_hunk_target(
    commit_range: FixupRange,
    *,
    porcelain: bool,
) -> Iterator[SuggestFixupResolvedTarget]:
    """Acquire the selected hunk as one exact fixup unit."""
    require_selected_hunk()
    line_changes = load_line_changes_from_state()

    if line_changes is None:
        if porcelain:
            sys.exit(1)
        exit_with_error(_("Full hunk state not available. Run 'show' to select a hunk."))

    hunk_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
    head_tree = tree_for_commit(commit_range.head_commit)
    _require_hunk_index_matches_head(line_changes, head_tree)
    with acquire_selected_fixup_unit(
        line_changes,
        None,
        source_tree=head_tree,
    ) as unit:
        yield SuggestFixupResolvedTarget(
            line_changes=line_changes,
            search_target=SuggestFixupSearchTarget(
                hunk_hash=hunk_hash,
                line_id_ranges=None,
                commit_range=commit_range,
                unit=unit,
            ),
            live_file_source=False,
        )


@contextmanager
def acquire_suggest_fixup_line_target(
    requested_ids: LineRanges,
    *,
    commit_range: FixupRange,
    file: str | None,
) -> Iterator[SuggestFixupResolvedTarget]:
    """Acquire selected display lines as one exact fixup unit."""
    line_changes, hunk_hash, live_file_source = _load_line_target_source(file)
    head_tree = tree_for_commit(commit_range.head_commit)
    if not live_file_source:
        _require_hunk_index_matches_head(line_changes, head_tree)
    with acquire_selected_fixup_unit(
        line_changes,
        requested_ids,
        source_tree=head_tree,
    ) as unit:
        yield SuggestFixupResolvedTarget(
            line_changes=line_changes,
            search_target=SuggestFixupSearchTarget(
                hunk_hash=hunk_hash,
                line_id_ranges=requested_ids.ranges(),
                commit_range=commit_range,
                unit=unit,
            ),
            live_file_source=live_file_source,
        )


def parse_suggest_fixup_line_selection(specification: str) -> LineRanges:
    """Parse line IDs at the command boundary before history analysis."""
    try:
        return parse_line_selection_ranges(specification)
    except ValueError as error:
        exit_with_error(str(error))


def _load_line_target_source(
    file: str | None,
) -> tuple[LineLevelChange, str, bool]:
    if file is None:
        require_selected_hunk()
        line_changes = load_line_changes_from_state()
        if line_changes is None:
            exit_with_error(_("Full hunk state not available. Run 'show' to select a hunk."))

        hunk_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
        return line_changes, hunk_hash, False

    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
    else:
        target_file = file

    line_changes = render_file_as_single_hunk(target_file)
    if line_changes is None:
        exit_with_error(
            _("No changes in file '{file}'.").format(
                file=display_path(target_file)
            )
        )

    hunk_hash = "file:" + compute_current_file_review_diff_fingerprint(
        target_file,
        line_changes=line_changes,
    )
    return line_changes, hunk_hash, True

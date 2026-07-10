"""Selected hunk filtering for cached line-level views."""

from __future__ import annotations

import json

from ...batch.attribution import build_file_attribution
from ...batch.attribution_projection import filter_owned_diff_fragments
from ...utils.file_io import write_text_file_contents
from ...utils.paths import get_line_changes_json_file_path
from .. import change_freshness as _change_freshness
from .. import consumed_replacement_masks as _consumed_replacement_masks
from .. import line_state as _line_state
from ..consumed_selections import read_consumed_file_metadata


def apply_line_level_batch_filter_to_cached_hunk(
    *,
    batch_metadata_by_name: dict[str, dict] | None = None,
) -> bool:
    """Filter cached hunk using file-centric ownership attribution.

    File-centric blame-like approach:
    1. Build complete file attribution (all ownership-relevant units + batch owners)
    2. Project attribution onto diff fragments
    3. Filter owned fragments

    Returns:
        True if hunk should be skipped (all lines filtered), False otherwise
    """
    line_changes = _line_state.load_line_changes_from_state()
    if line_changes is None:
        return True

    file_path = line_changes.path

    if (
        not line_changes.lines
        and _change_freshness.empty_text_lifecycle_change_is_batched(
            file_path,
            batch_metadata_by_name=batch_metadata_by_name,
        )
    ):
        return True

    attribution = build_file_attribution(
        file_path,
        batch_metadata_by_name=batch_metadata_by_name,
        supplemental_batch_metadata=_consumed_batch_metadata(file_path),
    )
    should_skip, filtered_line_changes = filter_owned_diff_fragments(
        line_changes, attribution
    )

    if should_skip:
        return True

    filtered_line_changes = _consumed_replacement_masks.filter_consumed_replacement_masks(
        filtered_line_changes
    )
    if filtered_line_changes is None:
        return True

    write_text_file_contents(
        get_line_changes_json_file_path(),
        json.dumps(
            _line_state.convert_line_changes_to_serializable_dict(
                filtered_line_changes
            ),
            ensure_ascii=False,
            indent=0,
        ),
    )

    return False


def _consumed_batch_metadata(file_path: str) -> dict[str, dict] | None:
    consumed_file_metadata = read_consumed_file_metadata(file_path)
    if consumed_file_metadata is None:
        return None
    return {
        "__consumed__": {
            "files": {
                file_path: consumed_file_metadata,
            },
        },
    }

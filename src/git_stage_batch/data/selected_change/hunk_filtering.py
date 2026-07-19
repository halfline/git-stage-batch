"""Selected hunk filtering for cached line-level views."""

from __future__ import annotations

import json
from dataclasses import asdict

from ...batch.attribution import (
    AttributionMetrics,
    FileAttribution,
    build_file_attribution,
)
from ...batch.attribution_projection import filter_owned_diff_fragments
from ...batch.state.query import list_batch_names, read_batch_metadata_for_batches
from ...core.models import LineLevelChange
from ...utils.file_io import write_text_file_contents
from ...utils.journal import log_journal
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

    filtered_line_changes = filter_line_level_change_for_batches(
        line_changes,
        batch_metadata_by_name=batch_metadata_by_name,
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


def filter_line_level_change_for_batches(
    line_changes: LineLevelChange,
    *,
    batch_metadata_by_name: dict[str, dict] | None = None,
) -> LineLevelChange | None:
    """Return the unowned portion of a live line change, or ``None``."""
    file_path = line_changes.path
    if batch_metadata_by_name is None:
        batch_metadata_by_name = read_batch_metadata_for_batches(list_batch_names())
    if _empty_lifecycle_change_is_batched(
        line_changes,
        batch_metadata_by_name=batch_metadata_by_name,
    ):
        return None
    consumed_file_metadata = read_consumed_file_metadata(file_path)

    attribution_metrics = AttributionMetrics()
    attribution = build_file_attribution(
        file_path,
        batch_metadata_by_name=batch_metadata_by_name,
        supplemental_batch_metadata=_consumed_batch_metadata(
            file_path,
            consumed_file_metadata,
        ),
        metrics=attribution_metrics,
    )
    log_journal(
        "file_attribution_complete",
        file_path=file_path,
        **asdict(attribution_metrics),
    )
    return _filter_line_level_change_with_prepared_resources(
        line_changes,
        attribution=attribution,
        consumed_file_metadata=consumed_file_metadata,
    )


def filter_line_level_change_with_attribution(
    line_changes: LineLevelChange,
    *,
    attribution: FileAttribution,
    batch_metadata_by_name: dict[str, dict],
    consumed_file_metadata: dict | None,
) -> LineLevelChange | None:
    """Filter one hunk from caller-supplied file attribution and metadata."""
    if _empty_lifecycle_change_is_batched(
        line_changes,
        batch_metadata_by_name=batch_metadata_by_name,
    ):
        return None

    return _filter_line_level_change_with_prepared_resources(
        line_changes,
        attribution=attribution,
        consumed_file_metadata=consumed_file_metadata,
    )


def _empty_lifecycle_change_is_batched(
    line_changes: LineLevelChange,
    *,
    batch_metadata_by_name: dict[str, dict],
) -> bool:
    return not line_changes.lines and (
        _change_freshness.empty_text_lifecycle_change_is_batched(
            line_changes.path,
            batch_metadata_by_name=batch_metadata_by_name,
        )
    )


def _filter_line_level_change_with_prepared_resources(
    line_changes: LineLevelChange,
    *,
    attribution: FileAttribution,
    consumed_file_metadata: dict | None,
) -> LineLevelChange | None:
    """Project attribution and replacement masks without repository I/O."""

    should_skip, filtered_line_changes = filter_owned_diff_fragments(
        line_changes,
        attribution,
    )
    if should_skip:
        return None

    return _consumed_replacement_masks.filter_consumed_replacement_masks_with_metadata(
        filtered_line_changes,
        file_metadata=consumed_file_metadata,
    )


def _consumed_batch_metadata(
    file_path: str,
    consumed_file_metadata: dict | None,
) -> dict[str, dict] | None:
    if consumed_file_metadata is None:
        return None
    return {
        "__consumed__": {
            "files": {
                file_path: consumed_file_metadata,
            },
        },
    }

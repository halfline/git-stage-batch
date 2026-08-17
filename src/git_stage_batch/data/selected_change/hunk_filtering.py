"""Selected hunk filtering for cached line-level views."""

from __future__ import annotations

import json
from dataclasses import asdict

from ...batch.attribution import (
    AttributionMetrics,
    FileAttribution,
    build_file_attribution,
)
from ...batch.attribution_projection import (
    filter_owned_diff_fragments,
    filter_owned_diff_fragments_with_owners,
)
from ...batch.state.query import list_batch_names, read_batch_metadata_for_batches
from ...batch.state.metadata_types import BatchFileMetadataDict, BatchMetadataDict
from ...core.models import LineLevelChange
from ...utils.file_io import write_text_file_contents
from ...utils.journal import log_journal
from ...utils.paths import get_line_changes_json_file_path
from .. import change_freshness as _change_freshness
from ..text_lifecycle_detection import detect_empty_text_lifecycle_change
from .. import consumed_replacement_masks as _consumed_replacement_masks
from .. import line_state as _line_state
from ..consumed_selections import read_consumed_file_metadata
from ..applied_batch_overlays import (
    AppliedBatchOverlayView,
    fresh_applied_batch_overlay_for_path,
    is_applied_batch_overlay_owner,
)


def apply_line_level_batch_filter_to_cached_hunk(
    *,
    batch_metadata_by_name: dict[str, BatchMetadataDict] | None = None,
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
    batch_metadata_by_name: dict[str, BatchMetadataDict] | None = None,
    applied_overlay: AppliedBatchOverlayView | None = None,
    masked_batch_names: set[str] | None = None,
) -> LineLevelChange | None:
    """Return the unowned portion of a live line change, or ``None``."""
    filtered, masked_names = filter_line_level_change_for_batches_with_owners(
        line_changes,
        batch_metadata_by_name=batch_metadata_by_name,
        applied_overlay=applied_overlay,
    )
    if masked_batch_names is not None:
        masked_batch_names.update(masked_names)
    return filtered


def filter_line_level_change_for_batches_with_owners(
    line_changes: LineLevelChange,
    *,
    batch_metadata_by_name: dict[str, BatchMetadataDict] | None = None,
    applied_overlay: AppliedBatchOverlayView | None = None,
) -> tuple[LineLevelChange | None, frozenset[str]]:
    """Return an unowned change plus named owners that hid any fragments."""
    file_path = line_changes.path
    if batch_metadata_by_name is None:
        batch_metadata_by_name = read_batch_metadata_for_batches(list_batch_names())
    if applied_overlay is None:
        applied_overlay = fresh_applied_batch_overlay_for_path(
            file_path,
            batch_metadata_by_name=batch_metadata_by_name,
        )
    if _empty_lifecycle_change_is_batched(
        line_changes,
        batch_metadata_by_name=batch_metadata_by_name,
        applied_overlay=applied_overlay,
    ):
        return None, _empty_lifecycle_owning_batch_names(
            line_changes,
            batch_metadata_by_name=batch_metadata_by_name,
        )
    consumed_file_metadata = read_consumed_file_metadata(file_path)

    attribution_metrics = AttributionMetrics()
    attribution = build_file_attribution(
        file_path,
        batch_metadata_by_name=batch_metadata_by_name,
        supplemental_batch_metadata=supplemental_batch_metadata(
            file_path,
            consumed_file_metadata,
            applied_overlay,
        ),
        supplemental_source_object_by_name=(
            applied_overlay.source_object_by_owner
        ),
        metrics=attribution_metrics,
    )
    log_journal(
        "file_attribution_complete",
        file_path=file_path,
        **asdict(attribution_metrics),
    )
    return _filter_line_level_change_with_prepared_resources_and_owners(
        line_changes,
        attribution=attribution,
        consumed_file_metadata=consumed_file_metadata,
        revealed_owner_names=applied_overlay.revealed_owner_names,
    )


def filter_line_level_change_with_attribution(
    line_changes: LineLevelChange,
    *,
    attribution: FileAttribution,
    batch_metadata_by_name: dict[str, BatchMetadataDict],
    consumed_file_metadata: BatchFileMetadataDict | None,
    captured_empty_lifecycle_is_batched: bool | None = None,
    revealed_owner_names: frozenset[str] = frozenset(),
) -> LineLevelChange | None:
    """Filter one hunk from caller-supplied attribution and metadata.

    ``None`` preserves the established repository-backed empty-lifecycle
    check. File jobs pass a captured boolean to keep worker computation free
    of mutable repository/session reads.
    """
    if captured_empty_lifecycle_is_batched is None:
        lifecycle_is_batched = _empty_lifecycle_change_is_batched(
            line_changes,
            batch_metadata_by_name=batch_metadata_by_name,
        )
    else:
        lifecycle_is_batched = (
            not line_changes.lines
            and captured_empty_lifecycle_is_batched
        )
    if lifecycle_is_batched:
        return None

    return _filter_line_level_change_with_prepared_resources(
        line_changes,
        attribution=attribution,
        consumed_file_metadata=consumed_file_metadata,
        revealed_owner_names=revealed_owner_names,
    )


def _empty_lifecycle_change_is_batched(
    line_changes: LineLevelChange,
    *,
    batch_metadata_by_name: dict[str, BatchMetadataDict],
    applied_overlay: AppliedBatchOverlayView | None = None,
) -> bool:
    if line_changes.lines:
        return False
    if (
        applied_overlay is not None
        and (
            change_type := detect_empty_text_lifecycle_change(line_changes.path)
        )
        is not None
        and change_type.value in applied_overlay.lifecycle_change_types
    ):
        return False
    return (
        _change_freshness.empty_text_lifecycle_change_is_batched(
            line_changes.path,
            batch_metadata_by_name=batch_metadata_by_name,
        )
    )


def _filter_line_level_change_with_prepared_resources(
    line_changes: LineLevelChange,
    *,
    attribution: FileAttribution,
    consumed_file_metadata: BatchFileMetadataDict | None,
    revealed_owner_names: frozenset[str] = frozenset(),
) -> LineLevelChange | None:
    """Project attribution and replacement masks without repository I/O."""

    should_skip, filtered_line_changes = filter_owned_diff_fragments(
        line_changes,
        attribution,
        revealed_owner_names=revealed_owner_names,
    )
    if should_skip:
        return None
    assert filtered_line_changes is not None

    return _consumed_replacement_masks.filter_consumed_replacement_masks_with_metadata(
        filtered_line_changes,
        file_metadata=consumed_file_metadata,
    )


def _filter_line_level_change_with_prepared_resources_and_owners(
    line_changes: LineLevelChange,
    *,
    attribution: FileAttribution,
    consumed_file_metadata: BatchFileMetadataDict | None,
    revealed_owner_names: frozenset[str],
) -> tuple[LineLevelChange | None, frozenset[str]]:
    """Project ownership while retaining user-visible named owners."""
    should_skip, filtered, masked_owners = filter_owned_diff_fragments_with_owners(
        line_changes,
        attribution,
        revealed_owner_names=revealed_owner_names,
    )
    named_owners = frozenset(
        owner
        for owner in masked_owners
        if owner != "__consumed__" and not is_applied_batch_overlay_owner(owner)
    )
    if should_skip:
        return None, named_owners
    assert filtered is not None
    filtered = _consumed_replacement_masks.filter_consumed_replacement_masks_with_metadata(
        filtered,
        file_metadata=consumed_file_metadata,
    )
    return filtered, named_owners


def consumed_batch_metadata(
    file_path: str,
    consumed_file_metadata: BatchFileMetadataDict | None,
) -> dict[str, BatchMetadataDict] | None:
    if consumed_file_metadata is None:
        return None
    consumed_metadata: BatchMetadataDict = {
        "files": {
            file_path: consumed_file_metadata,
        },
    }
    return {"__consumed__": consumed_metadata}


def supplemental_batch_metadata(
    file_path: str,
    consumed_file_metadata: BatchFileMetadataDict | None,
    applied_overlay: AppliedBatchOverlayView,
) -> dict[str, BatchMetadataDict] | None:
    """Combine session masking and durable applied ownership for attribution."""
    combined = dict(applied_overlay.metadata_by_owner)
    consumed = consumed_batch_metadata(file_path, consumed_file_metadata)
    if consumed:
        combined.update(consumed)
    return combined or None


def _empty_lifecycle_owning_batch_names(
    line_changes: LineLevelChange,
    *,
    batch_metadata_by_name: dict[str, BatchMetadataDict],
) -> frozenset[str]:
    change_type = detect_empty_text_lifecycle_change(
        line_changes.path
    )
    if change_type is None:
        return frozenset()
    return frozenset(
        batch_name
        for batch_name, metadata in batch_metadata_by_name.items()
        if metadata.get("files", {}).get(line_changes.path, {}).get("change_type")
        == change_type.value
    )

"""Batch file display rendering without selected-state mutation."""

from __future__ import annotations

from typing import Optional

from . import display as batch_display
from . import file_display_model as _file_display_model
from . import file_mergeability as _file_mergeability
from .ownership import BatchOwnership
from .ownership_metadata_loading import acquire_ownership_for_metadata_dict
from .query import read_batch_metadata
from ..core.line_selection import LineRanges
from ..core.models import (
    RenderedBatchDisplay,
)
from ..utils.repository_buffers import (
    load_git_object_as_buffer,
)
from ..utils.paths import get_context_lines


def render_batch_file_display(
    batch_name: str,
    file_path: str,
    metadata: dict | None = None,
    *,
    probe_mergeability: bool = True,
) -> Optional['RenderedBatchDisplay']:
    """Pure function to render batch file display with gutter ID translation.

    This is a side-effect-free helper that:
    - Reads batch metadata
    - Reads batch source content
    - Reads current working tree content
    - Probes individual line mergeability
    - Builds LineLevelChange with original selection IDs
    - Builds gutter ID mappings

    It does not:
    - Write cache files
    - Mutate selected hunk state
    - Compute patch hashes

    Args:
        batch_name: Name of the batch
        file_path: Specific file to render
        probe_mergeability: If True, compute which batch lines are currently
            mergeable. Multi-file navigational previews can set this to False
            because they do not cache or act on individual lines.

    Returns:
        RenderedBatchDisplay with line changes and gutter ID translation, or None if file not found.
    """
    # Read batch metadata
    if metadata is None:
        metadata = read_batch_metadata(batch_name)
    files = metadata.get("files", {})

    if not files or file_path not in files:
        return None

    file_meta = files[file_path]

    # Get batch source commit and ownership
    batch_source_commit = file_meta["batch_source_commit"]
    with acquire_ownership_for_metadata_dict(file_meta) as ownership:
        return _render_batch_file_display_from_ownership(
            batch_source_commit=batch_source_commit,
            file_path=file_path,
            file_meta=file_meta,
            ownership=ownership,
            probe_mergeability=probe_mergeability,
        )


def _render_batch_file_display_from_ownership(
    *,
    batch_source_commit: str,
    file_path: str,
    file_meta: dict,
    ownership: BatchOwnership,
    probe_mergeability: bool,
) -> Optional['RenderedBatchDisplay']:
    """Render batch file display from already-acquired ownership metadata."""

    batch_source_buffer = load_git_object_as_buffer(
        f"{batch_source_commit}:{file_path}"
    )
    if batch_source_buffer is None:
        return None

    mergeable_id_ranges = LineRanges.empty()
    units = []

    with batch_source_buffer as batch_source_lines:
        # Build display lines (already has correct line IDs matching ownership)
        display_lines = batch_display.build_display_lines_from_batch_source_lines(
            batch_source_lines,
            ownership,
            context_lines=get_context_lines(),
        )

        if probe_mergeability and display_lines:
            mergeability = _file_mergeability.probe_batch_file_mergeability(
                file_path=file_path,
                ownership=ownership,
                display_lines=display_lines,
                batch_source_lines=batch_source_lines,
            )
            mergeable_id_ranges = mergeability.mergeable_id_ranges
            units = mergeability.units

    return _file_display_model.build_rendered_batch_display_model(
        file_path=file_path,
        file_meta=file_meta,
        display_lines=display_lines,
        mergeable_id_ranges=mergeable_id_ranges,
        units=units,
    )

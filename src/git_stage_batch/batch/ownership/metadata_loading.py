"""Scoped loading for serialized ownership metadata."""

from __future__ import annotations

from ...core.buffer import LineBuffer
from ...utils.git_object_io import read_git_blobs_as_bytes
from ...utils.repository_buffers import load_git_blob_as_buffer
from .model import BatchOwnership
from .acquisition import AcquiredBatchOwnership
from .absence_claims import AbsenceClaim
from .claims import (
    PresenceClaim,
    parse_ownership_line_ranges,
    presence_claims_from_source_lines,
)
from .metadata_blobs import (
    deletion_content_blob_ids,
    deletion_reference_blob_ids,
    presence_claim_reference_blob_ids,
    replacement_origin_reference_blob_ids,
)
from .replacement_units import ReplacementUnit


def acquire_ownership_for_metadata_dict(
    data: dict,
) -> AcquiredBatchOwnership[BatchOwnership]:
    """Acquire ownership for metadata with buffered deletion blobs."""
    deletion_metadata = data.get("deletions", [])
    presence_metadata = data.get("presence_claims", [])
    replacement_metadata = data.get("replacement_units", [])
    blob_buffers: dict[str, LineBuffer] = {}
    buffers: list[LineBuffer] = []
    try:
        for blob_sha in deletion_content_blob_ids(deletion_metadata):
            if blob_sha in blob_buffers:
                continue
            buffer = load_git_blob_as_buffer(blob_sha)
            blob_buffers[blob_sha] = buffer
            buffers.append(buffer)

        blob_contents = read_git_blobs_as_bytes(
            [
                *deletion_reference_blob_ids(deletion_metadata),
                *presence_claim_reference_blob_ids(presence_metadata),
                *replacement_origin_reference_blob_ids(replacement_metadata),
            ]
        )
        ownership = ownership_from_metadata_dict(
            data,
            blob_contents=blob_contents,
            deletion_blob_buffers=blob_buffers,
        )
    except Exception:
        for buffer in buffers:
            buffer.close()
        raise

    return AcquiredBatchOwnership(
        ownership=ownership,
        buffers=buffers,
    )


def ownership_from_metadata_dict(
    data: dict,
    *,
    blob_contents: dict[str, bytes],
    deletion_blob_buffers: dict[str, LineBuffer] | None = None,
) -> BatchOwnership:
    """Deserialize ownership metadata from already-loaded blob content."""
    deletion_metadata = data.get("deletions", [])
    presence_metadata = data.get("presence_claims", [])
    legacy_claimed_lines = data.get("claimed_lines", [])
    presence_claims = [
        PresenceClaim.from_dict(claim, blob_contents)
        for claim in presence_metadata
    ]
    if not presence_claims and legacy_claimed_lines:
        presence_claims = presence_claims_from_source_lines(
            parse_ownership_line_ranges(legacy_claimed_lines)
        )
    deletions = [
        AbsenceClaim.from_dict(deletion, blob_contents, deletion_blob_buffers)
        for deletion in deletion_metadata
    ]
    replacement_units = [
        ReplacementUnit.from_dict(replacement, blob_contents)
        for replacement in data.get("replacement_units", [])
    ]
    return BatchOwnership(
        presence_claims=presence_claims,
        deletions=deletions,
        replacement_units=replacement_units,
    )

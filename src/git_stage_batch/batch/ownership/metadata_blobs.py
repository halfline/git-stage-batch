"""Blob discovery helpers for serialized ownership metadata."""

from __future__ import annotations


def read_metadata_blob(
    blob_sha: str | None,
    blob_contents: dict[str, bytes] | None,
) -> bytes | None:
    """Return metadata blob content from a preloaded blob map."""
    if blob_sha is None:
        return None
    if blob_contents is None:
        raise ValueError("metadata blobs must be loaded before deserialization")
    return blob_contents[blob_sha]


def baseline_reference_blob_ids(reference_metadata: dict) -> list[str]:
    """Return blob IDs referenced by one baseline-reference metadata value."""
    if not isinstance(reference_metadata, dict):
        return []
    blob_ids: list[str] = []
    for key in ("after_blob", "before_blob"):
        blob_sha = reference_metadata.get(key)
        if blob_sha:
            blob_ids.append(blob_sha)
    return blob_ids


def baseline_references_blob_ids(references_metadata: dict) -> list[str]:
    """Return blob IDs referenced by baseline references keyed by line."""
    blob_ids: list[str] = []
    for value in references_metadata.values():
        blob_ids.extend(baseline_reference_blob_ids(value))
    return blob_ids


def presence_claim_reference_blob_ids(presence_metadata: list[dict]) -> list[str]:
    """Return blob IDs for baseline references in presence-claim metadata."""
    blob_ids: list[str] = []
    for claim in presence_metadata:
        blob_ids.extend(
            baseline_references_blob_ids(
                claim.get("baseline_references", {})
            )
        )
    return blob_ids


def deletion_content_blob_ids(deletion_metadata: list[dict]) -> list[str]:
    """Return blob IDs for deletion content in ownership metadata."""
    return [
        metadata["blob"]
        for metadata in deletion_metadata
        if "blob" in metadata
    ]


def deletion_reference_blob_ids(deletion_metadata: list[dict]) -> list[str]:
    """Return blob IDs for deletion baseline references in metadata."""
    return [
        blob_id
        for metadata in deletion_metadata
        for blob_id in baseline_reference_blob_ids(
            metadata.get("baseline_reference", {})
        )
    ]


def replacement_origin_reference_blob_ids(
    replacement_metadata: list[dict],
) -> list[str]:
    """Return blob IDs for replacement-origin baseline references."""
    blob_ids: list[str] = []
    for metadata in replacement_metadata:
        origin_metadata = metadata.get("original_unit", {})
        if not isinstance(origin_metadata, dict):
            continue
        blob_ids.extend(
            baseline_reference_blob_ids(
                origin_metadata.get("baseline_reference", {})
            )
        )
    return blob_ids

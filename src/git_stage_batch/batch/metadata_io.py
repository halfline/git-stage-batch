"""Canonical file-backed batch metadata I/O."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .metadata_schema import (
    BatchMetadata,
    decode_batch_metadata,
    encode_batch_metadata,
    metadata_from_application_dict,
)
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import get_batch_metadata_file_path


def read_file_backed_batch_metadata_model(batch_name: str) -> BatchMetadata | None:
    """Read and validate compatibility metadata, if it exists."""
    metadata_path = get_batch_metadata_file_path(batch_name)
    if not metadata_path.exists():
        return None
    return decode_batch_metadata(
        read_text_file_contents(metadata_path),
        expected_batch=batch_name,
    )


def write_file_backed_batch_metadata(
    batch_name: str,
    metadata: Mapping[str, Any],
) -> BatchMetadata:
    """Validate and atomically write current-schema compatibility metadata."""
    metadata_path = get_batch_metadata_file_path(batch_name)
    if metadata_path.exists():
        original_payload = read_text_file_contents(metadata_path)
        # Validate before replacing so unknown future schemas are preserved.
        decode_batch_metadata(original_payload, expected_batch=batch_name)
        original_data = json.loads(original_payload)
        if "schema_version" not in original_data:
            backup_path = metadata_path.with_name("metadata.v0.json")
            if not backup_path.exists():
                write_text_file_contents(backup_path, original_payload)

    model = metadata_from_application_dict(batch_name, metadata)
    write_text_file_contents(
        metadata_path,
        encode_batch_metadata(model),
    )
    return model

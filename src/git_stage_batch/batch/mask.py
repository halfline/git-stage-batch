"""Batch masking: manage global mask derived from per-batch claims."""

from __future__ import annotations

import json
from ..core.line_selection import parse_line_selection


def recompute_global_batch_mask() -> None:
    """Recompute global batch mask from union of all batch claims.

    The global batch mask is derived state computed from the union of all
    batch metadata. It uses stable batch-source coordinates (not ephemeral
    display IDs) keyed by file path.

    Mask format:
    {
      "path/to/file.py": {
        "batch_source_commit": "abc123...",
        "claimed_lines": ["1-3", "8"],  # Batch source line numbers
        "deletion_positions": ["1", "5"]  # Positions where deletions exist (for masking deletion lines)
      }
    }
    """
    from ..utils.file_io import read_text_file_contents, write_text_file_contents
    from ..utils.paths import (
        get_batch_claimed_hunks_file_path,
        get_batched_hunks_file_path,
        get_processed_batch_ids_file_path,
    )
    from .query import list_batch_names, read_batch_metadata

    all_hunk_hashes = set()
    file_mask: dict[str, dict] = {}

    # Union all per-batch claims
    for batch_name in list_batch_names():
        # Read claimed hunks (still used for hunk-level blocking)
        hunks_path = get_batch_claimed_hunks_file_path(batch_name)
        if hunks_path.exists():
            content = read_text_file_contents(hunks_path)
            if content:
                all_hunk_hashes.update(content.splitlines())

        # Union file-level batch ownership from metadata
        metadata = read_batch_metadata(batch_name)
        for file_path, file_data in metadata.get("files", {}).items():
            batch_source_commit = file_data.get("batch_source_commit")
            claimed_lines = file_data.get("claimed_lines", [])
            deletions = file_data.get("deletions", [])

            if file_path not in file_mask:
                file_mask[file_path] = {
                    "batch_source_commit": batch_source_commit,
                    "claimed_lines": [],
                    "deletion_positions": []
                }

            # Merge claimed lines
            existing_claimed = set(parse_line_selection(",".join(file_mask[file_path]["claimed_lines"]))) if file_mask[file_path]["claimed_lines"] else set()
            new_claimed = set(parse_line_selection(",".join(claimed_lines))) if claimed_lines else set()
            combined_claimed = existing_claimed | new_claimed

            if combined_claimed:
                from ..core.line_selection import format_line_ids
                file_mask[file_path]["claimed_lines"] = [format_line_ids(sorted(combined_claimed))]

            # Merge deletion positions (for masking deletion lines)
            existing_positions = set(parse_line_selection(",".join(file_mask[file_path]["deletion_positions"]))) if file_mask[file_path]["deletion_positions"] else set()
            new_positions = set()
            for deletion in deletions:
                after_line = deletion.get("after_source_line")
                if after_line is not None:  # None means start-of-file, which is position-less
                    new_positions.add(after_line)
            combined_positions = existing_positions | new_positions

            if combined_positions:
                from ..core.line_selection import format_line_ids
                file_mask[file_path]["deletion_positions"] = [format_line_ids(sorted(combined_positions))]

    # Write hunk-level mask (still needed for hunk blocking)
    batched_hunks_path = get_batched_hunks_file_path()
    write_text_file_contents(
        batched_hunks_path,
        "\n".join(sorted(all_hunk_hashes)) + "\n" if all_hunk_hashes else ""
    )

    # Write file-aware batch source mask (JSON format)
    # This is the canonical mask format - file-keyed with batch source coordinates
    processed_batch_ids_path = get_processed_batch_ids_file_path()
    write_text_file_contents(
        processed_batch_ids_path,
        json.dumps(file_mask, indent=2, ensure_ascii=False)
    )

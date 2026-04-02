"""Batch masking: manage global mask derived from per-batch claims."""

from __future__ import annotations


def recompute_global_batch_mask() -> None:
    """Recompute global batch mask from union of all batch claims.

    The global batch mask (batched hunks and processed batch IDs) is derived
    state computed from the union of all per-batch claim files. This ensures
    hunks remain masked as long as they exist in at least one batch.
    """
    from ..core.line_selection import read_line_ids_file, write_line_ids_file
    from ..utils.file_io import read_text_file_contents, write_text_file_contents
    from ..utils.paths import (
        get_batch_claimed_hunks_file_path,
        get_batch_claimed_line_ids_file_path,
        get_batched_hunks_file_path,
        get_processed_batch_ids_file_path,
    )
    from .query import list_batch_names

    all_hunk_hashes = set()
    all_line_ids = set()

    # Union all per-batch claims
    for batch_name in list_batch_names():
        # Read claimed hunks
        hunks_path = get_batch_claimed_hunks_file_path(batch_name)
        if hunks_path.exists():
            content = read_text_file_contents(hunks_path)
            if content:
                all_hunk_hashes.update(content.splitlines())

        # Read claimed line IDs
        line_ids_path = get_batch_claimed_line_ids_file_path(batch_name)
        if line_ids_path.exists():
            all_line_ids.update(read_line_ids_file(line_ids_path))

    # Write global masks (derived state)
    batched_hunks_path = get_batched_hunks_file_path()
    write_text_file_contents(
        batched_hunks_path,
        "\n".join(sorted(all_hunk_hashes)) + "\n" if all_hunk_hashes else ""
    )

    processed_batch_ids_path = get_processed_batch_ids_file_path()
    write_line_ids_file(processed_batch_ids_path, all_line_ids)

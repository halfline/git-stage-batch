"""Batch ownership data models and transformation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BatchOwnership:
    """Represents batch ownership in batch source space.

    A batch owns content relative to its batch source commit:
    - claimed_lines: Line ranges that exist in batch source (context/additions)
    - deletions: Content that doesn't exist in batch source (deletions from baseline)
    """
    claimed_lines: list[str]  # Range strings like ["1-5", "10-15", "18"]
    deletions: list[dict]      # [{after_source_line: int|None, blob: str}, ...]

    def is_empty(self) -> bool:
        """Check if this ownership is empty (no claimed lines or deletions)."""
        return not self.claimed_lines and not self.deletions

    def to_metadata_dict(self) -> dict:
        """Convert to metadata dictionary format for storage."""
        return {
            "claimed_lines": self.claimed_lines,
            "deletions": self.deletions
        }

    @classmethod
    def from_metadata_dict(cls, data: dict) -> BatchOwnership:
        """Create from metadata dictionary."""
        return cls(
            claimed_lines=data.get("claimed_lines", []),
            deletions=data.get("deletions", [])
        )

    def resolve(self, as_bytes: bool = False) -> ResolvedBatchOwnership:
        """Resolve into shared representation with loaded blob content.

        Both merge_batch and _build_realized_content consume this.

        Args:
            as_bytes: If True, return deletions as bytes (for _build_realized_content).
                     If False, return as strings with normalized line endings (for merge_batch).
        """
        from ..core.line_selection import parse_line_selection
        from ..utils.git import read_git_blob

        # Parse claimed line ranges into set
        claimed_line_set = set(parse_line_selection(",".join(self.claimed_lines))) if self.claimed_lines else set()

        # Load deletion blobs and group by position
        if as_bytes:
            deletions_by_position: dict[int | None, list] = {}
            for deletion in self.deletions:
                after_line = deletion.get("after_source_line")
                blob_sha = deletion["blob"]
                deletion_content = b"".join(read_git_blob(blob_sha))
                deletion_lines = deletion_content.splitlines(keepends=True)

                if after_line not in deletions_by_position:
                    deletions_by_position[after_line] = []
                deletions_by_position[after_line].extend(deletion_lines)
        else:
            # For merge.py: decode and normalize line endings
            deletions_by_position: dict[int | None, list] = {}
            for deletion in self.deletions:
                after_line = deletion.get("after_source_line")
                blob_sha = deletion["blob"]
                deletion_content = b"".join(read_git_blob(blob_sha))
                # Decode with error replacement
                deletion_text = deletion_content.decode('utf-8', errors='replace')
                # Normalize line endings for merge.py
                deletion_text = deletion_text.replace('\r\n', '\n').replace('\r', '\n')
                deletion_lines = deletion_text.splitlines(keepends=True)

                if after_line not in deletions_by_position:
                    deletions_by_position[after_line] = []
                deletions_by_position[after_line].extend(deletion_lines)

        return ResolvedBatchOwnership(claimed_line_set, deletions_by_position)


@dataclass
class ResolvedBatchOwnership:
    """Resolved ownership representation shared by merge and materialization.

    Positional vocabulary (explicit semantics):
    - claimed_line N: line N exists in batch source (identity-based)
    - deletion after_line N: positional attachment after batch source line N
    - after_line None: start of file (before batch source line 1)
    """
    claimed_line_set: set[int]  # Batch source line numbers (1-indexed)
    deletions_by_position: dict[int | None, list]  # Position -> loaded deletion content


def merge_batch_ownership(existing: BatchOwnership, new: BatchOwnership) -> BatchOwnership:
    """Merge two BatchOwnership objects.

    Args:
        existing: Existing batch ownership
        new: New ownership to merge in

    Returns:
        Merged BatchOwnership
    """
    from ..core.line_selection import format_line_ids, parse_line_selection

    # Merge claimed lines (combine and normalize ranges)
    existing_claimed = set(parse_line_selection(",".join(existing.claimed_lines))) if existing.claimed_lines else set()
    new_claimed = set(parse_line_selection(",".join(new.claimed_lines))) if new.claimed_lines else set()
    combined_claimed = existing_claimed | new_claimed

    # Normalize to range strings
    claimed_lines = []
    if combined_claimed:
        claimed_lines = [format_line_ids(sorted(combined_claimed))]

    # Merge deletions (combine both lists, keeping all unique deletions)
    # Group by position to avoid duplicates
    deletions_by_pos: dict[int | None, list[str]] = {}

    for deletion in existing.deletions:
        after_line = deletion.get("after_source_line")
        if after_line not in deletions_by_pos:
            deletions_by_pos[after_line] = []
        deletions_by_pos[after_line].append(deletion["blob"])

    for deletion in new.deletions:
        after_line = deletion.get("after_source_line")
        if after_line not in deletions_by_pos:
            deletions_by_pos[after_line] = []
        # Only add if not already present
        blob = deletion["blob"]
        if blob not in deletions_by_pos[after_line]:
            deletions_by_pos[after_line].append(blob)

    # Reconstruct deletions list
    deletions = []
    for after_line in sorted(deletions_by_pos.keys(), key=lambda x: -1 if x is None else x):
        for blob in deletions_by_pos[after_line]:
            deletions.append({
                "after_source_line": after_line,
                "blob": blob
            })

    return BatchOwnership(claimed_lines=claimed_lines, deletions=deletions)


def translate_lines_to_batch_ownership(selected_lines: list) -> BatchOwnership:
    """Translate display lines to batch source ownership.

    Args:
        selected_lines: List of LineEntry objects to translate

    Returns:
        BatchOwnership with claimed_lines and deletions
    """
    from ..core.line_selection import format_line_ids
    from ..utils.git import create_git_blob

    # Translate to batch source-space ownership
    # Diff shows index→working tree, batch source = working tree
    # Context/addition lines exist in batch source → claimed_lines
    # Deletion lines don't exist in batch source → deletions

    claimed_source_lines: list[int] = []
    deletion_lines_by_position: dict[int | None, list[bytes]] = {}

    for line in selected_lines:
        if line.kind in (' ', '+'):
            # Context or addition: exists in batch source (working tree)
            if line.source_line is not None:
                claimed_source_lines.append(line.source_line)
        elif line.kind == '-':
            # Deletion: doesn't exist in batch source, store as deletion claim
            # Position: after the batch source line that precedes this deletion
            after_line = line.source_line  # None for start-of-file, N for after line N
            if after_line not in deletion_lines_by_position:
                deletion_lines_by_position[after_line] = []
            # text_bytes has line content with \r preserved but \n stripped (diff format)
            # Add back \n for proper round-tripping
            deletion_lines_by_position[after_line].append(line.text_bytes + b'\n')

    # Normalize claimed lines into range strings
    claimed_lines = []
    if claimed_source_lines:
        claimed_lines = [format_line_ids(claimed_source_lines)]

    # Build deletions list (group consecutive deletions by position)
    deletions = []
    for after_line in sorted(deletion_lines_by_position.keys(), key=lambda x: -1 if x is None else x):
        deletion_content = b"".join(deletion_lines_by_position[after_line])
        blob_sha = create_git_blob([deletion_content])
        deletions.append({
            "after_source_line": after_line,
            "blob": blob_sha
        })

    return BatchOwnership(claimed_lines=claimed_lines, deletions=deletions)

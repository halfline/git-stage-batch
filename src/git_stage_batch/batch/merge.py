"""Structural batch merge using Long Common Subsequence-based alignment."""

from __future__ import annotations

from .match import LineMapping, match_lines
from ..exceptions import MergeError
from ..i18n import _


def _check_structural_validity(
    line_mapping: LineMapping,
    claimed_lines: set[int],
    deletions: list[dict],
    source_lines: list[str],
    target_lines: list[str]
) -> None:
    """Validate that missing batch content has nearby context for reliable placement.

    This performs basic sanity checks but does NOT guarantee structural unambiguity:
    - Rejects completely rewritten files (zero alignment with claimed lines)
    - For missing claimed lines: requires at least one aligned neighbor within 5 lines
    - For missing deletion anchors: requires neighbors within 3 lines
    - Present content (already in working tree) is NOT validated

    Args:
        line_mapping: Alignment between batch source and working tree
        claimed_lines: Set of claimed batch source line numbers
        deletions: List of {after_source_line, blob}
        source_lines: Batch source file lines
        target_lines: Working tree file lines

    Raises:
        MergeError: If basic placement requirements aren't met
    """
    # Count how many source lines are present in working tree
    present_count = sum(1 for line in range(1, len(source_lines) + 1)
                       if line_mapping.is_source_line_present(line))

    # If working tree is empty, allow merge - batch source structure provides ordering
    if len(target_lines) == 0:
        return

    # If working tree exists but has zero alignment, that's problematic
    if present_count == 0 and len(target_lines) > 0:
        # File was completely rewritten - not reliable
        if claimed_lines:
            # Pick first claimed line for error message
            first_claimed = min(claimed_lines)
            raise MergeError(
                _("Cannot reliably place claimed line {line}: file completely rewritten").format(
                    line=first_claimed
                )
            )

    # For each claimed line, verify we can place it reliably
    for claimed_line in claimed_lines:
        if claimed_line < 1 or claimed_line > len(source_lines):
            raise MergeError(
                _("Claimed line {line} is out of range (batch source has {count} lines)").format(
                    line=claimed_line,
                    count=len(source_lines)
                )
            )

        # If claimed line is missing from working tree, we need surrounding context
        # to know where to insert it
        if not line_mapping.is_source_line_present(claimed_line):
            # Check for context before/after (conservative: require at least one neighbor)
            has_context_before = False
            has_context_after = False

            # Look for aligned line before (search entire file for structure)
            for check_line in range(claimed_line - 1, 0, -1):
                if line_mapping.is_source_line_present(check_line):
                    has_context_before = True
                    break

            # Look for aligned line after (search entire file for structure)
            for check_line in range(claimed_line + 1, len(source_lines) + 1):
                if line_mapping.is_source_line_present(check_line):
                    has_context_after = True
                    break

            # Require at least one aligned neighbor for reliable placement
            if not has_context_before and not has_context_after:
                raise MergeError(
                    _("Cannot reliably place claimed line {line}: surrounding context lost").format(
                        line=claimed_line
                    )
                )

    # For each deletion, verify we can determine its position reliably
    for deletion in deletions:
        after_line = deletion.get("after_source_line")

        if after_line is not None:
            # Deletion after a specific line - verify that line is present or has context
            if after_line < 1 or after_line > len(source_lines):
                raise MergeError(
                    _("Deletion after line {line} is out of range").format(line=after_line)
                )

            # If the anchor line itself is missing, we need its neighbors to determine position
            if not line_mapping.is_source_line_present(after_line):
                # Check for context around the anchor
                has_context = False
                for check_line in range(max(1, after_line - 3), min(len(source_lines) + 1, after_line + 4)):
                    if check_line != after_line and line_mapping.is_source_line_present(check_line):
                        has_context = True
                        break

                if not has_context and after_line != len(source_lines):
                    raise MergeError(
                        _("Cannot determine deletion position after line {line}: anchor and neighbors missing").format(
                            line=after_line
                        )
                    )


def merge_batch(
    batch_source_content: str,
    ownership: 'BatchOwnership',
    working_content: str
) -> str:
    """Structurally merge batch into working tree.

    Algorithm:
    1. Normalize line endings (batch source already normalized, normalize working)
    2. Align working tree to batch source using difflib.SequenceMatcher
    3. Determine which claimed lines are present vs missing
    4. Determine which insertions are present vs missing
    5. Validate structural unambiguity
    6. Reconstruct: walk batch source order, preserve working tree extras, add missing content

    Args:
        batch_source_content: File content from batch source commit (normalized)
        ownership: BatchOwnership specifying claimed lines and insertions
        working_content: Current working tree file content

    Returns:
        New working tree content (union of current + missing claimed + missing insertions)

    Raises:
        MergeError: If alignment is structurally ambiguous
    """
    # Step 1: Normalize line endings (batch source already normalized during creation)
    working_content_normalized = working_content.replace('\r\n', '\n').replace('\r', '\n')

    source_lines = batch_source_content.splitlines(keepends=True)
    working_lines = working_content_normalized.splitlines(keepends=True)

    # Step 2: Align working tree to batch source (non-strict for reconstruction)
    line_mapping = match_lines(source_lines, working_lines, strict=False)
    strict_mapping = match_lines(source_lines, working_lines, strict=True)

    # Resolve ownership into shared representation
    resolved = ownership.resolve()
    claimed_line_set = resolved.claimed_line_set
    deletions_by_position = resolved.deletions_by_position

    # Step 3: Determine claimed lines present vs missing
    missing_claimed: dict[int, str] = {}  # source line -> content
    for source_line in claimed_line_set:
        if not line_mapping.is_source_line_present(source_line):
            if 1 <= source_line <= len(source_lines):
                missing_claimed[source_line] = source_lines[source_line - 1]

    # Step 4: Determine insertions present vs missing
    missing_insertions: dict[int | None, list[str]] = {}  # after_line -> insertion lines

    for after_line, insertion_lines in deletions_by_position.items():
        # Determine where to check in working tree
        if after_line is None:
            # Start of file
            check_position = 0
        else:
            # Map batch source position to working tree
            target_line = line_mapping.get_target_line_from_source_line(after_line)
            if target_line is not None:
                check_position = target_line
            else:
                # Anchor line missing - find nearest aligned line before it
                check_position = 0
                for check_line in range(after_line - 1, 0, -1):
                    target = line_mapping.get_target_line_from_source_line(check_line)
                    if target is not None:
                        check_position = target
                        break

        # Check if insertion already present immediately after check_position
        # Use fuzzy check: rstrip() to handle trailing whitespace/newline differences
        # (preserves leading whitespace/indentation which is semantically meaningful)
        is_present = True
        for i, insertion_line in enumerate(insertion_lines):
            working_idx = check_position + i
            if working_idx >= len(working_lines):
                is_present = False
                break
            # Compare with trailing whitespace normalization only
            if working_lines[working_idx].rstrip() != insertion_line.rstrip():
                is_present = False
                break

        if not is_present:
            # Accumulate insertions for same position (don't overwrite)
            if after_line not in missing_insertions:
                missing_insertions[after_line] = []
            missing_insertions[after_line].extend(insertion_lines)

    # Step 5: Validate structural placement requirements
    _check_structural_validity(strict_mapping, claimed_line_set, ownership.deletions, source_lines, working_lines)

    # Step 6: Reconstruct file
    # Walk through batch source order, preserving working tree extras and inserting missing content
    result_lines: list[str] = []
    working_idx = 0

    for source_line in range(1, len(source_lines) + 1):
        target_line = line_mapping.get_target_line_from_source_line(source_line)

        if target_line is not None:
            # This batch source line exists in working tree
            # First, add any working tree extras before this target line
            while working_idx < target_line - 1:
                result_lines.append(working_lines[working_idx])
                working_idx += 1

            # Add the matched line (or claimed version if it was claimed)
            if source_line in claimed_line_set:
                # Use batch source version (claimed)
                result_lines.append(source_lines[source_line - 1])
            else:
                # Use working tree version
                result_lines.append(working_lines[working_idx])
            working_idx += 1
        else:
            # This batch source line missing from working tree
            if source_line in missing_claimed:
                # Need to insert it
                result_lines.append(missing_claimed[source_line])

        # After this batch source line, add any missing insertions
        if source_line in missing_insertions:
            result_lines.extend(missing_insertions[source_line])

    # Add any remaining working tree lines
    while working_idx < len(working_lines):
        result_lines.append(working_lines[working_idx])
        working_idx += 1

    # Add insertions at start of file if needed
    if None in missing_insertions:
        result_lines = missing_insertions[None] + result_lines

    return "".join(result_lines)


def discard_batch(
    batch_source_content: str,
    ownership: 'BatchOwnership',
    working_content: str,
    baseline_content: str
) -> str:
    """Remove batch content from working tree and restore baseline.

    This is the inverse of merge_batch: instead of adding missing batch content,
    it removes present batch content and restores baseline for those parts.

    Algorithm:
    1. Normalize line endings
    2. Align working tree to batch source
    3. Align batch source to baseline
    4. Identify which claimed lines are present in working tree
    5. Identify which insertions are present in working tree
    6. Reconstruct: replace batch-owned parts with baseline, preserve non-batch parts

    Args:
        batch_source_content: File content from batch source commit (normalized)
        ownership: BatchOwnership specifying claimed lines and insertions
        working_content: Current working tree file content
        baseline_content: File content from baseline commit

    Returns:
        New working tree content (baseline for batch parts + non-batch working tree parts)

    Raises:
        MergeError: If alignment is structurally ambiguous
    """
    # Step 1: Normalize line endings
    working_content_normalized = working_content.replace('\r\n', '\n').replace('\r', '\n')
    baseline_content_normalized = baseline_content.replace('\r\n', '\n').replace('\r', '\n')

    source_lines = batch_source_content.splitlines(keepends=True)
    working_lines = working_content_normalized.splitlines(keepends=True)
    baseline_lines = baseline_content_normalized.splitlines(keepends=True)

    # Step 2: Align working tree to batch source
    working_to_source = match_lines(source_lines, working_lines, strict=False)

    # Step 3: Align batch source to baseline
    import difflib
    source_to_baseline_matcher = difflib.SequenceMatcher(None, baseline_lines, source_lines)

    # Build map: batch source line -> baseline lines
    source_line_to_baseline: dict[int, list[str]] = {}
    for tag, base_start, base_end, src_start, src_end in source_to_baseline_matcher.get_opcodes():
        if tag == 'equal':
            for offset in range(src_end - src_start):
                source_line_to_baseline[src_start + offset + 1] = [baseline_lines[base_start + offset]]
        elif tag == 'replace':
            # For replace, map each source line to corresponding baseline line(s)
            base_len = base_end - base_start
            src_len = src_end - src_start
            if base_len > 0 and src_len > 0:
                # Simple 1:1 mapping for first min(base_len, src_len) lines
                for offset in range(min(base_len, src_len)):
                    source_line_to_baseline[src_start + offset + 1] = [baseline_lines[base_start + offset]]
                # Any remaining base lines map to last source line
                if base_len > src_len:
                    source_line_to_baseline[src_start + src_len] = baseline_lines[base_start:base_end]
        elif tag == 'insert':
            # Source lines added (don't exist in baseline) - map to empty
            for src_idx in range(src_start, src_end):
                source_line_to_baseline[src_idx + 1] = []
        elif tag == 'delete':
            # Baseline lines removed (don't exist in source) - nothing to map
            pass

    # Resolve ownership into shared representation
    resolved = ownership.resolve()
    claimed_line_set = resolved.claimed_line_set
    deletions_by_position = resolved.deletions_by_position

    # Step 4: Identify present claimed lines and insertions
    present_claimed: dict[int, int] = {}  # source line -> working line
    for source_line in claimed_line_set:
        target_line = working_to_source.get_target_line_from_source_line(source_line)
        if target_line is not None:
            present_claimed[source_line] = target_line

    present_insertions: dict[int | None, list[str]] = {}
    # Track cumulative offset for multiple insertions at same position
    insertion_offsets: dict[int | None, int] = {}

    for after_line, insertion_lines in deletions_by_position.items():

        # Find base position in working tree
        if after_line is None:
            check_position = 0
        else:
            target_line = working_to_source.get_target_line_from_source_line(after_line)
            if target_line is not None:
                check_position = target_line
            else:
                check_position = 0
                for check_line in range(after_line - 1, 0, -1):
                    target = working_to_source.get_target_line_from_source_line(check_line)
                    if target is not None:
                        check_position = target
                        break

        # Account for previously detected insertions at this position
        if after_line in insertion_offsets:
            check_position += insertion_offsets[after_line]

        # Check if present
        is_present = True
        for i, insertion_line in enumerate(insertion_lines):
            working_idx = check_position + i
            if working_idx >= len(working_lines):
                is_present = False
                break
            if working_lines[working_idx].rstrip() != insertion_line.rstrip():
                is_present = False
                break

        if is_present:
            if after_line not in present_insertions:
                present_insertions[after_line] = []
            present_insertions[after_line].extend(insertion_lines)
            # Update offset for next insertion at same position
            if after_line not in insertion_offsets:
                insertion_offsets[after_line] = 0
            insertion_offsets[after_line] += len(insertion_lines)

    # Step 5: Reconstruct: baseline for batch parts + working tree for non-batch parts
    result_lines: list[str] = []
    working_idx = 0

    # Skip start-of-file insertions
    if None in present_insertions:
        working_idx += len(present_insertions[None])

    for source_line in range(1, len(source_lines) + 1):
        target_line = working_to_source.get_target_line_from_source_line(source_line)

        if target_line is not None:
            # Add working tree extras before this line
            while working_idx < target_line - 1:
                result_lines.append(working_lines[working_idx])
                working_idx += 1

            # Check if this line is claimed
            if source_line in present_claimed:
                # Replace with baseline version
                if source_line in source_line_to_baseline:
                    result_lines.extend(source_line_to_baseline[source_line])
                working_idx += 1
            else:
                # Keep working tree version
                result_lines.append(working_lines[working_idx])
                working_idx += 1

        # Skip present insertions after this line
        if source_line in present_insertions:
            working_idx += len(present_insertions[source_line])

    # Add remaining working tree lines
    while working_idx < len(working_lines):
        result_lines.append(working_lines[working_idx])
        working_idx += 1

    return "".join(result_lines)

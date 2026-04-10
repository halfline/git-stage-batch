"""Line matching and alignment between batch source and working tree."""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass
class LineMapping:
    """Alignment between batch source lines and working tree lines."""

    # Maps batch source line number (1-based) -> working tree line number (1-based) or None
    source_to_target: dict[int, int | None]

    # Maps working tree line number (1-based) -> batch source line number (1-based) or None
    target_to_source: dict[int, int | None]

    def is_source_line_present(self, source_line: int) -> bool:
        """Check if a batch source line is present in working tree."""
        return source_line in self.source_to_target and self.source_to_target[source_line] is not None

    def get_target_line_from_source_line(self, source_line: int) -> int | None:
        """Map batch source line to working tree line (None if not present)."""
        return self.source_to_target.get(source_line)

    def get_source_line_from_target_line(self, target_line: int) -> int | None:
        """Map working tree line to batch source line (None if working tree extra)."""
        return self.target_to_source.get(target_line)


def match_lines(source_lines: list[str], target_lines: list[str], *, strict: bool = False) -> LineMapping:
    """Compute alignment between batch source and working tree using difflib.SequenceMatcher.

    When strict=False, uses recursive alignment within 'replace' blocks:
    - First-level SequenceMatcher finds top-level equal/delete/insert/replace blocks
    - For each 'replace' block, a second SequenceMatcher aligns lines within that block
    - Within the nested matcher, 'equal' blocks create 1:1 mappings (semantic matches)
    - Nested 'replace' blocks map to None (recursion stops at one level to avoid infinite descent)

    This allows semantically similar lines within a replacement to be aligned, enabling
    structural merge to preserve working tree changes that don't conflict with batch changes.

    Args:
        source_lines: Batch source file lines
        target_lines: Working tree file lines
        strict: If True, treat 'replace' opcodes as delete+insert (source lines map to None)
                If False, recursively align within replace blocks (one level deep)

    Returns:
        LineMapping with bidirectional source ↔ target mappings
    """
    matcher = difflib.SequenceMatcher(None, source_lines, target_lines)
    opcodes = matcher.get_opcodes()

    source_to_target: dict[int, int | None] = {}
    target_to_source: dict[int, int | None] = {}

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'equal':
            # Lines match - create 1:1 mapping (1-based line numbers)
            for offset in range(i2 - i1):
                source_line = i1 + offset + 1
                target_line = j1 + offset + 1
                source_to_target[source_line] = target_line
                target_to_source[target_line] = source_line

        elif tag == 'delete':
            # Batch source lines missing from working tree
            for source_idx in range(i1, i2):
                source_line = source_idx + 1
                source_to_target[source_line] = None

        elif tag == 'insert':
            # Working tree extras not in batch source
            for target_idx in range(j1, j2):
                target_line = target_idx + 1
                target_to_source[target_line] = None

        elif tag == 'replace':
            # Changed lines
            if strict:
                # Treat as delete + insert: source lines map to None
                for source_idx in range(i1, i2):
                    source_line = source_idx + 1
                    source_to_target[source_line] = None
                for target_idx in range(j1, j2):
                    target_line = target_idx + 1
                    target_to_source[target_line] = None
            else:
                # Recursive alignment: use secondary SequenceMatcher within replace block
                # This finds semantic matches between changed regions (one level deep)
                block_source = source_lines[i1:i2]
                block_target = target_lines[j1:j2]
                sub_matcher = difflib.SequenceMatcher(None, block_source, block_target)

                for sub_tag, sub_i1, sub_i2, sub_j1, sub_j2 in sub_matcher.get_opcodes():
                    if sub_tag == 'equal':
                        # Lines match within block - create 1:1 alignment
                        for offset in range(sub_i2 - sub_i1):
                            source_line = i1 + sub_i1 + offset + 1
                            target_line = j1 + sub_j1 + offset + 1
                            source_to_target[source_line] = target_line
                            target_to_source[target_line] = source_line
                    elif sub_tag == 'delete':
                        # Source lines missing from target within block
                        for sub_idx in range(sub_i1, sub_i2):
                            source_line = i1 + sub_idx + 1
                            source_to_target[source_line] = None
                    elif sub_tag == 'insert':
                        # Target extras within block
                        for sub_idx in range(sub_j1, sub_j2):
                            target_line = j1 + sub_idx + 1
                            target_to_source[target_line] = None
                    elif sub_tag == 'replace':
                        # Nested replace within replace: stop recursion here
                        # Treat as unmapped (prevents infinite descent on pathological diffs)
                        for sub_idx in range(sub_i1, sub_i2):
                            source_line = i1 + sub_idx + 1
                            source_to_target[source_line] = None
                        for sub_idx in range(sub_j1, sub_j2):
                            target_line = j1 + sub_idx + 1
                            target_to_source[target_line] = None

    return LineMapping(source_to_target, target_to_source)

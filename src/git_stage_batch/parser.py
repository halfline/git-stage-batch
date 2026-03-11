"""Parse unified diff format into structured models."""

from __future__ import annotations

from .models import SingleHunkPatch


def parse_unified_diff_into_single_hunk_patches(diff_text: str) -> list[SingleHunkPatch]:
    """Parse a unified diff into separate single-hunk patches.

    Takes the output of `git diff` and splits it so that each file+hunk
    combination becomes its own SingleHunkPatch object. This allows
    processing hunks independently.

    Args:
        diff_text: Output from `git diff` in unified format

    Returns:
        List of SingleHunkPatch objects, one per hunk
    """
    patches: list[SingleHunkPatch] = []
    lines = diff_text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]

        # Look for start of a file diff
        if line.startswith("diff --git "):
            # Extract file paths from the diff --git line
            # Format: diff --git a/path b/path
            # Need to handle paths with spaces, so can't just split()
            rest = line[len("diff --git "):]

            # Find a/ and b/ markers
            a_start = rest.find("a/")
            b_start = rest.find(" b/")

            if a_start == -1 or b_start == -1:
                i += 1
                continue

            old_path = rest[a_start + 2:b_start]
            new_path = rest[b_start + 3:]  # Skip " b/"

            i += 1
            patch_lines: list[str] = []

            # Collect lines until we hit the --- line (start of unified diff)
            while i < len(lines) and not lines[i].startswith("---"):
                i += 1

            if i >= len(lines):
                break

            # Found the --- line, start of this file's diff
            old_file_line = lines[i]
            i += 1

            if i >= len(lines) or not lines[i].startswith("+++"):
                continue

            new_file_line = lines[i]
            i += 1

            # Process all hunks for this file
            while i < len(lines) and lines[i].startswith("@@"):
                hunk_lines = [old_file_line, new_file_line, lines[i]]
                i += 1

                # Collect hunk body (lines starting with space, +, or -)
                while i < len(lines):
                    if lines[i].startswith("diff --git "):
                        # Next file starting
                        break
                    if lines[i].startswith("@@"):
                        # Next hunk for same file
                        break
                    if lines[i].startswith("---") and i + 1 < len(lines) and lines[i + 1].startswith("+++"):
                        # This might be the start of a new file diff
                        break

                    # Include lines that are part of the hunk
                    if lines[i].startswith((" ", "+", "-", "\\")):
                        hunk_lines.append(lines[i])
                        i += 1
                    else:
                        # Unknown line, stop collecting this hunk
                        break

                # Create a SingleHunkPatch for this hunk
                patches.append(SingleHunkPatch(
                    old_path=old_path,
                    new_path=new_path,
                    lines=hunk_lines
                ))
        else:
            i += 1

    return patches

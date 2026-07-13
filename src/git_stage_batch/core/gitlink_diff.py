"""Gitlink-specific helpers for unified diff parsing."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable


INDEX_LINE_PATTERN = re.compile(br"^index ([0-9a-f]+)\.\.([0-9a-f]+)(?: ([0-7]+))?$")
SUBPROJECT_COMMIT_PATTERN = re.compile(br"^([+-])Subproject commit ([0-9a-f]+)(?:-[^\s]+)?$")


def metadata_indicates_gitlink(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a mode-160000 entry."""
    for line in metadata_lines:
        if line in (
            b"new file mode 160000",
            b"deleted file mode 160000",
            b"old mode 160000",
            b"new mode 160000",
        ):
            return True
        match = INDEX_LINE_PATTERN.match(line)
        if match is not None and match.group(3) == b"160000":
            return True
    return False


def gitlink_oids_from_index(
    metadata_lines: list[bytes],
) -> tuple[str | None, str | None]:
    """Extract old and new object ids from a gitlink index line."""
    for line in metadata_lines:
        match = INDEX_LINE_PATTERN.match(line)
        if match is not None:
            return (
                match.group(1).decode("ascii"),
                match.group(2).decode("ascii"),
            )
    return None, None


def non_null_git_oid(oid: str | None) -> str | None:
    """Return an object id unless it represents the null side of a diff."""
    if oid is None:
        return None
    if oid and all(character == "0" for character in oid):
        return None
    return oid


def gitlink_old_path(path: str, old_oid: str | None) -> str:
    """Return /dev/null for the old side of an added gitlink."""
    return "/dev/null" if non_null_git_oid(old_oid) is None else path


def gitlink_new_path(path: str, new_oid: str | None) -> str:
    """Return /dev/null for the new side of a deleted gitlink."""
    return "/dev/null" if non_null_git_oid(new_oid) is None else path


def gitlink_change_type(
    metadata_lines: list[bytes],
    old_oid: str | None,
    new_oid: str | None,
) -> str:
    """Derive added/modified/deleted from gitlink diff metadata."""
    if any(line == b"new file mode 160000" for line in metadata_lines):
        return "added"
    if any(line == b"deleted file mode 160000" for line in metadata_lines):
        return "deleted"
    if non_null_git_oid(old_oid) is None:
        return "added"
    if non_null_git_oid(new_oid) is None:
        return "deleted"
    return "modified"


def consume_gitlink_hunks(
    next_line: Callable[[], bytes | None],
    peek_line: Callable[[], bytes | None],
) -> tuple[str | None, str | None]:
    """Consume all gitlink hunks for the current file and return full oids."""
    old_oid = None
    new_oid = None

    while True:
        next_l = peek_line()
        if next_l is None:
            break
        next_l_stripped = next_l.rstrip(b"\n")
        if next_l_stripped.startswith(b"diff --git "):
            break
        if next_l_stripped.startswith(b"---"):
            break

        next_line()
        match = SUBPROJECT_COMMIT_PATTERN.match(next_l_stripped)
        if match is not None:
            oid = match.group(2).decode("ascii")
            if match.group(1) == b"-":
                old_oid = oid
            else:
                new_oid = oid

    return old_oid, new_oid


def gitlink_oids_from_subproject_commit_patch(
    patch_lines: Iterable[bytes],
) -> tuple[str | None, str | None] | None:
    """Return gitlink oids when a patch only changes Subproject commit lines."""
    old_oid = None
    new_oid = None
    changed_line_count = 0

    for line in patch_lines:
        stripped = line.rstrip(b"\n")
        if stripped.startswith((b"--- ", b"+++ ", b"@@ ", b"\\ ")):
            continue
        if not stripped or stripped[0:1] not in (b"+", b"-"):
            continue

        changed_line_count += 1
        match = SUBPROJECT_COMMIT_PATTERN.match(stripped)
        if match is None:
            return None

        oid = match.group(2).decode("ascii")
        if match.group(1) == b"-":
            old_oid = oid
        else:
            new_oid = oid

    if changed_line_count == 0:
        return None
    return old_oid, new_oid

"""Atomic file-change records built from batch metadata."""

from __future__ import annotations

from collections.abc import Mapping

from ..core.models import BinaryFileChange, GitlinkChange


def binary_change_from_batch_file_metadata(
    file_path: str,
    file_meta: Mapping[str, object],
) -> BinaryFileChange | None:
    """Return an atomic binary batch change, if the entry is binary."""
    if file_meta.get("file_type") != "binary":
        return None
    change_type = file_meta.get("change_type")
    if change_type not in ("added", "modified", "deleted"):
        return None
    return BinaryFileChange(
        old_path="/dev/null" if change_type == "added" else file_path,
        new_path="/dev/null" if change_type == "deleted" else file_path,
        change_type=change_type,
    )


def gitlink_change_from_batch_file_metadata(
    file_path: str,
    file_meta: Mapping[str, object],
) -> GitlinkChange | None:
    """Return an atomic submodule pointer batch change, if the entry is one."""
    if file_meta.get("file_type") != "gitlink":
        return None
    change_type = file_meta.get("change_type")
    if change_type not in ("added", "modified", "deleted"):
        return None
    return GitlinkChange(
        old_path="/dev/null" if change_type == "added" else file_path,
        new_path="/dev/null" if change_type == "deleted" else file_path,
        old_oid=file_meta.get("old_oid"),
        new_oid=file_meta.get("new_oid"),
        change_type=change_type,
    )

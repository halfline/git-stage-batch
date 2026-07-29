"""Typed schemas for cached selected atomic changes."""

from __future__ import annotations

from typing import Literal, TypedDict


ChangeType = Literal["added", "modified", "deleted"]
class SelectedBinaryData(TypedDict, total=False):
    """Cached binary-file selection metadata."""

    old_path: str
    new_path: str
    change_type: ChangeType
    content_fingerprint: str | None
    batch_name: str | None
    batch_binary_fingerprint: str | None
    comparison_base: str | None

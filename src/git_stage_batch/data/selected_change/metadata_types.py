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


class SelectedGitlinkData(TypedDict, total=False):
    """Cached submodule-pointer selection metadata."""

    old_path: str
    new_path: str
    old_oid: str | None
    new_oid: str | None
    change_type: ChangeType
    batch_name: str | None
    batch_gitlink_fingerprint: str | None
    comparison_base: str | None


class SelectedModeData(TypedDict, total=False):
    """Cached executable-mode selection metadata."""

    file_path: str
    old_mode: str
    new_mode: str
    batch_name: str | None


class SelectedRenameData(TypedDict, total=False):
    """Cached rename selection metadata."""

    old_path: str


    new_path: str

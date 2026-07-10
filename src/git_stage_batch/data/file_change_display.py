"""Live file change rendering without selected-state mutation."""

from __future__ import annotations

import subprocess
from typing import Optional

from ..core.diff_parser import acquire_unified_diff
from ..core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from .file_tracking import auto_add_untracked_files
from .live_diff import stream_live_git_diff


def render_binary_file_change(file_path: str) -> Optional[BinaryFileChange]:
    """Render a binary file change for file-scoped display without caching state."""
    auto_add_untracked_files([file_path])
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                base="HEAD",
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
                paths=[file_path],
            )
        ) as patches:
            for item in patches:
                if isinstance(item, BinaryFileChange):
                    return item
    except subprocess.CalledProcessError:
        return None
    return None


def render_gitlink_change(file_path: str) -> Optional[GitlinkChange]:
    """Render a gitlink change for file-scoped display without caching state."""
    auto_add_untracked_files([file_path])
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                base="HEAD",
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
                paths=[file_path],
            )
        ) as patches:
            for item in patches:
                if isinstance(item, GitlinkChange):
                    return item
    except subprocess.CalledProcessError:
        return None
    return None


def render_rename_change(file_path: str) -> Optional[RenameChange]:
    """Render a rename change involving file_path without caching state."""
    auto_add_untracked_files([file_path])
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for item in patches:
                if (
                    isinstance(item, RenameChange)
                    and file_path in (item.old_path, item.new_path)
                ):
                    return item
    except subprocess.CalledProcessError:
        return None
    return None


def render_text_deletion_change(
    file_path: str,
) -> Optional[TextFileDeletionChange]:
    """Render a whole-text-file deletion without caching state."""
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
                paths=[file_path],
            )
        ) as patches:
            for item in patches:
                if isinstance(item, TextFileDeletionChange):
                    return item
    except subprocess.CalledProcessError:
        return None
    return None

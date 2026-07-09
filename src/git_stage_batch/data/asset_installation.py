"""Packaged asset installation primitives."""

from __future__ import annotations

from pathlib import Path

from .asset_catalog import Traversable
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import write_file_bytes


def _should_skip_asset_entry(entry: Traversable) -> bool:
    """Return whether a packaged entry is generated Python cache data."""
    return entry.name == "__pycache__" or entry.name.endswith((".pyc", ".pyo"))


def copy_asset_tree(source: Traversable, destination: Path) -> None:
    """Copy a packaged asset tree into a filesystem path."""
    if _should_skip_asset_entry(source):
        return
    if source.is_dir():
        for child in source.iterdir():
            copy_asset_tree(child, destination / child.name)
        return
    if source.is_file():
        write_file_bytes(destination, source.read_bytes())


def validate_asset_destination_path(
    source: Traversable,
    destination: Path,
    repo_root: Path,
) -> None:
    """Reject installs that would collide with non-directory path components."""
    for parent in destination.parents:
        if parent == repo_root.parent:
            break
        if not parent.exists():
            continue
        if not parent.is_dir():
            raise CommandError(
                _("Cannot install bundled assets because '{path}' is not a directory.").format(
                    path=str(parent.relative_to(repo_root)),
                )
            )

    if not destination.exists():
        return

    if source.is_dir() and not destination.is_dir():
        raise CommandError(
            _("Cannot install bundled assets because '{path}' is not a directory.").format(
                path=str(destination.relative_to(repo_root)),
            )
        )
    if source.is_file() and destination.is_dir():
        raise CommandError(
            _("Cannot install bundled assets because '{path}' is a directory.").format(
                path=str(destination.relative_to(repo_root)),
            )
        )

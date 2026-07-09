"""Packaged asset installation primitives."""

from __future__ import annotations

from pathlib import Path

from .asset_catalog import Traversable
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

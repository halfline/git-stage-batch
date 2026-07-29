"""Session source-commit cache for batch operations."""

from __future__ import annotations

import json
from typing import cast

from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import get_session_batch_sources_file_path


def get_batch_source_for_file(file_path: str) -> str | None:
    """Retrieve an existing batch source commit for a file from the session cache.

    Args:
        file_path: Repository-relative path to the file

    Returns:
        Batch source commit SHA if found, None otherwise
    """
    batch_sources = load_session_batch_sources()
    return batch_sources.get(file_path)


def load_session_batch_sources() -> dict[str, str]:
    """Load the session source-commit cache.

    Returns:
        Dictionary mapping file paths to batch source commit SHAs
    """
    batch_sources_path = get_session_batch_sources_file_path()
    if not batch_sources_path.exists():
        return {}

    try:
        content = read_text_file_contents(batch_sources_path)
        value: object = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return {}
    if type(value) is not dict:
        return {}
    entries = cast(dict[object, object], value)
    if not all(
        isinstance(file_path, str) and isinstance(commit, str)
        for file_path, commit in entries.items()
    ):
        return {}
    return {
        cast(str, file_path): cast(str, commit)
        for file_path, commit in entries.items()
    }


def save_session_batch_sources(batch_sources: dict[str, str]) -> None:
    """Save the session source-commit cache.

    Args:
        batch_sources: Dictionary mapping file paths to batch source commit SHAs
    """
    batch_sources_path = get_session_batch_sources_file_path()
    content = json.dumps(batch_sources, indent=2)
    write_text_file_contents(batch_sources_path, content)

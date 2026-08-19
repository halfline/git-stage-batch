"""Session source-commit cache for batch operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import get_session_batch_sources_file_path


@dataclass(frozen=True, slots=True)
class SessionSourceHint:
    """Path-scoped display hint with no durable ownership authority."""

    file_path: str
    commit: str


def get_session_source_hint(file_path: str) -> SessionSourceHint | None:
    """Return a path-scoped hint that persistence must independently verify."""
    commit = load_session_batch_sources().get(file_path)
    if commit is None:
        return None
    return SessionSourceHint(file_path, commit)


def get_batch_source_for_file(file_path: str) -> str | None:
    """Retrieve an existing batch source commit for a file from the session cache.

    Args:
        file_path: Repository-relative path to the file

    Returns:
        Batch source commit SHA if found, None otherwise
    """
    hint = get_session_source_hint(file_path)
    return None if hint is None else hint.commit


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

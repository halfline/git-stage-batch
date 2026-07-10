"""Session-persistent consumed-selection ownership for hidden masking."""

from __future__ import annotations

import json
from typing import Any

from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import get_session_consumed_selections_file_path


def load_consumed_selections_metadata() -> dict[str, Any]:
    """Load hidden consumed-selection metadata."""
    path = get_session_consumed_selections_file_path()
    if not path.exists():
        return {"files": {}}

    try:
        data = json.loads(read_text_file_contents(path))
    except json.JSONDecodeError:
        return {"files": {}}

    files = data.get("files", {})
    if not isinstance(files, dict):
        return {"files": {}}
    return {"files": files}


def read_consumed_file_metadata(file_path: str) -> dict[str, Any] | None:
    """Return hidden consumed-selection metadata for one file."""
    metadata = load_consumed_selections_metadata()
    file_metadata = metadata.get("files", {}).get(file_path)
    return file_metadata if isinstance(file_metadata, dict) else None


def write_consumed_file_metadata(
    file_path: str,
    file_metadata: dict[str, Any],
) -> None:
    """Persist hidden consumed-selection metadata for one file."""
    metadata = load_consumed_selections_metadata()
    metadata.setdefault("files", {})[file_path] = file_metadata
    write_text_file_contents(
        get_session_consumed_selections_file_path(),
        json.dumps(metadata, ensure_ascii=False, indent=2),
    )

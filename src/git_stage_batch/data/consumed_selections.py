"""Session-persistent consumed-selection ownership for hidden masking."""

from __future__ import annotations

import json
from typing import cast

from ..batch.state.metadata_types import BatchFileMetadataDict, BatchMetadataDict
from ..exceptions import CommandError
from ..git_paths import display_path
from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import get_session_consumed_selections_file_path


def load_consumed_selections_metadata() -> BatchMetadataDict:
    """Load hidden consumed-selection metadata."""
    path = get_session_consumed_selections_file_path()
    if not path.exists():
        return {"files": {}}

    try:
        data = json.loads(read_text_file_contents(path))
    except json.JSONDecodeError as exc:
        raise CommandError(
            _(
                "Consumed-selection state is corrupt: {path}. "
                "Abort the session to recover safely."
            ).format(path=display_path(str(path)))
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("files", {}), dict):
        raise CommandError(
            _(
                "Consumed-selection state has an invalid structure: {path}. "
                "Abort the session to recover safely."
            ).format(path=display_path(str(path)))
        )
    files = data.get("files", {})
    for file_path, file_metadata in files.items():
        if not isinstance(file_metadata, dict):
            raise CommandError(
                _(
                    "Consumed-selection state has an invalid entry for {file}: "
                    "{path}. Abort the session to recover safely."
                ).format(
                    file=display_path(file_path),
                    path=display_path(str(path)),
                )
            )
    return {"files": cast(dict[str, BatchFileMetadataDict], files)}


def read_consumed_file_metadata(
    file_path: str,
) -> BatchFileMetadataDict | None:
    """Return hidden consumed-selection metadata for one file."""
    metadata = load_consumed_selections_metadata()
    file_metadata = metadata.get("files", {}).get(file_path)
    return file_metadata


def write_consumed_file_metadata(
    file_path: str,
    file_metadata: BatchFileMetadataDict,
) -> None:
    """Persist hidden consumed-selection metadata for one file."""
    metadata = load_consumed_selections_metadata()
    metadata.setdefault("files", {})[file_path] = file_metadata
    write_text_file_contents(
        get_session_consumed_selections_file_path(),
        json.dumps(metadata, ensure_ascii=False, indent=2),
    )

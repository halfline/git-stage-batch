"""Baseline boundary reference metadata for batch ownership."""

from __future__ import annotations

from dataclasses import dataclass

from ...utils.git_object_io import create_git_blob
from .metadata_blobs import read_metadata_blob as _metadata_blob_content


@dataclass
class BaselineReference:
    """Baseline-side coordinate and optional boundary identity.

    The line numbers are old-file coordinates from the diff that produced the
    selection. Byte payloads, when present, let a later merge prove the target
    still has the same local boundary before applying a baseline coordinate.
    """

    after_line: int | None
    after_content: bytes | None = None
    has_after_line: bool = True
    before_line: int | None = None
    before_content: bytes | None = None
    has_before_line: bool = False

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        data = {}
        if self.has_after_line:
            data["after_line"] = self.after_line
        if self.after_content is not None:
            data["after_blob"] = create_git_blob([self.after_content])
        if self.has_before_line:
            data["before_line"] = self.before_line
        if self.before_content is not None:
            data["before_blob"] = create_git_blob([self.before_content])
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
    ) -> BaselineReference:
        """Deserialize from metadata dictionary."""
        if not isinstance(data, dict):
            raise ValueError("Baseline reference metadata must be a dictionary")

        after_blob = data.get("after_blob")
        before_blob = data.get("before_blob")
        after_content = _metadata_blob_content(after_blob, blob_contents)
        before_content = _metadata_blob_content(before_blob, blob_contents)
        return cls(
            after_line=data.get("after_line"),
            after_content=after_content,
            has_after_line="after_line" in data,
            before_line=data.get("before_line"),
            before_content=before_content,
            has_before_line="before_line" in data,
        )

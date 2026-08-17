"""Absence claim value records for batch ownership."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...core.buffer import LineBuffer, buffer_byte_chunks
from ...utils.git_object_io import create_git_blob
from .metadata_types import AbsenceClaimMetadata
from .references import BaselineReference


@dataclass
class AbsenceClaim:
    """A suppression constraint: specific old-side content that must not appear.

    Deletions are constraints, not content to replay. Each absence claim represents
    a contiguous run of lines that must be absent from the materialized result.

    Attributes:
        anchor_line: Batch source line after which this absence claim is anchored
                     (None for start-of-file)
        content_lines: Exact old-side line content that must be suppressed,
                       with line endings preserved
        baseline_reference: Optional old-file coordinate where this absence
                            claim was selected. This lets same-source batch
                            round trips apply replacement units back to an
                            unchanged baseline/index without guessing from
                            post-change source anchors.
        source_alternative: The old side came from an explicit live replacement
                            payload rather than from the batch baseline.
    """

    anchor_line: int | None
    content_lines: Sequence[bytes]
    baseline_reference: BaselineReference | None = None
    source_alternative: bool = False

    def to_dict(self) -> AbsenceClaimMetadata:
        """Serialize to metadata dictionary."""
        blob_sha = create_git_blob(buffer_byte_chunks(self.content_lines))
        data: AbsenceClaimMetadata = {
            "after_source_line": self.anchor_line,
            "blob": blob_sha,
        }
        if self.baseline_reference is not None:
            data["baseline_reference"] = self.baseline_reference.to_dict()
        if self.source_alternative:
            data["source_alternative"] = True
        return data

    def to_attribution_dict(self) -> AbsenceClaimMetadata:
        """Serialize the anchor and content identity used by attribution."""
        return {
            "after_source_line": self.anchor_line,
            "blob": create_git_blob(buffer_byte_chunks(self.content_lines)),
        }

    @classmethod
    def from_dict(
        cls,
        data: AbsenceClaimMetadata,
        blob_contents: dict[str, bytes] | None = None,
        blob_buffers: dict[str, LineBuffer] | None = None,
    ) -> AbsenceClaim:
        """Deserialize from metadata dictionary."""
        anchor_line = data.get("after_source_line")
        blob_sha = data["blob"]
        if blob_buffers is None:
            raise ValueError("deletion blobs must be loaded before deserialization")
        content_lines = blob_buffers[blob_sha]
        baseline_metadata = data.get("baseline_reference")
        baseline_reference = (
            BaselineReference.from_dict(baseline_metadata, blob_contents)
            if baseline_metadata is not None else None
        )
        return cls(
            anchor_line=anchor_line,
            content_lines=content_lines,
            baseline_reference=baseline_reference,
            source_alternative=data.get("source_alternative") is True,
        )

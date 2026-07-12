"""Shared value types for operation candidate previews."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..core.buffer import LineBuffer
from .merge.candidates import MergeResolution


CandidateOperation = Literal["apply", "include"]
CandidateTarget = Literal["index", "worktree"]
MAX_OPERATION_CANDIDATES = 50


class CandidateEnumerationLimitError(ValueError):
    """Raised when a candidate set is too large to preview safely."""


@dataclass(frozen=True)
class CandidatePreviewCount:
    """Candidate preview count result for one file."""

    count: int = 0
    too_many: bool = False
    error: str | None = None


@dataclass
class TargetCandidatePreview:
    """Materialized candidate result for one target."""

    target: CandidateTarget
    file_path: str
    before_buffer: LineBuffer
    after_buffer: LineBuffer
    file_mode: str | None
    change_type: str
    destination_exists: bool
    resolution: MergeResolution | None
    resolution_ordinal: int
    resolution_count: int
    summary: str
    explanation: str
    ambiguity_target_line_range: tuple[int, int] | None

    def close(self) -> None:
        self.before_buffer.close()
        self.after_buffer.close()


@dataclass
class OperationCandidatePreview:
    """Materialized preview for one complete operation candidate."""

    operation: CandidateOperation
    batch_name: str
    file_path: str
    ordinal: int
    count: int
    candidate_id: str
    targets: tuple[TargetCandidatePreview, ...]
    batch_fingerprint: str
    target_fingerprints: dict[str, str]
    target_result_fingerprints: dict[str, str]
    scope_fingerprint: str

    def require_target(self, target: CandidateTarget) -> TargetCandidatePreview:
        """Return a target preview or raise when the candidate shape is invalid."""
        for candidate_target in self.targets:
            if candidate_target.target == target:
                return candidate_target
        raise KeyError(target)

    def close(self) -> None:
        for target in self.targets:
            target.close()

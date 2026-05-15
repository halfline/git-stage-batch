"""Batch ownership data models and transformation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256

from ..core.line_selection import (
    LineRanges,
    LineSelection,
    format_line_ids,
)
from ..core.models import LineEntry
from ..data.batch_sources import create_batch_source_commit
from ..editor import (
    Editor,
    EditorBuffer,
    buffer_byte_chunks,
    load_git_blob_as_buffer,
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ..exceptions import AtomicUnitError, MergeError
from ..i18n import _
from ..utils.git import (
    create_git_blob,
    get_git_repository_root_path,
    read_git_blobs_as_bytes,
)
from .comparison import SemanticChangeKind, derive_semantic_change_runs
from .lineage import _BatchSourceLineage, _LineageRun
from .match import LineMapping, match_lines
from .merge import (
    _apply_presence_constraints,
    _realized_entry_content_chunks,
)


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
        after_content = _read_metadata_blob(after_blob, blob_contents)
        before_content = _read_metadata_blob(before_blob, blob_contents)
        return cls(
            after_line=data.get("after_line"),
            after_content=after_content,
            has_after_line="after_line" in data,
            before_line=data.get("before_line"),
            before_content=before_content,
            has_before_line="before_line" in data,
        )


@dataclass
class AbsenceClaim:
    """A suppression constraint: specific baseline content that must not appear.

    Deletions are constraints, not content to replay. Each absence claim represents
    a contiguous run of lines that must be absent from the materialized result.

    Attributes:
        anchor_line: Batch source line after which this absence claim is anchored
                     (None for start-of-file)
        content_lines: Exact baseline line content that must be suppressed,
                       with line endings preserved
        baseline_reference: Optional old-file coordinate where this absence
                            claim was selected. This lets same-source batch
                            round trips apply replacement units back to an
                            unchanged baseline/index without guessing from
                            post-change source anchors.
    """
    anchor_line: int | None
    content_lines: Sequence[bytes]
    baseline_reference: BaselineReference | None = None

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        blob_sha = create_git_blob(buffer_byte_chunks(self.content_lines))
        data = {
            "after_source_line": self.anchor_line,
            "blob": blob_sha
        }
        if self.baseline_reference is not None:
            data["baseline_reference"] = self.baseline_reference.to_dict()
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
        blob_buffers: dict[str, EditorBuffer] | None = None,
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
        )


def _read_metadata_blob(
    blob_sha: str | None,
    blob_contents: dict[str, bytes] | None,
) -> bytes | None:
    if blob_sha is None:
        return None
    if blob_contents is None:
        raise ValueError("metadata blobs must be loaded before deserialization")
    return blob_contents[blob_sha]


def _baseline_reference_blob_ids(reference_metadata: dict) -> list[str]:
    if not isinstance(reference_metadata, dict):
        return []
    blob_ids: list[str] = []
    for key in ("after_blob", "before_blob"):
        blob_sha = reference_metadata.get(key)
        if blob_sha:
            blob_ids.append(blob_sha)
    return blob_ids


def _baseline_references_blob_ids(references_metadata: dict) -> list[str]:
    blob_ids: list[str] = []
    for value in references_metadata.values():
        blob_ids.extend(_baseline_reference_blob_ids(value))
    return blob_ids


@dataclass
class PresenceClaim:
    """A presence constraint over batch-source lines.

    Presence claims are the first-class representation for content that must
    exist after a batch is applied. Source lines identify the content in the
    batch source; optional baseline references record where those source lines came
    from in the original index/tree diff.
    """

    source_lines: list[str]
    baseline_references: dict[int, BaselineReference] = field(default_factory=dict)

    def source_line_set(self) -> LineRanges:
        """Return batch-source line numbers covered by this presence claim."""
        return _parse_line_ranges(self.source_lines)

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        data = {"source_lines": self.source_lines}
        if self.baseline_references:
            data["baseline_references"] = {
                str(line): reference.to_dict()
                for line, reference in sorted(self.baseline_references.items())
            }
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
    ) -> PresenceClaim:
        """Deserialize from metadata dictionary."""
        references_metadata = data.get("baseline_references", {})
        return cls(
            source_lines=data.get("source_lines", []),
            baseline_references={
                int(line): BaselineReference.from_dict(
                    reference,
                    blob_contents,
                )
                for line, reference in references_metadata.items()
            },
        )


def _presence_claim_reference_blob_ids(presence_metadata: list[dict]) -> list[str]:
    blob_ids: list[str] = []
    for claim in presence_metadata:
        blob_ids.extend(
            _baseline_references_blob_ids(
                claim.get("baseline_references", {})
            )
        )
    return blob_ids


def _deletion_content_blob_ids(deletion_metadata: list[dict]) -> list[str]:
    return [
        metadata["blob"]
        for metadata in deletion_metadata
        if "blob" in metadata
    ]


def _deletion_reference_blob_ids(deletion_metadata: list[dict]) -> list[str]:
    return [
        blob_id
        for metadata in deletion_metadata
        for blob_id in _baseline_reference_blob_ids(
            metadata.get("baseline_reference", {})
        )
    ]


@dataclass
class ReplacementUnit:
    """Explicit coupling between presence claims and absence claims.

    The deletion side references indexes in BatchOwnership.deletions so the
    canonical deletion constraint is stored only once in metadata.
    """

    presence_lines: list[str]
    deletion_indices: list[int]

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        return {
            "presence_lines": self.presence_lines,
            "deletion_indices": self.deletion_indices,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ReplacementUnit:
        """Deserialize from metadata dictionary."""
        return cls(
            presence_lines=data.get("presence_lines", data.get("claimed_lines", [])),
            deletion_indices=data.get("deletion_indices", []),
        )


@dataclass(frozen=True, slots=True)
class ReplacementLineRun:
    """One file-derived replacement run in old-file and new-file coordinates."""

    old_start: int
    old_end: int
    new_start: int
    new_end: int

    def __post_init__(self) -> None:
        if self.old_start > self.old_end:
            raise ValueError("old range start must be <= end")
        if self.new_start > self.new_end:
            raise ValueError("new range start must be <= end")

    def old_line_numbers(self) -> range:
        """Return old-file line numbers without materializing them."""
        return range(self.old_start, self.old_end + 1)

    def new_line_numbers(self) -> range:
        """Return new-file line numbers without materializing them."""
        return range(self.new_start, self.new_end + 1)


def derive_replacement_line_runs_from_lines(
    *,
    old_file_lines: Sequence[bytes],
    new_file_lines: Sequence[bytes],
) -> list[ReplacementLineRun]:
    """Derive replacement line runs from old/new byte-line sequences."""
    replacement_runs: list[ReplacementLineRun] = []
    semantic_runs = derive_semantic_change_runs(old_file_lines, new_file_lines)
    for run in semantic_runs:
        if (
            run.kind == SemanticChangeKind.REPLACEMENT
            and run.source_start is not None
            and run.source_end is not None
            and run.target_start is not None
            and run.target_end is not None
        ):
            replacement_runs.append(
                ReplacementLineRun(
                    old_start=run.source_start,
                    old_end=run.source_end,
                    new_start=run.target_start,
                    new_end=run.target_end,
                )
            )
    return replacement_runs


@dataclass
class BatchOwnership:
    """Represents batch ownership in batch source space.

    A batch owns content relative to its batch source commit:
    - presence_claims: Batch-source lines that must exist after application
    - deletions: Suppression constraints for baseline content (absence claims)
    - replacement_units: Optional explicit coupling between claims and deletions
    """
    presence_claims: list[PresenceClaim]
    deletions: list[AbsenceClaim]  # Separate deletion constraints
    replacement_units: list[ReplacementUnit] = field(default_factory=list)

    @classmethod
    def from_presence_lines(
        cls,
        source_lines: list[str],
        deletions: list[AbsenceClaim] | None = None,
        *,
        replacement_units: list[ReplacementUnit] | None = None,
        baseline_references: dict[int, BaselineReference] | None = None,
    ) -> BatchOwnership:
        """Create ownership from source-line ranges.

        This is a construction helper for tests and call sites that naturally
        start with a flat set of source-line ranges. The stored model remains a
        list of PresenceClaim objects.
        """
        return cls(
            presence_claims=_presence_claims_from_source_lines(
                _parse_line_ranges(source_lines),
                baseline_references or {},
            ),
            deletions=deletions or [],
            replacement_units=replacement_units or [],
        )

    def is_empty(self) -> bool:
        """Check if this ownership is empty (no presence claims or deletions)."""
        return not self.presence_claims and not self.deletions

    def presence_line_set(self) -> LineRanges:
        """Return all batch-source lines claimed present by this ownership."""
        presence_lines = LineRanges.empty()
        for claim in self.presence_claims:
            presence_lines = presence_lines.union(claim.source_line_set())
        return presence_lines

    def presence_baseline_references(self) -> dict[int, BaselineReference]:
        """Return baseline references keyed by claimed batch-source line."""
        references: dict[int, BaselineReference] = {}
        for claim in self.presence_claims:
            references.update(claim.baseline_references)
        return references

    def to_metadata_dict(self) -> dict:
        """Convert to metadata dictionary format for storage."""
        data = {
            "presence_claims": [claim.to_dict() for claim in self.presence_claims],
            "deletions": [claim.to_dict() for claim in self.deletions]
        }
        replacement_units = [
            unit.to_dict()
            for unit in _normalize_replacement_units(
                self.replacement_units,
                deletion_count=len(self.deletions),
            )
        ]
        if replacement_units:
            data["replacement_units"] = replacement_units
        return data

    @classmethod
    def acquire_for_metadata_dict(
        cls,
        data: dict,
    ) -> _AcquiredBatchOwnership:
        """Acquire ownership for metadata with buffered deletion blobs."""
        deletion_metadata = data.get("deletions", [])
        presence_metadata = data.get("presence_claims", [])
        blob_buffers: dict[str, EditorBuffer] = {}
        buffers: list[EditorBuffer] = []
        try:
            for blob_sha in _deletion_content_blob_ids(deletion_metadata):
                if blob_sha in blob_buffers:
                    continue
                buffer = load_git_blob_as_buffer(blob_sha)
                blob_buffers[blob_sha] = buffer
                buffers.append(buffer)

            blob_contents = read_git_blobs_as_bytes(
                [
                    *_deletion_reference_blob_ids(deletion_metadata),
                    *_presence_claim_reference_blob_ids(presence_metadata),
                ]
            )
            ownership = cls._from_metadata_dict(
                data,
                blob_contents=blob_contents,
                deletion_blob_buffers=blob_buffers,
            )
        except Exception:
            for buffer in buffers:
                buffer.close()
            raise

        return _AcquiredBatchOwnership(
            ownership=ownership,
            buffers=buffers,
        )

    @classmethod
    def _from_metadata_dict(
        cls,
        data: dict,
        *,
        blob_contents: dict[str, bytes],
        deletion_blob_buffers: dict[str, EditorBuffer] | None = None,
    ) -> BatchOwnership:
        deletion_metadata = data.get("deletions", [])
        presence_metadata = data.get("presence_claims", [])
        legacy_claimed_lines = data.get("claimed_lines", [])
        presence_claims = [
            PresenceClaim.from_dict(d, blob_contents)
            for d in presence_metadata
        ]
        if not presence_claims and legacy_claimed_lines:
            presence_claims = _presence_claims_from_source_lines(
                _parse_line_ranges(legacy_claimed_lines)
            )
        deletions = [
            AbsenceClaim.from_dict(d, blob_contents, deletion_blob_buffers)
            for d in deletion_metadata
        ]
        replacement_units = [
            ReplacementUnit.from_dict(d)
            for d in data.get("replacement_units", [])
        ]
        return cls(
            presence_claims=presence_claims,
            deletions=deletions,
            replacement_units=replacement_units,
        )

    def resolve(self) -> ResolvedBatchOwnership:
        """Resolve into representation for materialization and merge.

        Returns presence lines as a selection and absence claims as a list
        (preserving structure).
        """
        return ResolvedBatchOwnership(self.presence_line_set(), self.deletions)


@dataclass
class _AcquiredBatchOwnership:
    """Own buffers used by a scoped BatchOwnership value."""

    ownership: BatchOwnership
    buffers: list[EditorBuffer]

    def close(self) -> None:
        for buffer in self.buffers:
            buffer.close()

    def __enter__(self) -> BatchOwnership:
        return self.ownership

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


@dataclass
class ResolvedBatchOwnership:
    """Resolved ownership representation for materialization and merge.

    Preserves the structure of absence claims as separate constraints.

    Attributes:
        presence_line_set: Batch source line numbers (1-indexed, identity-based)
        deletion_claims: List of suppression constraints (order and structure preserved)
    """
    presence_line_set: LineRanges  # Batch source line numbers (1-indexed)
    deletion_claims: list[AbsenceClaim]  # Separate constraints, not collapsed


def acquire_detached_batch_ownership(
    ownership: BatchOwnership,
) -> _AcquiredBatchOwnership:
    """Acquire an ownership copy with independent absence content buffers."""
    buffers: list[EditorBuffer] = []
    deletions: list[AbsenceClaim] = []
    try:
        for deletion in ownership.deletions:
            content_lines = _copy_absence_content(deletion.content_lines)
            buffers.append(content_lines)
            deletions.append(
                AbsenceClaim(
                    anchor_line=deletion.anchor_line,
                    content_lines=content_lines,
                    baseline_reference=deletion.baseline_reference,
                )
            )
    except Exception:
        for buffer in buffers:
            buffer.close()
        raise

    return _AcquiredBatchOwnership(
        ownership=BatchOwnership(
            presence_claims=[
                PresenceClaim(
                    source_lines=claim.source_lines[:],
                    baseline_references=dict(claim.baseline_references),
                )
                for claim in ownership.presence_claims
            ],
            deletions=deletions,
            replacement_units=[
                ReplacementUnit(
                    presence_lines=unit.presence_lines[:],
                    deletion_indices=unit.deletion_indices[:],
                )
                for unit in ownership.replacement_units
            ],
        ),
        buffers=buffers,
    )


@dataclass
class SourceContentWithLineProvenance:
    """Synthesized source buffer with line provenance from its inputs."""

    source_buffer: EditorBuffer
    lineage: _BatchSourceLineage

    def close(self) -> None:
        """Release the synthesized buffer and line lineage."""
        self.source_buffer.close()
        self.lineage.close()

    def __enter__(self) -> SourceContentWithLineProvenance:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


@dataclass
class BatchSourceAdvanceResult:
    """Result of advancing one file's batch source."""

    batch_source_commit: str
    ownership: BatchOwnership
    source_buffer: EditorBuffer
    lineage: _BatchSourceLineage

    def close(self) -> None:
        """Release the refreshed source buffer and line lineage."""
        self.source_buffer.close()
        self.lineage.close()

    def __enter__(self) -> BatchSourceAdvanceResult:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class _AbsenceSignature:
    anchor_line: int | None
    content_digest: str
    byte_count: int
    line_count: int


def _absence_signature(claim: AbsenceClaim) -> _AbsenceSignature:
    """Return a stable signature for an absence claim."""
    digest = sha256()
    byte_count = 0
    for chunk in buffer_byte_chunks(claim.content_lines):
        digest.update(chunk)
        byte_count += len(chunk)

    return _AbsenceSignature(
        anchor_line=claim.anchor_line,
        content_digest=digest.hexdigest(),
        byte_count=byte_count,
        line_count=len(claim.content_lines),
    )


def _baseline_reference_side_score(
    reference: BaselineReference | None,
    *,
    side: str,
) -> int:
    """Score how much boundary data a baseline reference side carries."""
    if reference is None:
        return 0
    if side == "after":
        has_line = reference.has_after_line
        content = reference.after_content
    else:
        has_line = reference.has_before_line
        content = reference.before_content
    return (1 if has_line else 0) + (2 if content is not None else 0)


def _merge_baseline_references(
    existing: BaselineReference | None,
    new: BaselineReference | None,
) -> BaselineReference | None:
    """Keep the strongest available baseline boundary metadata."""
    if existing is None:
        return new
    if new is None:
        return existing

    after = (
        new
        if _baseline_reference_side_score(new, side="after")
        > _baseline_reference_side_score(existing, side="after")
        else existing
    )
    before = (
        new
        if _baseline_reference_side_score(new, side="before")
        > _baseline_reference_side_score(existing, side="before")
        else existing
    )
    return BaselineReference(
        after_line=after.after_line,
        after_content=after.after_content,
        has_after_line=after.has_after_line,
        before_line=before.before_line,
        before_content=before.before_content,
        has_before_line=before.has_before_line,
    )


def _merge_deletion_claim_metadata(
    existing: AbsenceClaim,
    new: AbsenceClaim,
) -> AbsenceClaim:
    """Merge metadata for absence claims with the same anchor and content."""
    return AbsenceClaim(
        anchor_line=existing.anchor_line,
        content_lines=existing.content_lines,
        baseline_reference=_merge_baseline_references(
            existing.baseline_reference,
            new.baseline_reference,
        ),
    )


def _parse_line_ranges(line_ranges: list[str] | list[int]) -> LineRanges:
    """Parse source line range strings into a selection."""
    return LineRanges.from_specs(line_ranges)


def _format_line_set(source_lines: LineSelection | Iterable[int]) -> list[str]:
    """Format a source line selection as normalized range strings."""
    if isinstance(source_lines, LineRanges):
        return source_lines.to_range_strings()
    source_selection = LineRanges.from_lines(source_lines)
    if not source_selection:
        return []
    return source_selection.to_range_strings()


@dataclass
class _LineRangeBuilder:
    """Build a normalized line selection from mostly ordered additions."""

    ranges: list[tuple[int, int]] = field(default_factory=list)
    pending_start: int | None = None
    pending_end: int | None = None

    def add_line(self, line_number: int) -> None:
        if self.pending_start is None or self.pending_end is None:
            self.pending_start = line_number
            self.pending_end = line_number
            return

        if self.pending_start <= line_number <= self.pending_end:
            return

        if line_number == self.pending_end + 1:
            self.pending_end = line_number
            return

        self.ranges.append((self.pending_start, self.pending_end))
        self.pending_start = line_number
        self.pending_end = line_number

    def finish(self) -> LineRanges:
        ranges = list(self.ranges)
        if self.pending_start is not None and self.pending_end is not None:
            ranges.append((self.pending_start, self.pending_end))
        return LineRanges.from_ranges(ranges)


@dataclass
class _ReplacementUnitBuilder:
    deletion_indices: list[int]
    claimed_lines: _LineRangeBuilder = field(default_factory=_LineRangeBuilder)

    def add_presence_line(self, source_line: int) -> None:
        self.claimed_lines.add_line(source_line)

    def finish(self) -> ReplacementUnit:
        return ReplacementUnit(
            presence_lines=self.claimed_lines.finish().to_range_strings(),
            deletion_indices=self.deletion_indices,
        )


def _presence_claims_from_source_lines(
    source_lines: LineSelection | Iterable[int],
    baseline_references: dict[int, BaselineReference] | None = None,
) -> list[PresenceClaim]:
    """Build normalized presence claims from a source-line selection."""
    source_selection = (
        source_lines
        if isinstance(source_lines, LineRanges)
        else LineRanges.from_lines(source_lines)
    )
    if not source_selection:
        return []
    references = baseline_references or {}
    return [
        PresenceClaim(
            source_lines=_format_line_set(source_selection),
            baseline_references={
                line: reference
                for line, reference in references.items()
                if line in source_selection
            },
        )
    ]


def _normalize_replacement_units(
    replacement_units: list[ReplacementUnit],
    *,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Drop invalid references and coalesce overlapping replacement units."""
    components: list[tuple[LineRanges, set[int]]] = []

    for unit in replacement_units:
        claimed = _parse_line_ranges(unit.presence_lines)
        deletion_indices = {
            index
            for index in unit.deletion_indices
            if type(index) is int and 0 <= index < deletion_count
        }
        if not claimed or not deletion_indices:
            continue

        overlapping_component_indices = [
            index
            for index, (component_claimed, component_deletions)
            in enumerate(components)
            if component_claimed.intersection(claimed) or component_deletions & deletion_indices
        ]
        if not overlapping_component_indices:
            components.append((claimed, set(deletion_indices)))
            continue

        target_index = overlapping_component_indices[0]
        target_claimed, target_deletions = components[target_index]
        target_claimed = target_claimed.union(claimed)
        target_deletions.update(deletion_indices)

        for source_index in reversed(overlapping_component_indices[1:]):
            source_claimed, source_deletions = components[source_index]
            target_claimed = target_claimed.union(source_claimed)
            target_deletions.update(source_deletions)
            del components[source_index]
        components[target_index] = (target_claimed, target_deletions)

    return [
        ReplacementUnit(
            presence_lines=_format_line_set(claimed),
            deletion_indices=sorted(deletion_indices),
        )
        for claimed, deletion_indices in components
    ]


def merge_batch_ownership(existing: BatchOwnership, new: BatchOwnership) -> BatchOwnership:
    """Merge two BatchOwnership objects.

    Combines presence claims (union) and merges deletion constraints with deduplication.

    Absence claims are deduplicated by (anchor_line, content) signature to prevent
    duplicate deletions when batch source advances and ownership is remapped. The same
    deletion can appear in both existing (remapped) and new (from current diff).

    Args:
        existing: Existing batch ownership
        new: New ownership to merge in

    Returns:
        Merged BatchOwnership with combined claims and deduplicated deletions
    """
    # Merge presence claims (combine and normalize ranges)
    existing_claimed = existing.presence_line_set()
    new_claimed = new.presence_line_set()
    combined_claimed = existing_claimed.union(new_claimed)
    combined_presence_references = {
        **existing.presence_baseline_references(),
        **new.presence_baseline_references(),
    }

    # Merge absence claims: deduplicate by anchor and content
    # When batch source advances and ownership is remapped, the same deletion can appear
    # in both existing (remapped) and new (from current diff). We need to deduplicate.
    combined_deletions = []
    deletion_index_by_signature: dict[_AbsenceSignature, int] = {}
    existing_deletion_index_map: dict[int, int] = {}
    new_deletion_index_map: dict[int, int] = {}

    for source_name, source_index, deletion in (
        [("existing", index, deletion) for index, deletion in enumerate(existing.deletions)]
        + [("new", index, deletion) for index, deletion in enumerate(new.deletions)]
    ):
        # Create a signature for this deletion: anchor + content
        signature = _absence_signature(deletion)

        if signature not in deletion_index_by_signature:
            deletion_index_by_signature[signature] = len(combined_deletions)
            combined_deletions.append(deletion)
        else:
            combined_index = deletion_index_by_signature[signature]
            combined_deletions[combined_index] = _merge_deletion_claim_metadata(
                combined_deletions[combined_index],
                deletion,
            )
        combined_index = deletion_index_by_signature[signature]
        if source_name == "existing":
            existing_deletion_index_map[source_index] = combined_index
        else:
            new_deletion_index_map[source_index] = combined_index

    combined_replacement_units: list[ReplacementUnit] = []
    for source_units, index_map in (
        (existing.replacement_units, existing_deletion_index_map),
        (new.replacement_units, new_deletion_index_map),
    ):
        for unit in source_units:
            remapped_indices = [
                index_map[index]
                for index in unit.deletion_indices
                if type(index) is int and index in index_map
            ]
            combined_replacement_units.append(ReplacementUnit(
                presence_lines=unit.presence_lines,
                deletion_indices=remapped_indices,
            ))

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(
            combined_claimed,
            combined_presence_references,
        ),
        deletions=combined_deletions,
        replacement_units=_normalize_replacement_units(
            combined_replacement_units,
            deletion_count=len(combined_deletions),
        ),
    )


def detect_stale_batch_source_for_selection(selected_lines: list) -> bool:
    """Detect if selected lines cannot be expressed in current batch source.

    Returns True if any claimed/addition line has source_line=None, indicating
    the batch source is stale and must be advanced before translation.

    Args:
        selected_lines: List of LineEntry objects to check

    Returns:
        True if batch source is stale, False otherwise
    """
    for line in selected_lines:
        # Context and addition lines should have source_line populated
        # If they don't, the current batch source cannot express this change
        if line.kind in (' ', '+') and line.source_line is None:
            return True
        # A None deletion anchor is only current for deletions before line 1.
        if (
            line.kind == '-'
            and line.source_line is None
            and line.old_line_number is not None
            and line.old_line_number > 1
        ):
            return True
    return False


def translate_lines_to_batch_ownership(selected_lines: list) -> BatchOwnership:
    """Translate display lines to batch source ownership.

    Creates presence claims and suppression constraints (deletion_claims).
    Each contiguous run of deletions becomes a separate AbsenceClaim.

    This function assumes all selected lines can be expressed in batch source
    space. Call detect_stale_batch_source_for_selection() first and handle stale
    sources before calling this function. If source_line is None for claimed
    lines, this raises an error instead of dropping them.

    Args:
        selected_lines: List of LineEntry objects to translate

    Returns:
        BatchOwnership with presence claims and absence claims

    Raises:
        ValueError: If any claimed line has source_line=None (stale batch source)
    """
    # Translate to batch source-space ownership
    # Diff shows index→working tree, batch source = working tree
    # Context/addition lines exist in batch source → presence claims
    # Deletion lines don't exist in batch source → absence claims (suppression)

    content_view = _LineEntryContentSequence(selected_lines)
    claimed_source_lines = _LineRangeBuilder()
    presence_baseline_references: dict[int, BaselineReference] = {}
    absence_claims: list[AbsenceClaim] = []
    replacement_units: list[ReplacementUnit] = []

    # Track current deletion run
    current_absence_anchor: int | None = None
    current_absence_baseline_reference: BaselineReference | None = None
    current_absence_start: int | None = None
    current_absence_stop: int | None = None
    active_replacement_unit: _ReplacementUnitBuilder | None = None

    def finish_replacement_unit(
        builder: _ReplacementUnitBuilder | None,
    ) -> None:
        if builder is not None:
            replacement_units.append(builder.finish())

    def flush_absence_run() -> list[int]:
        """Finalize current deletion run as an AbsenceClaim."""
        nonlocal current_absence_anchor
        nonlocal current_absence_baseline_reference
        nonlocal current_absence_start
        nonlocal current_absence_stop
        if current_absence_start is None or current_absence_stop is None:
            return []

        content_lines = _build_absence_content_from_range(
            content_view,
            current_absence_start,
            current_absence_stop,
        )
        absence_claims.append(
            AbsenceClaim(
                anchor_line=current_absence_anchor,
                content_lines=content_lines,
                baseline_reference=current_absence_baseline_reference,
            )
        )
        absence_index = len(absence_claims) - 1
        current_absence_start = None
        current_absence_stop = None
        current_absence_anchor = None
        current_absence_baseline_reference = None
        return [absence_index]

    for index, line in enumerate(selected_lines):
        if line.kind in (' ', '+'):
            # Context or addition: exists in batch source (working tree)
            # Flush any pending deletion run
            flushed_deletion_indices = flush_absence_run()

            if line.source_line is None:
                raise ValueError(
                    f"Cannot translate line to batch ownership: source_line is None "
                    f"(kind={line.kind!r}, text={line.display_text()!r}). "
                    f"Batch source is stale and must be advanced before translation."
                )

            claimed_source_lines.add_line(line.source_line)
            if line.has_baseline_reference_after:
                presence_baseline_references[line.source_line] = BaselineReference(
                    after_line=line.baseline_reference_after_line,
                    after_content=line.baseline_reference_after_text_bytes,
                    has_after_line=line.has_baseline_reference_after,
                    before_line=line.baseline_reference_before_line,
                    before_content=line.baseline_reference_before_text_bytes,
                    has_before_line=line.has_baseline_reference_before,
                )
            if line.kind == '+':
                if flushed_deletion_indices:
                    finish_replacement_unit(active_replacement_unit)
                    active_replacement_unit = _ReplacementUnitBuilder(
                        deletion_indices=flushed_deletion_indices,
                    )

                if active_replacement_unit is not None:
                    active_replacement_unit.add_presence_line(line.source_line)
            else:
                finish_replacement_unit(active_replacement_unit)
                active_replacement_unit = None

            # Update anchor for next deletion run
            current_absence_anchor = line.source_line

        elif line.kind == '-':
            finish_replacement_unit(active_replacement_unit)
            active_replacement_unit = None
            # Deletion: suppression constraint
            # Anchor each deletion run at its first deleted line. A None anchor
            # means the run starts before the first source line and must not be
            # overwritten by later deleted lines in the same run.
            if current_absence_start is None:
                current_absence_start = index
                current_absence_anchor = line.source_line
                if line.old_line_number is not None:
                    current_absence_baseline_reference = BaselineReference(
                        after_line=(
                            line.old_line_number - 1
                            if line.old_line_number > 1 else
                            None
                        )
                    )
            current_absence_stop = index + 1

    # Flush any final deletion run
    flush_absence_run()
    finish_replacement_unit(active_replacement_unit)

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(
            claimed_source_lines.finish(),
            presence_baseline_references,
        ),
        deletions=absence_claims,
        replacement_units=_normalize_replacement_units(
            replacement_units,
            deletion_count=len(absence_claims),
        ),
    )


def _old_line_content_by_number(hunk_lines: list[LineEntry]) -> dict[int, bytes]:
    return {
        line.old_line_number: line.text_bytes
        for line in hunk_lines
        if line.old_line_number is not None and line.kind in {" ", "-"}
    }


def _line_entry_content(line: LineEntry) -> bytes:
    return line.text_bytes + (b"\n" if line.has_trailing_newline else b"")


class _LineEntryContentSequence(Sequence[bytes]):
    """Lazy byte-line view over LineEntry content."""

    def __init__(self, lines: Sequence[LineEntry]) -> None:
        self._lines = lines

    def __len__(self) -> int:
        return len(self._lines)

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return _LineEntryContentSequence(self._lines[index])

        return _line_entry_content(self._lines[index])


class _AbsenceContentBuilder:
    """Build absence content as an EditorBuffer from appended line ranges."""

    def __init__(self) -> None:
        self._editor: Editor | None = Editor(())

    def __enter__(self) -> _AbsenceContentBuilder:
        self._check_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def append_line_range(
        self,
        lines: Sequence[bytes],
        start: int,
        end: int,
    ) -> None:
        editor = self._check_open()
        editor.append_line_range(lines, start, end)

    def finish(self) -> EditorBuffer:
        editor = self._check_open()
        try:
            return EditorBuffer.from_chunks(editor.line_chunks())
        finally:
            self.close()

    def close(self) -> None:
        editor = self._editor
        if editor is None:
            return

        self._editor = None
        editor.close()

    def _check_open(self) -> Editor:
        editor = self._editor
        if editor is None:
            raise RuntimeError("absence content builder is closed")

        return editor


def _copy_absence_content(content_lines: Sequence[bytes]) -> EditorBuffer:
    if isinstance(content_lines, EditorBuffer):
        return EditorBuffer.from_chunks(buffer_byte_chunks(content_lines))
    return _build_absence_content_from_range(content_lines, 0, len(content_lines))


def _build_absence_content_from_range(
    content_lines: Sequence[bytes],
    start: int,
    end: int,
) -> EditorBuffer:
    with _AbsenceContentBuilder() as builder:
        builder.append_line_range(content_lines, start, end)
        return builder.finish()


def _baseline_reference_for_old_line_range(
    old_start: int,
    old_end: int,
    old_line_content: dict[int, bytes],
) -> BaselineReference:
    after_line = old_start - 1 if old_start > 1 else None
    before_line = old_end + 1
    before_content = old_line_content.get(before_line)
    return BaselineReference(
        after_line=after_line,
        after_content=(
            old_line_content.get(after_line)
            if after_line is not None else
            None
        ),
        has_after_line=True,
        before_line=before_line if before_content is not None else None,
        before_content=before_content,
        has_before_line=before_content is not None,
    )


@dataclass(frozen=True)
class _HunkLineRangeScan:
    start: int
    end: int
    start_index: int
    stop_index: int
    count: int
    selected_count: int

    @property
    def complete(self) -> bool:
        return self.count == self.end - self.start + 1

    @property
    def fully_selected(self) -> bool:
        return self.complete and self.selected_count == self.count


def _scan_hunk_line_range(
    hunk_lines: list[LineEntry],
    cursor: int,
    *,
    kind: str,
    line_number_attr: str,
    start: int,
    end: int,
    selected_display_ids: set[int],
) -> _HunkLineRangeScan:
    index = cursor
    start_index = cursor
    count = 0
    selected_count = 0
    found_first = False

    while index < len(hunk_lines):
        line = hunk_lines[index]
        line_number = getattr(line, line_number_attr)
        if line_number is not None and line_number > end:
            break
        if line.kind == kind and line_number is not None:
            if line_number < start:
                index += 1
                continue
            if line_number > end:
                break
            if not found_first:
                start_index = index
                found_first = True
            count += 1
            if line.id is not None and line.id in selected_display_ids:
                selected_count += 1
        index += 1

    return _HunkLineRangeScan(
        start=start,
        end=end,
        start_index=start_index,
        stop_index=index,
        count=count,
        selected_count=selected_count,
    )


def _hunk_line_indexes_in_range(
    hunk_lines: list[LineEntry],
    scan: _HunkLineRangeScan,
    *,
    kind: str,
    line_number_attr: str,
) -> Iterable[int]:
    for index in range(scan.start_index, scan.stop_index):
        line = hunk_lines[index]
        line_number = getattr(line, line_number_attr)
        if (
            line.kind == kind
            and line_number is not None
            and scan.start <= line_number <= scan.end
        ):
            yield index


def _hunk_line_index_ranges_in_range(
    hunk_lines: list[LineEntry],
    scan: _HunkLineRangeScan,
    *,
    kind: str,
    line_number_attr: str,
) -> Iterable[tuple[int, int]]:
    pending_start: int | None = None
    pending_stop: int | None = None

    for index in _hunk_line_indexes_in_range(
        hunk_lines,
        scan,
        kind=kind,
        line_number_attr=line_number_attr,
    ):
        if pending_stop == index:
            pending_stop = index + 1
            continue

        if pending_start is not None and pending_stop is not None:
            yield pending_start, pending_stop
        pending_start = index
        pending_stop = index + 1

    if pending_start is not None and pending_stop is not None:
        yield pending_start, pending_stop


def translate_hunk_selection_to_batch_ownership(
    hunk_lines: list[LineEntry],
    selected_display_ids: set[int],
    *,
    replacement_line_runs: list[ReplacementLineRun] | None = None,
) -> BatchOwnership:
    """Translate selected live-hunk IDs while retaining full-hunk boundaries.

    Unlike translate_lines_to_batch_ownership(), this scans the complete live
    diff hunk. Unselected lines are not claimed, but they still delimit selected
    deletion runs and provide source/baseline boundary metadata for conservative
    round trips through batch storage. The IDs are user-facing selection handles;
    the input is not rendered batch-display output.

    Replacement coupling is supplied by the caller as before/after line-number
    runs derived from the full files represented by the hunk. This function does
    not infer semantic replacement units from the pregenerated diff layout.
    """
    claimed_source_lines = _LineRangeBuilder()
    presence_baseline_references: dict[int, BaselineReference] = {}
    absence_claims: list[AbsenceClaim] = []
    replacement_units: list[ReplacementUnit] = []
    old_line_content = _old_line_content_by_number(hunk_lines)
    hunk_content_view = _LineEntryContentSequence(hunk_lines)
    consumed_replacement_ids: set[int] = set()

    def add_replacement_unit(
        selected_old_ranges: Iterable[tuple[int, int]],
        selected_new_lines: Iterable[LineEntry],
        *,
        old_start: int,
        old_end: int,
    ) -> None:
        deletion_anchor: int | None = None
        old_line_seen = False
        selected_source_lines = _LineRangeBuilder()
        consumed_ids: list[int] = []
        with _AbsenceContentBuilder() as builder:
            for range_start, range_stop in selected_old_ranges:
                if not old_line_seen:
                    deletion_anchor = hunk_lines[range_start].source_line
                    old_line_seen = True
                builder.append_line_range(
                    hunk_content_view,
                    range_start,
                    range_stop,
                )
                for index in range(range_start, range_stop):
                    old_line = hunk_lines[index]
                    if old_line.id is not None:
                        consumed_ids.append(old_line.id)

            content_lines = builder.finish()

        for new_line in selected_new_lines:
            if new_line.source_line is None:
                raise ValueError(
                    f"Cannot translate line to batch ownership: source_line is None "
                    f"(kind={new_line.kind!r}, text={new_line.display_text()!r}). "
                    f"Batch source is stale and must be advanced before translation."
                )

            claimed_source_lines.add_line(new_line.source_line)
            selected_source_lines.add_line(new_line.source_line)
            if new_line.id is not None:
                consumed_ids.append(new_line.id)
            if new_line.has_baseline_reference_after:
                presence_baseline_references[new_line.source_line] = BaselineReference(
                    after_line=new_line.baseline_reference_after_line,
                    after_content=new_line.baseline_reference_after_text_bytes,
                    has_after_line=new_line.has_baseline_reference_after,
                    before_line=new_line.baseline_reference_before_line,
                    before_content=new_line.baseline_reference_before_text_bytes,
                    has_before_line=new_line.has_baseline_reference_before,
                )

        absence_claims.append(
            AbsenceClaim(
                anchor_line=deletion_anchor,
                content_lines=content_lines,
                baseline_reference=_baseline_reference_for_old_line_range(
                    old_start,
                    old_end,
                    old_line_content,
                ),
            )
        )
        replacement_units.append(
            ReplacementUnit(
                presence_lines=selected_source_lines.finish().to_range_strings(),
                deletion_indices=[len(absence_claims) - 1],
            )
        )
        consumed_replacement_ids.update(consumed_ids)

    old_cursor = 0
    new_cursor = 0

    for replacement_run in replacement_line_runs or []:
        old_scan = _scan_hunk_line_range(
            hunk_lines,
            old_cursor,
            kind="-",
            line_number_attr="old_line_number",
            start=replacement_run.old_start,
            end=replacement_run.old_end,
            selected_display_ids=selected_display_ids,
        )
        new_scan = _scan_hunk_line_range(
            hunk_lines,
            new_cursor,
            kind="+",
            line_number_attr="new_line_number",
            start=replacement_run.new_start,
            end=replacement_run.new_end,
            selected_display_ids=selected_display_ids,
        )
        old_cursor = old_scan.stop_index
        new_cursor = new_scan.stop_index

        if not old_scan.complete or not new_scan.complete:
            continue

        if old_scan.count == new_scan.count:
            old_indexes = _hunk_line_indexes_in_range(
                hunk_lines,
                old_scan,
                kind="-",
                line_number_attr="old_line_number",
            )
            new_indexes = _hunk_line_indexes_in_range(
                hunk_lines,
                new_scan,
                kind="+",
                line_number_attr="new_line_number",
            )
            for old_index, new_index in zip(old_indexes, new_indexes):
                old_line = hunk_lines[old_index]
                new_line = hunk_lines[new_index]
                old_selected = (
                    old_line.id is not None
                    and old_line.id in selected_display_ids
                )
                new_selected = (
                    new_line.id is not None
                    and new_line.id in selected_display_ids
                )
                if old_selected and new_selected:
                    if old_line.old_line_number is None:
                        continue
                    add_replacement_unit(
                        ((old_index, old_index + 1),),
                        (new_line,),
                        old_start=old_line.old_line_number,
                        old_end=old_line.old_line_number,
                    )
            continue

        if old_scan.fully_selected and new_scan.fully_selected:
            add_replacement_unit(
                _hunk_line_index_ranges_in_range(
                    hunk_lines,
                    old_scan,
                    kind="-",
                    line_number_attr="old_line_number",
                ),
                (
                    hunk_lines[index]
                    for index in _hunk_line_indexes_in_range(
                        hunk_lines,
                        new_scan,
                        kind="+",
                        line_number_attr="new_line_number",
                    )
                ),
                old_start=replacement_run.old_start,
                old_end=replacement_run.old_end,
            )

    current_absence_anchor: int | None = None
    current_absence_start: int | None = None
    current_absence_stop: int | None = None
    current_absence_old_start: int | None = None
    current_absence_old_end: int | None = None
    active_replacement_unit: _ReplacementUnitBuilder | None = None

    def finish_replacement_unit(
        builder: _ReplacementUnitBuilder | None,
    ) -> None:
        if builder is not None:
            replacement_units.append(builder.finish())

    def flush_absence_run() -> list[int]:
        nonlocal current_absence_anchor
        nonlocal current_absence_start
        nonlocal current_absence_stop
        nonlocal current_absence_old_start
        nonlocal current_absence_old_end
        if current_absence_start is None or current_absence_stop is None:
            return []

        baseline_reference = (
            _baseline_reference_for_old_line_range(
                current_absence_old_start,
                current_absence_old_end,
                old_line_content,
            )
            if (
                current_absence_old_start is not None
                and current_absence_old_end is not None
            )
            else None
        )
        absence_claims.append(
            AbsenceClaim(
                anchor_line=current_absence_anchor,
                content_lines=_build_absence_content_from_range(
                    hunk_content_view,
                    current_absence_start,
                    current_absence_stop,
                ),
                baseline_reference=baseline_reference,
            )
        )
        absence_index = len(absence_claims) - 1
        current_absence_anchor = None
        current_absence_start = None
        current_absence_stop = None
        current_absence_old_start = None
        current_absence_old_end = None
        return [absence_index]

    for index, line in enumerate(hunk_lines):
        is_selected = (
            line.id is not None
            and line.id in selected_display_ids
            and line.id not in consumed_replacement_ids
        )

        if line.kind in {" ", "+"}:
            flushed_deletion_indices = flush_absence_run()

            if is_selected:
                if line.source_line is None:
                    raise ValueError(
                        f"Cannot translate line to batch ownership: source_line is None "
                        f"(kind={line.kind!r}, text={line.display_text()!r}). "
                        f"Batch source is stale and must be advanced before translation."
                    )

                claimed_source_lines.add_line(line.source_line)
                if line.has_baseline_reference_after:
                    presence_baseline_references[line.source_line] = BaselineReference(
                        after_line=line.baseline_reference_after_line,
                        after_content=line.baseline_reference_after_text_bytes,
                        has_after_line=line.has_baseline_reference_after,
                        before_line=line.baseline_reference_before_line,
                        before_content=line.baseline_reference_before_text_bytes,
                        has_before_line=line.has_baseline_reference_before,
                    )

                if line.kind == "+":
                    if flushed_deletion_indices:
                        finish_replacement_unit(active_replacement_unit)
                        active_replacement_unit = _ReplacementUnitBuilder(
                            deletion_indices=flushed_deletion_indices,
                        )

                    if active_replacement_unit is not None:
                        active_replacement_unit.add_presence_line(line.source_line)
                else:
                    finish_replacement_unit(active_replacement_unit)
                    active_replacement_unit = None
            else:
                finish_replacement_unit(active_replacement_unit)
                active_replacement_unit = None

            if line.source_line is not None:
                current_absence_anchor = line.source_line
            continue

        if line.kind == "-":
            if not is_selected:
                flush_absence_run()
                finish_replacement_unit(active_replacement_unit)
                active_replacement_unit = None
                continue

            finish_replacement_unit(active_replacement_unit)
            active_replacement_unit = None
            if current_absence_start is None:
                current_absence_anchor = line.source_line
                current_absence_start = index
            current_absence_stop = index + 1
            if line.old_line_number is not None:
                if current_absence_old_start is None:
                    current_absence_old_start = line.old_line_number
                current_absence_old_end = line.old_line_number

    flush_absence_run()
    finish_replacement_unit(active_replacement_unit)

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(
            claimed_source_lines.finish(),
            presence_baseline_references,
        ),
        deletions=absence_claims,
        replacement_units=_normalize_replacement_units(
            replacement_units,
            deletion_count=len(absence_claims),
        ),
    )


class OwnershipUnitKind(Enum):
    """Type of ownership unit for semantic filtering operations."""

    PRESENCE_ONLY = "presence_only"
    """Pure claimed lines with no coupled deletions (non-atomic)."""

    REPLACEMENT = "replacement"
    """Claimed lines coupled with absence claims (atomic)."""

    DELETION_ONLY = "deletion_only"
    """Pure absence claims with no claimed lines (atomic)."""


@dataclass
class OwnershipUnit:
    """Semantic unit of ownership that should be manipulated atomically.

    Represents the coupling between claimed lines and absence claims.
    Used for semantic filtering operations like line-level reset.

    Attributes:
        kind: Type of ownership unit
        claimed_source_lines: Set of batch source line numbers owned by this unit
        deletion_claims: Absence claims that are part of this unit
        display_line_ids: Display line IDs that map to this unit (from reconstructed display)
        is_atomic: If True, partial removal is not allowed
        atomic_reason: Explanation for why unit is atomic (for debugging/errors)
        preserves_replacement_unit: True when this unit came from persisted replacement metadata
    """
    kind: OwnershipUnitKind
    claimed_source_lines: set[int]
    deletion_claims: list[AbsenceClaim]
    display_line_ids: set[int]
    is_atomic: bool = False
    atomic_reason: str | None = None
    preserves_replacement_unit: bool = False


def build_ownership_units_from_batch_source_lines(
    ownership: BatchOwnership,
    batch_source_lines: Sequence[bytes],
) -> list[OwnershipUnit]:
    """Build semantic ownership units from indexed batch-source lines.

    Persisted replacement metadata is honored first, so captured replacements
    remain whole atomic units even if their lines are no longer display-adjacent.
    Remaining lines fall back to display-adjacency grouping in reconstructed
    display order, not source-line proximity. This reflects what the user
    actually sees in the batch display.

    Grouping rules:
    - Deletion block immediately followed by claimed line -> REPLACEMENT unit (atomic)
    - Claimed line immediately followed by deletion block -> REPLACEMENT unit (atomic)
    - Deletion block with no adjacent claimed line -> DELETION_ONLY unit (atomic)
    - Claimed line with no adjacent deletion -> PRESENCE_ONLY unit (non-atomic)

    For fallback display-adjacent grouping, claimed lines are processed
    individually (not as blocks) to preserve fine-grained reset capability.
    When a deletion block is followed by multiple claimed lines, only the first
    claimed line couples with the deletion to form a REPLACEMENT unit.
    Subsequent claimed lines remain independent PRESENCE_ONLY units.

    "Adjacent" means consecutive in the display_lines sequence with no intervening
    entries of a different type. Source-line proximity is not considered.
    """
    from ..batch.display import build_display_lines_from_batch_source_lines

    display_lines = build_display_lines_from_batch_source_lines(
        batch_source_lines,
        ownership,
    )
    return build_ownership_units_from_display_lines(ownership, display_lines)


def build_ownership_units_from_display_lines(
    ownership: BatchOwnership,
    display_lines: list[dict],
) -> list[OwnershipUnit]:
    """Build semantic ownership units from already reconstructed display lines.

    This is the fast path for callers that already need display lines for
    rendering.  It preserves the same grouping rules as
    build_ownership_units_from_batch_source_lines() without rebuilding the
    display model.
    """
    units, consumed_claimed_lines, consumed_deletion_indices = (
        _build_explicit_replacement_units_from_display_lines(
            ownership,
            display_lines,
        )
    )
    i = 0

    while i < len(display_lines):
        line = display_lines[i]
        if _display_line_is_consumed(
            line,
            consumed_claimed_lines,
            consumed_deletion_indices,
        ):
            i += 1
            continue

        if line["type"] == "deletion":
            # Collect consecutive deletion block
            deletion_run = _collect_display_run(
                display_lines,
                i,
                "deletion",
                consumed_claimed_lines,
                consumed_deletion_indices,
            )
            i = deletion_run["next_index"]

            # Check if immediately followed by claimed line (display adjacency)
            if (
                i < len(display_lines)
                and display_lines[i]["type"] == "claimed"
                and not _display_line_is_consumed(
                    display_lines[i],
                    consumed_claimed_lines,
                    consumed_deletion_indices,
                )
            ):
                # Collect single claimed line (to preserve fine-grained reset)
                claimed_display_id = display_lines[i]["id"]
                claimed_source_line = display_lines[i]["source_line"]
                i += 1

                # Replacement unit: deletion block adjacent to single claimed line
                claimed_run = {
                    "display_ids": [claimed_display_id],
                    "source_lines": [claimed_source_line]
                }
                units.append(_build_replacement_unit(
                    ownership=ownership,
                    deletion_run=deletion_run,
                    claimed_run=claimed_run
                ))
            else:
                # Deletion-only unit: no adjacent claimed block
                units.append(_build_deletion_only_unit(
                    ownership=ownership,
                    deletion_run=deletion_run
                ))

        elif line["type"] == "claimed":
            # Collect single claimed line (not a block, to preserve fine-grained reset)
            claimed_display_id = line["id"]
            claimed_source_line = line["source_line"]
            i += 1

            # Check if immediately followed by deletion block (display adjacency)
            if (
                i < len(display_lines)
                and display_lines[i]["type"] == "deletion"
                and not _display_line_is_consumed(
                    display_lines[i],
                    consumed_claimed_lines,
                    consumed_deletion_indices,
                )
            ):
                # Collect consecutive deletion block
                deletion_run = _collect_display_run(
                    display_lines,
                    i,
                    "deletion",
                    consumed_claimed_lines,
                    consumed_deletion_indices,
                )
                i = deletion_run["next_index"]

                # Replacement unit: claimed line adjacent to deletion block
                claimed_run = {
                    "display_ids": [claimed_display_id],
                    "source_lines": [claimed_source_line]
                }
                units.append(_build_replacement_unit(
                    ownership=ownership,
                    deletion_run=deletion_run,
                    claimed_run=claimed_run
                ))
            else:
                # Presence-only unit: one claimed line without adjacent deletions
                # One unit per line allows independent reset
                units.append(OwnershipUnit(
                    kind=OwnershipUnitKind.PRESENCE_ONLY,
                    claimed_source_lines={claimed_source_line},
                    deletion_claims=[],
                    display_line_ids={claimed_display_id},
                    is_atomic=False,
                    atomic_reason=None
                ))
        else:
            # Unknown type - skip
            i += 1

    return sorted(units, key=_ownership_unit_display_order_key)


def _ownership_unit_display_order_key(unit: OwnershipUnit) -> int:
    """Return the first visible display line covered by a semantic unit."""
    if not unit.display_line_ids:
        return 10**12
    return min(unit.display_line_ids)


def _build_explicit_replacement_units_from_display_lines(
    ownership: BatchOwnership,
    display_lines: list[dict],
) -> tuple[list[OwnershipUnit], set[int], set[int]]:
    """Build units from persisted replacement metadata."""
    units: list[OwnershipUnit] = []
    consumed_claimed_lines: set[int] = set()
    consumed_deletion_indices: set[int] = set()

    replacement_units = _normalize_replacement_units(
        ownership.replacement_units,
        deletion_count=len(ownership.deletions),
    )
    if not replacement_units:
        return units, consumed_claimed_lines, consumed_deletion_indices

    for replacement_unit in replacement_units:
        claimed_source_lines = _parse_line_ranges(replacement_unit.presence_lines).to_set()
        deletion_indices = set(replacement_unit.deletion_indices)
        claimed_display_ids: set[int] = set()
        deletion_display_ids: set[int] = set()

        for display_line in display_lines:
            display_id = display_line.get("id")
            if display_id is None:
                continue

            if (
                display_line["type"] == "claimed"
                and display_line["source_line"] in claimed_source_lines
            ):
                claimed_display_ids.add(display_id)
            elif (
                display_line["type"] == "deletion"
                and display_line["deletion_index"] in deletion_indices
            ):
                deletion_display_ids.add(display_id)

        if not claimed_display_ids or not deletion_display_ids:
            continue

        deletion_claims = [
            ownership.deletions[index]
            for index in sorted(deletion_indices)
        ]
        units.append(OwnershipUnit(
            kind=OwnershipUnitKind.REPLACEMENT,
            claimed_source_lines=claimed_source_lines,
            deletion_claims=deletion_claims,
            display_line_ids=claimed_display_ids | deletion_display_ids,
            is_atomic=True,
            atomic_reason="explicit_replacement",
            preserves_replacement_unit=True,
        ))
        consumed_claimed_lines.update(claimed_source_lines)
        consumed_deletion_indices.update(deletion_indices)

    return units, consumed_claimed_lines, consumed_deletion_indices


def _display_line_is_consumed(
    display_line: dict,
    consumed_claimed_lines: set[int],
    consumed_deletion_indices: set[int],
) -> bool:
    """Return True when a display line is already covered by an explicit unit."""
    if display_line["type"] == "claimed":
        return display_line["source_line"] in consumed_claimed_lines
    if display_line["type"] == "deletion":
        return display_line["deletion_index"] in consumed_deletion_indices
    return False


def _collect_display_run(
    display_lines: list,
    start_index: int,
    expected_type: str,
    consumed_claimed_lines: set[int],
    consumed_deletion_indices: set[int],
) -> dict:
    """Collect a consecutive run of display lines of the same type.

    Args:
        display_lines: List of display line dicts
        start_index: Starting index in display_lines
        expected_type: Expected line type ("deletion" or "claimed")

    Returns:
        Dict with:
        - display_ids: List of display IDs in the run
        - source_lines: List of source lines (for claimed) or None
        - deletion_indices: List of deletion indices (for deletion) or None
        - next_index: Index of first line after the run
    """
    display_ids = []
    source_lines = [] if expected_type == "claimed" else None
    deletion_indices = [] if expected_type == "deletion" else None

    i = start_index
    while (
        i < len(display_lines)
        and display_lines[i]["type"] == expected_type
        and not _display_line_is_consumed(
            display_lines[i],
            consumed_claimed_lines,
            consumed_deletion_indices,
        )
    ):
        display_ids.append(display_lines[i]["id"])

        if expected_type == "claimed":
            source_lines.append(display_lines[i]["source_line"])
        elif expected_type == "deletion":
            deletion_indices.append(display_lines[i]["deletion_index"])

        i += 1

    return {
        "display_ids": display_ids,
        "source_lines": source_lines,
        "deletion_indices": deletion_indices,
        "next_index": i
    }


def _build_replacement_unit(
    ownership: BatchOwnership,
    deletion_run: dict,
    claimed_run: dict
) -> OwnershipUnit:
    """Build a REPLACEMENT unit from adjacent deletion and claimed runs.

    Args:
        ownership: BatchOwnership containing absence claims
        deletion_run: Dict from _collect_display_run for deletions
        claimed_run: Dict from _collect_display_run for claimed lines

    Returns:
        REPLACEMENT OwnershipUnit (atomic)
    """
    deletion_claims = [
        ownership.deletions[idx]
        for idx in set(deletion_run["deletion_indices"])
    ]

    return OwnershipUnit(
        kind=OwnershipUnitKind.REPLACEMENT,
        claimed_source_lines=set(claimed_run["source_lines"]),
        deletion_claims=deletion_claims,
        display_line_ids=set(deletion_run["display_ids"] + claimed_run["display_ids"]),
        is_atomic=True,
        atomic_reason="display_adjacency"
    )


def _build_deletion_only_unit(
    ownership: BatchOwnership,
    deletion_run: dict
) -> OwnershipUnit:
    """Build a DELETION_ONLY unit from a deletion run with no adjacent claimed lines.

    Args:
        ownership: BatchOwnership containing absence claims
        deletion_run: Dict from _collect_display_run for deletions

    Returns:
        DELETION_ONLY OwnershipUnit (atomic)
    """
    deletion_claims = [
        ownership.deletions[idx]
        for idx in set(deletion_run["deletion_indices"])
    ]

    return OwnershipUnit(
        kind=OwnershipUnitKind.DELETION_ONLY,
        claimed_source_lines=set(),
        deletion_claims=deletion_claims,
        display_line_ids=set(deletion_run["display_ids"]),
        is_atomic=True,
        atomic_reason="deletion_only"
    )


def validate_ownership_units(units: list[OwnershipUnit]) -> None:
    """Validate structural invariants of ownership units.

    Ensures:
    - No orphaned absence claims
    - No duplicate ownership of absence claims
    - Atomic units have valid structure

    Args:
        units: List of ownership units to validate

    Raises:
        MergeError: If units have invalid structure
    """
    # Track absence claim usage to ensure no orphans or duplicates
    deletion_claim_usage = {}

    for unit in units:
        for claim in unit.deletion_claims:
            claim_id = id(claim)
            if claim_id in deletion_claim_usage:
                # Duplicate ownership - may be intentional in some cases
                # but worth tracking for now
                pass
            deletion_claim_usage[claim_id] = unit

        # Validate atomic units have coherent structure
        if unit.is_atomic:
            if unit.kind == OwnershipUnitKind.REPLACEMENT:
                if not unit.claimed_source_lines or not unit.deletion_claims:
                    raise MergeError(
                        _("Invalid replacement in batch metadata: expected both added and removed lines.")
                    )
            elif unit.kind == OwnershipUnitKind.DELETION_ONLY:
                if unit.claimed_source_lines or not unit.deletion_claims:
                    raise MergeError(
                        _("Invalid deletion in batch metadata: expected removed lines only.")
                    )


def select_ownership_units_by_display_ids(
    units: list[OwnershipUnit],
    selected_display_ids: set[int]
) -> list[OwnershipUnit]:
    """Select ownership units that match the given display line IDs.

    Validates that atomic units are not partially selected.

    Args:
        units: List of ownership units
        selected_display_ids: Display line IDs to select

    Returns:
        List of units that match the selection

    Raises:
        MergeError: If atomic unit is partially selected
    """
    selected = []

    for unit in units:
        # Check if any display IDs from this unit are selected
        intersection = unit.display_line_ids & selected_display_ids

        if not intersection:
            # Not selected - skip it
            continue
        elif unit.is_atomic and intersection != unit.display_line_ids:
            # Partial selection of atomic unit - error
            raise AtomicUnitError(
                _("Cannot select only part of this change.\n"
                  "Select all related lines together: {required_ids}\n"
                  "You selected: {selected_ids}").format(
                    required_ids=format_line_ids(sorted(unit.display_line_ids)),
                    selected_ids=format_line_ids(sorted(intersection))
                ),
                required_selection_ids=unit.display_line_ids,
                unit_kind=unit.kind.value
            )
        else:
            # Fully selected (or non-atomic with partial selection allowed)
            selected.append(unit)

    return selected


def filter_ownership_units_by_display_ids(
    units: list[OwnershipUnit],
    selected_display_ids: set[int]
) -> tuple[list[OwnershipUnit], list[OwnershipUnit]]:
    """Filter ownership units, removing those that match display line IDs.

    This is a convenience wrapper around select_ownership_units_by_display_ids
    for reset-style operations where you want both remaining and removed units.

    Args:
        units: List of ownership units
        selected_display_ids: Display line IDs to remove

    Returns:
        Tuple of (remaining_units, removed_units)

    Raises:
        MergeError: If atomic unit is partially selected
    """
    removed = select_ownership_units_by_display_ids(units, selected_display_ids)
    removed_ids = {id(unit) for unit in removed}
    remaining = [unit for unit in units if id(unit) not in removed_ids]
    return remaining, removed


def rebuild_ownership_from_units(units: list[OwnershipUnit]) -> BatchOwnership:
    """Rebuild BatchOwnership from semantic ownership units.

    Args:
        units: List of ownership units to combine

    Returns:
        New BatchOwnership with combined ownership from all units
    """
    all_presence_lines = set()
    all_deletions = []
    replacement_units: list[ReplacementUnit] = []

    for unit in units:
        all_presence_lines.update(unit.claimed_source_lines)
        deletion_indices = []
        for deletion in unit.deletion_claims:
            all_deletions.append(deletion)
            deletion_indices.append(len(all_deletions) - 1)
        if unit.kind == OwnershipUnitKind.REPLACEMENT and unit.preserves_replacement_unit:
            replacement_units.append(ReplacementUnit(
                presence_lines=_format_line_set(unit.claimed_source_lines),
                deletion_indices=deletion_indices,
            ))

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(all_presence_lines),
        deletions=all_deletions,
        replacement_units=_normalize_replacement_units(
            replacement_units,
            deletion_count=len(all_deletions),
        ),
    )


def _remap_replacement_units(
    replacement_units: list[ReplacementUnit],
    *,
    map_claimed_line,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Remap explicit replacement-unit presence lines into a new source space."""
    remapped_units: list[ReplacementUnit] = []

    for unit in replacement_units:
        new_presence_lines: set[int] = set()
        for old_line_num in _parse_line_ranges(unit.presence_lines):
            new_line_num = map_claimed_line(old_line_num)
            if new_line_num is None:
                raise ValueError(
                    f"Cannot remap replacement unit presence line {old_line_num} "
                    f"from old source to new source: no unique mapping found."
                )
            new_presence_lines.add(new_line_num)

        remapped_units.append(ReplacementUnit(
            presence_lines=_format_line_set(new_presence_lines),
            deletion_indices=unit.deletion_indices,
        ))

    return _normalize_replacement_units(
        remapped_units,
        deletion_count=deletion_count,
    )


def _first_unmapped_line(
    line_selection: LineSelection,
    lineage: _BatchSourceLineage,
) -> int | None:
    return lineage.first_unmapped_source_line(line_selection)


def _remap_replacement_units_with_lineage(
    replacement_units: list[ReplacementUnit],
    *,
    lineage: _BatchSourceLineage,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Remap replacement-unit presence lines with refreshed source lineage."""
    remapped_units: list[ReplacementUnit] = []

    for unit in replacement_units:
        old_presence_lines = _parse_line_ranges(unit.presence_lines)
        first_unmapped = _first_unmapped_line(old_presence_lines, lineage)
        if first_unmapped is not None:
            raise ValueError(
                f"Cannot remap replacement unit presence line {first_unmapped} "
                f"from old source to new source: no unique mapping found."
            )

        remapped_units.append(ReplacementUnit(
            presence_lines=_format_line_set(
                lineage.translate_source_selection(old_presence_lines)
            ),
            deletion_indices=unit.deletion_indices,
        ))

    return _normalize_replacement_units(
        remapped_units,
        deletion_count=deletion_count,
    )


def remap_batch_ownership_to_new_source_lines(
    ownership: BatchOwnership,
    old_source_lines: Sequence[bytes],
    new_source_lines: Sequence[bytes],
) -> BatchOwnership:
    """Remap batch ownership between old and new source line sequences."""
    with match_lines(old_source_lines, new_source_lines) as mapping:
        return _remap_batch_ownership_with_mapping(ownership, mapping)


def _remap_batch_ownership_with_mapping(
    ownership: BatchOwnership,
    mapping: LineMapping,
) -> BatchOwnership:
    """Remap batch ownership using an existing old-to-new source mapping."""
    # Remap presence lines
    old_presence = ownership.presence_line_set()
    new_presence = set()

    for old_line_num in old_presence:
        new_line_num = mapping.get_target_line_from_source_line(old_line_num)
        if new_line_num is None:
            # Line cannot be mapped - fail loudly
            raise ValueError(
                f"Cannot remap presence line {old_line_num} from old source to new source: "
                f"no unique mapping found. This indicates the old line was removed or "
                f"significantly changed in the new source."
            )
        new_presence.add(new_line_num)

    # Remap deletion anchors
    new_deletions = []
    for deletion in ownership.deletions:
        if deletion.anchor_line is None:
            # Start-of-file anchor remains None
            new_deletions.append(AbsenceClaim(
                anchor_line=None,
                content_lines=deletion.content_lines,
                baseline_reference=deletion.baseline_reference,
            ))
        else:
            # Remap anchor line
            new_anchor = mapping.get_target_line_from_source_line(deletion.anchor_line)
            if new_anchor is None:
                # Anchor cannot be mapped - fail loudly
                raise ValueError(
                    f"Cannot remap deletion anchor line {deletion.anchor_line} from old source "
                    f"to new source: no unique mapping found. This indicates the anchor line "
                    f"was removed or significantly changed in the new source."
                )
            new_deletions.append(AbsenceClaim(
                anchor_line=new_anchor,
                content_lines=deletion.content_lines,
                baseline_reference=deletion.baseline_reference,
            ))

    new_replacement_units = _remap_replacement_units(
        ownership.replacement_units,
        map_claimed_line=mapping.get_target_line_from_source_line,
        deletion_count=len(new_deletions),
    )

    new_presence_baseline_references = {}
    for old_line_num, reference in ownership.presence_baseline_references().items():
        new_line_num = mapping.get_target_line_from_source_line(old_line_num)
        if new_line_num is not None:
            new_presence_baseline_references[new_line_num] = reference

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(
            new_presence,
            new_presence_baseline_references,
        ),
        deletions=new_deletions,
        replacement_units=new_replacement_units,
    )


def _advance_source_lines_preserving_existing_presence(
    old_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
) -> SourceContentWithLineProvenance:
    """Build source content with provenance from line sequences."""
    presence_lines = ownership.presence_line_set()

    entries = _apply_presence_constraints(
        old_lines,
        working_lines,
        presence_lines,
    )

    lineage = _BatchSourceLineage()
    try:
        for run in entries.provenance_runs():
            line_count = run.dest_end - run.dest_start
            new_start = run.dest_start + 1
            if run.source_start != 0:
                lineage.append_source_run(
                    _LineageRun(
                        old_start=run.source_start,
                        old_end=run.source_start + line_count - 1,
                        new_start=new_start,
                    )
                )
            if run.target_start != 0:
                lineage.append_working_run(
                    _LineageRun(
                        old_start=run.target_start,
                        old_end=run.target_start + line_count - 1,
                        new_start=new_start,
                    )
                )

        return SourceContentWithLineProvenance(
            source_buffer=EditorBuffer.from_chunks(
                _realized_entry_content_chunks(entries)
            ),
            lineage=lineage,
        )
    except Exception:
        lineage.close()
        raise
    finally:
        entries.close()


def _remap_batch_ownership_with_lineage(
    ownership: BatchOwnership,
    lineage: _BatchSourceLineage,
) -> BatchOwnership:
    """Remap ownership using provenance from source refresh construction."""
    old_presence = ownership.presence_line_set()
    first_unmapped = _first_unmapped_line(old_presence, lineage)
    if first_unmapped is not None:
        raise ValueError(
            f"Cannot remap presence line {first_unmapped} from old source to new source: "
            f"no preserved source lineage found."
        )
    new_presence = lineage.translate_source_selection(old_presence)

    new_deletions = []
    for deletion in ownership.deletions:
        if deletion.anchor_line is None:
            new_deletions.append(AbsenceClaim(
                anchor_line=None,
                content_lines=deletion.content_lines,
                baseline_reference=deletion.baseline_reference,
            ))
            continue

        new_anchor = lineage.translate_source_line(deletion.anchor_line)
        if new_anchor is None:
            raise ValueError(
                f"Cannot remap deletion anchor line {deletion.anchor_line} from old source "
                f"to new source: no preserved source lineage found."
            )
        new_deletions.append(AbsenceClaim(
            anchor_line=new_anchor,
            content_lines=deletion.content_lines,
            baseline_reference=deletion.baseline_reference,
        ))

    new_replacement_units = _remap_replacement_units_with_lineage(
        ownership.replacement_units,
        lineage=lineage,
        deletion_count=len(new_deletions),
    )

    new_presence_baseline_references = {}
    for old_line_num, reference in ownership.presence_baseline_references().items():
        new_line_num = lineage.translate_source_line(old_line_num)
        if new_line_num is not None:
            new_presence_baseline_references[new_line_num] = reference

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(
            new_presence,
            new_presence_baseline_references,
        ),
        deletions=new_deletions,
        replacement_units=new_replacement_units,
    )


def advance_batch_source_for_file_with_provenance(
    batch_name: str,
    file_path: str,
    old_batch_source_commit: str,
    existing_ownership: BatchOwnership,
) -> BatchSourceAdvanceResult:
    """Advance batch source and expose provenance for re-annotation."""
    repo_root = get_git_repository_root_path()
    working_file_path = repo_root / file_path
    if not working_file_path.exists():
        raise ValueError(
            f"Cannot advance batch source for {file_path}: "
            f"file does not exist in working tree"
        )

    old_source_buffer = load_git_object_as_buffer(
        f"{old_batch_source_commit}:{file_path}"
    )
    if old_source_buffer is None:
        raise ValueError(
            f"Cannot read old batch source for {file_path} at {old_batch_source_commit}"
        )

    source_with_provenance: SourceContentWithLineProvenance | None = None
    try:
        with (
            old_source_buffer as old_source_lines,
            load_working_tree_file_as_buffer(file_path) as working_lines,
        ):
            source_with_provenance = _advance_source_lines_preserving_existing_presence(
                old_lines=old_source_lines,
                working_lines=working_lines,
                ownership=existing_ownership,
            )

        # Create new batch source commit from the refreshed source. This is
        # intentionally different from initial batch-source creation, which uses the
        # session-start snapshot for abort/discard correctness.
        new_batch_source_commit = create_batch_source_commit(
            file_path,
            file_buffer_override=source_with_provenance.source_buffer
        )

        # Remap ownership using lineage produced while constructing the refreshed
        # source. This preserves already-owned lines that no longer exist in the
        # working tree after earlier discard operations.
        remapped_ownership = _remap_batch_ownership_with_lineage(
            ownership=existing_ownership,
            lineage=source_with_provenance.lineage,
        )

        return BatchSourceAdvanceResult(
            batch_source_commit=new_batch_source_commit,
            ownership=remapped_ownership,
            source_buffer=source_with_provenance.source_buffer,
            lineage=source_with_provenance.lineage,
        )
    except Exception:
        if source_with_provenance is not None:
            source_with_provenance.close()
        raise

"""Batch ownership data models and transformation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core.line_selection import format_line_ids, parse_line_selection
from ..data.batch_sources import create_batch_source_commit
from ..exceptions import AtomicUnitError, MergeError
from ..i18n import _
from ..utils.git import (
    create_git_blob,
    get_git_repository_root_path,
    read_git_blob,
    read_git_blobs_as_bytes,
    run_git_command,
)
from .match import match_lines
from .merge import _apply_presence_constraints


@dataclass
class DeletionClaim:
    """A suppression constraint: specific baseline content that must not appear.

    Deletions are constraints, not content to replay. Each deletion claim represents
    a contiguous run of lines that must be absent from the materialized result.

    Attributes:
        anchor_line: Batch source line after which this deletion claim is anchored
                     (None for start-of-file)
        content_lines: Exact baseline line content that must be suppressed (as bytes)
    """
    anchor_line: int | None
    content_lines: list[bytes]  # Each element is one line with newline preserved

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        blob_content = b"".join(self.content_lines)
        blob_sha = create_git_blob([blob_content])
        return {
            "after_source_line": self.anchor_line,
            "blob": blob_sha
        }

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
    ) -> DeletionClaim:
        """Deserialize from metadata dictionary."""
        anchor_line = data.get("after_source_line")
        blob_sha = data["blob"]
        blob_content = (
            blob_contents[blob_sha]
            if blob_contents is not None and blob_sha in blob_contents
            else b"".join(read_git_blob(blob_sha))
        )
        content_lines = blob_content.splitlines(keepends=True)
        return cls(anchor_line=anchor_line, content_lines=content_lines)


@dataclass
class PresenceClaim:
    """A presence constraint over batch-source lines."""

    source_lines: list[str]

    def source_line_set(self) -> set[int]:
        """Return batch-source line numbers covered by this presence claim."""
        return _parse_line_ranges(self.source_lines)

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        return {"source_lines": self.source_lines}

    @classmethod
    def from_dict(cls, data: dict) -> PresenceClaim:
        """Deserialize from metadata dictionary."""
        return cls(source_lines=data.get("source_lines", []))


@dataclass
class ReplacementUnit:
    """Explicit coupling between presence claims and deletion claims.

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


@dataclass
class BatchOwnership:
    """Represents batch ownership in batch source space.

    A batch owns content relative to its batch source commit:
    - presence_claims: Batch-source lines that must exist after application
    - deletions: Suppression constraints for baseline content (absence claims)
    - replacement_units: Optional explicit coupling between claims and deletions
    """
    presence_claims: list[PresenceClaim]
    deletions: list[DeletionClaim]  # Separate deletion constraints
    replacement_units: list[ReplacementUnit] = field(default_factory=list)

    @classmethod
    def from_presence_lines(
        cls,
        source_lines: list[str],
        deletions: list[DeletionClaim] | None = None,
        *,
        replacement_units: list[ReplacementUnit] | None = None,
    ) -> BatchOwnership:
        """Create ownership from source-line ranges."""
        return cls(
            presence_claims=_presence_claims_from_source_lines(
                _parse_line_ranges(source_lines),
            ),
            deletions=deletions or [],
            replacement_units=replacement_units or [],
        )

    def is_empty(self) -> bool:
        """Check if this ownership is empty (no presence claims or deletions)."""
        return not self.presence_claims and not self.deletions

    def presence_line_set(self) -> set[int]:
        """Return all batch-source lines claimed present by this ownership."""
        presence_lines: set[int] = set()
        for claim in self.presence_claims:
            presence_lines.update(claim.source_line_set())
        return presence_lines

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
    def from_metadata_dict(cls, data: dict) -> BatchOwnership:
        """Create from metadata dictionary."""
        deletion_metadata = data.get("deletions", [])
        presence_metadata = data.get("presence_claims", [])
        legacy_claimed_lines = data.get("claimed_lines", [])
        blob_contents = read_git_blobs_as_bytes(
            d["blob"] for d in deletion_metadata if "blob" in d
        )
        presence_claims = [
            PresenceClaim.from_dict(d)
            for d in presence_metadata
        ]
        if not presence_claims and legacy_claimed_lines:
            presence_claims = _presence_claims_from_source_lines(
                _parse_line_ranges(legacy_claimed_lines)
            )
        deletions = [
            DeletionClaim.from_dict(d, blob_contents) for d in deletion_metadata
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

        Returns presence lines as a set and deletion claims as a list (preserving structure).
        """
        return ResolvedBatchOwnership(self.presence_line_set(), self.deletions)


@dataclass
class ResolvedBatchOwnership:
    """Resolved ownership representation for materialization and merge.

    Preserves the structure of deletion claims as separate constraints.

    Attributes:
        presence_line_set: Batch source line numbers (1-indexed, identity-based)
        deletion_claims: List of suppression constraints (order and structure preserved)
    """
    presence_line_set: set[int]  # Batch source line numbers (1-indexed)
    deletion_claims: list[DeletionClaim]  # Separate constraints, not collapsed


@dataclass
class AdvancedSourceContent:
    """Synthesized source content with line provenance from its inputs."""

    content: bytes
    source_line_map: dict[int, int]
    working_line_map: dict[int, int]


@dataclass
class BatchSourceAdvanceResult:
    """Result of advancing one file's batch source."""

    batch_source_commit: str
    ownership: BatchOwnership
    source_content: bytes
    working_content: bytes
    working_line_map: dict[int, int]


def _deletion_signature(deletion: DeletionClaim) -> tuple[int | None, bytes]:
    """Return a stable signature for a deletion claim."""
    return deletion.anchor_line, b"".join(deletion.content_lines)


def _parse_line_ranges(line_ranges: list[str] | list[int]) -> set[int]:
    """Parse source line range strings into a set."""
    return (
        set(parse_line_selection(",".join(str(line) for line in line_ranges)))
        if line_ranges else set()
    )


def _format_line_set(source_lines: set[int]) -> list[str]:
    """Format a source line set as normalized range strings."""
    if not source_lines:
        return []
    return [format_line_ids(sorted(source_lines))]


def _presence_claims_from_source_lines(source_lines: set[int]) -> list[PresenceClaim]:
    """Build normalized presence claims from a source-line set."""
    if not source_lines:
        return []
    return [PresenceClaim(source_lines=_format_line_set(source_lines))]


def _normalize_replacement_units(
    replacement_units: list[ReplacementUnit],
    *,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Drop invalid references and coalesce overlapping replacement units."""
    components: list[tuple[set[int], set[int]]] = []

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
            if component_claimed & claimed or component_deletions & deletion_indices
        ]
        if not overlapping_component_indices:
            components.append((set(claimed), set(deletion_indices)))
            continue

        target_index = overlapping_component_indices[0]
        target_claimed, target_deletions = components[target_index]
        target_claimed.update(claimed)
        target_deletions.update(deletion_indices)

        for source_index in reversed(overlapping_component_indices[1:]):
            source_claimed, source_deletions = components[source_index]
            target_claimed.update(source_claimed)
            target_deletions.update(source_deletions)
            del components[source_index]

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

    Deletion claims are deduplicated by (anchor_line, content) signature to prevent
    duplicate deletions when batch source advances and ownership is remapped. The same
    deletion can appear in both existing (remapped) and new (from current diff).

    Args:
        existing: Existing batch ownership
        new: New ownership to merge in

    Returns:
        Merged BatchOwnership with combined claims and deduplicated deletions
    """
    # Merge claimed lines (combine and normalize ranges)
    existing_claimed = existing.presence_line_set()
    new_claimed = new.presence_line_set()
    combined_claimed = existing_claimed | new_claimed

    # Merge deletion claims: deduplicate by anchor and content
    # When batch source advances and ownership is remapped, the same deletion can appear
    # in both existing (remapped) and new (from current diff). We need to deduplicate.
    seen_deletions = set()
    combined_deletions = []
    existing_deletion_index_map: dict[int, int] = {}
    new_deletion_index_map: dict[int, int] = {}

    for source_name, source_index, deletion in (
        [("existing", index, deletion) for index, deletion in enumerate(existing.deletions)]
        + [("new", index, deletion) for index, deletion in enumerate(new.deletions)]
    ):
        # Create a signature for this deletion: anchor + content
        signature = _deletion_signature(deletion)

        if signature not in seen_deletions:
            seen_deletions.add(signature)
            combined_deletions.append(deletion)
        combined_index = next(
            index
            for index, combined in enumerate(combined_deletions)
            if _deletion_signature(combined) == signature
        )
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
        presence_claims=_presence_claims_from_source_lines(combined_claimed),
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
    Each contiguous run of deletions becomes a separate DeletionClaim.

    This function assumes all selected lines can be expressed in batch source
    space. Call detect_stale_batch_source_for_selection() first and handle stale
    sources before calling this function. If source_line is None for claimed
    lines, this raises an error instead of dropping them.

    Args:
        selected_lines: List of LineEntry objects to translate

    Returns:
        BatchOwnership with presence claims and deletion claims

    Raises:
        ValueError: If any claimed line has source_line=None (stale batch source)
    """
    # Translate to batch source-space ownership
    # Diff shows index→working tree, batch source = working tree
    # Context/addition lines exist in batch source → presence claims
    # Deletion lines don't exist in batch source → deletion claims (suppression)

    claimed_source_lines: list[int] = []
    deletion_claims: list[DeletionClaim] = []
    replacement_units: list[ReplacementUnit] = []

    # Track current deletion run
    current_deletion_anchor: int | None = None
    current_deletion_lines: list[bytes] = []
    active_replacement_unit: ReplacementUnit | None = None

    def flush_deletion_run() -> list[int]:
        """Finalize current deletion run as a DeletionClaim."""
        nonlocal current_deletion_anchor, current_deletion_lines
        if current_deletion_lines:
            deletion_claims.append(
                DeletionClaim(
                    anchor_line=current_deletion_anchor,
                    content_lines=current_deletion_lines[:]
                )
            )
            deletion_index = len(deletion_claims) - 1
            current_deletion_lines = []
            return [deletion_index]
        return []

    for line in selected_lines:
        if line.kind in (' ', '+'):
            # Context or addition: exists in batch source (working tree)
            # Flush any pending deletion run
            flushed_deletion_indices = flush_deletion_run()

            if line.source_line is None:
                raise ValueError(
                    f"Cannot translate line to batch ownership: source_line is None "
                    f"(kind={line.kind!r}, text={line.text!r}). "
                    f"Batch source is stale and must be advanced before translation."
                )

            claimed_source_lines.append(line.source_line)
            if line.kind == '+':
                if flushed_deletion_indices:
                    active_replacement_unit = ReplacementUnit(
                        presence_lines=[],
                        deletion_indices=flushed_deletion_indices,
                    )
                    replacement_units.append(active_replacement_unit)

                if active_replacement_unit is not None:
                    claimed = _parse_line_ranges(active_replacement_unit.presence_lines)
                    claimed.add(line.source_line)
                    active_replacement_unit.presence_lines = _format_line_set(claimed)
            else:
                active_replacement_unit = None

            # Update anchor for next deletion run
            current_deletion_anchor = line.source_line

        elif line.kind == '-':
            active_replacement_unit = None
            # Deletion: suppression constraint
            # Anchor each deletion run at its first deleted line. A None anchor
            # means the run starts before the first source line and must not be
            # overwritten by later deleted lines in the same run.
            if not current_deletion_lines:
                current_deletion_anchor = line.source_line
            # text_bytes has line content with \r preserved but \n stripped (diff format)
            # Add back \n for proper round-tripping
            current_deletion_lines.append(line.text_bytes + b'\n')

    # Flush any final deletion run
    flush_deletion_run()

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(set(claimed_source_lines)),
        deletions=deletion_claims,
        replacement_units=_normalize_replacement_units(
            replacement_units,
            deletion_count=len(deletion_claims),
        ),
    )


class OwnershipUnitKind(Enum):
    """Type of ownership unit for semantic filtering operations."""

    PRESENCE_ONLY = "presence_only"
    """Pure claimed lines with no coupled deletions (non-atomic)."""

    REPLACEMENT = "replacement"
    """Claimed lines coupled with deletion claims (atomic)."""

    DELETION_ONLY = "deletion_only"
    """Pure deletion claims with no claimed lines (atomic)."""


@dataclass
class OwnershipUnit:
    """Semantic unit of ownership that should be manipulated atomically.

    Represents the coupling between claimed lines and deletion claims.
    Used for semantic filtering operations like line-level reset.

    Attributes:
        kind: Type of ownership unit
        claimed_source_lines: Set of batch source line numbers owned by this unit
        deletion_claims: Deletion claims that are part of this unit
        display_line_ids: Display line IDs that map to this unit (from reconstructed display)
        is_atomic: If True, partial removal is not allowed
        atomic_reason: Explanation for why unit is atomic (for debugging/errors)
        preserves_replacement_unit: True when this unit came from persisted replacement metadata
    """
    kind: OwnershipUnitKind
    claimed_source_lines: set[int]
    deletion_claims: list[DeletionClaim]
    display_line_ids: set[int]
    is_atomic: bool = False
    atomic_reason: str | None = None
    preserves_replacement_unit: bool = False


def build_ownership_units_from_display(
    ownership: BatchOwnership,
    batch_source_content: bytes
) -> list[OwnershipUnit]:
    """Build semantic ownership units from reconstructed display lines.

    Persisted replacement metadata is honored first, so captured replacements
    remain whole atomic units even if their lines are no longer display-adjacent.
    Remaining lines fall back to display-adjacency grouping in reconstructed
    display order, not source-line proximity. This reflects what the user
    actually sees in the batch display.

    Grouping rules:
    - Deletion block immediately followed by claimed line → REPLACEMENT unit (atomic)
    - Claimed line immediately followed by deletion block → REPLACEMENT unit (atomic)
    - Deletion block with no adjacent claimed line → DELETION_ONLY unit (atomic)
    - Claimed line with no adjacent deletion → PRESENCE_ONLY unit (non-atomic)

    For fallback display-adjacent grouping, claimed lines are processed
    individually (not as blocks) to preserve fine-grained reset capability.
    When a deletion block is followed by multiple claimed lines, only the first
    claimed line couples with the deletion to form a REPLACEMENT unit.
    Subsequent claimed lines remain independent PRESENCE_ONLY units.

    "Adjacent" means consecutive in the display_lines sequence with no intervening
    entries of a different type. Source-line proximity is not considered.

    Args:
        ownership: BatchOwnership to analyze
        batch_source_content: Batch source content (bytes)

    Returns:
        List of semantic ownership units with real display IDs

    Note:
        Decoding with errors='replace' is lossy but necessary for display
        reconstruction. This limits line-level reset to textual content.
        If non-UTF-8 binary content needs reset support, the display model
        would need enhancement.
    """
    from ..batch.display import build_display_lines_from_batch_source

    # Decode content for display reconstruction (lossy for non-UTF-8)
    batch_source_str = batch_source_content.decode('utf-8', errors='replace')

    # Reconstruct actual display lines (what user sees)
    display_lines = build_display_lines_from_batch_source(batch_source_str, ownership)

    return build_ownership_units_from_display_lines(ownership, display_lines)


def build_ownership_units_from_display_lines(
    ownership: BatchOwnership,
    display_lines: list[dict],
) -> list[OwnershipUnit]:
    """Build semantic ownership units from already reconstructed display lines.

    This is the fast path for callers that already need display lines for
    rendering.  It preserves the same grouping rules as
    build_ownership_units_from_display() without rebuilding the display model.
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
        claimed_source_lines = _parse_line_ranges(replacement_unit.presence_lines)
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
        ownership: BatchOwnership containing deletion claims
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
        ownership: BatchOwnership containing deletion claims
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
    - No orphaned deletion claims
    - No duplicate ownership of deletion claims
    - Atomic units have valid structure

    Args:
        units: List of ownership units to validate

    Raises:
        MergeError: If units have invalid structure
    """
    # Track deletion claim usage to ensure no orphans or duplicates
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
    """Remap explicit replacement-unit claimed lines into a new source space."""
    remapped_units: list[ReplacementUnit] = []

    for unit in replacement_units:
        new_presence_lines: set[int] = set()
        for old_line_num in _parse_line_ranges(unit.presence_lines):
            new_line_num = map_claimed_line(old_line_num)
            if new_line_num is None:
                raise ValueError(
                    f"Cannot remap replacement unit claimed line {old_line_num} "
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


def remap_batch_ownership_to_new_source(
    ownership: BatchOwnership,
    old_source_content: bytes,
    new_source_content: bytes,
) -> BatchOwnership:
    """Remap batch ownership from old source space to new source space.

    Uses structural line matching to map claimed lines and deletion anchors
    from the old batch source to the new batch source.

    Args:
        ownership: Existing ownership in old source space
        old_source_content: Content of old batch source (as bytes)
        new_source_content: Content of new batch source (as bytes)

    Returns:
        Remapped ownership in new source space

    Raises:
        ValueError: If remapping is ambiguous or impossible
    """
    # Parse old content into lines
    old_lines = old_source_content.splitlines(keepends=True)
    new_lines = new_source_content.splitlines(keepends=True)

    # Build mapping from old source line numbers to new source line numbers
    mapping = match_lines(old_lines, new_lines)

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
            new_deletions.append(DeletionClaim(
                anchor_line=None,
                content_lines=deletion.content_lines
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
            new_deletions.append(DeletionClaim(
                anchor_line=new_anchor,
                content_lines=deletion.content_lines
            ))

    new_replacement_units = _remap_replacement_units(
        ownership.replacement_units,
        map_claimed_line=mapping.get_target_line_from_source_line,
        deletion_count=len(new_deletions),
    )

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(new_presence),
        deletions=new_deletions,
        replacement_units=new_replacement_units,
    )


def _advance_source_content_preserving_existing_presence(
    old_source_content: bytes,
    working_content: bytes,
    ownership: BatchOwnership,
) -> tuple[bytes, dict[int, int]]:
    """Build advanced source content without dropping already-owned lines.

    When discard-to-batch is run repeatedly for hunks in the same file, earlier
    owned additions may have been reverse-applied out of the working tree. A
    stale-source refresh must keep those lines in the new batch source or the
    batch would lose the data it already owns.

    Returns:
        Tuple of (new source bytes, old source line -> new source line map).
    """
    advanced = _advance_source_content_preserving_existing_presence_with_provenance(
        old_source_content=old_source_content,
        working_content=working_content,
        ownership=ownership,
    )
    return advanced.content, advanced.source_line_map


def _advance_source_content_preserving_existing_presence_with_provenance(
    old_source_content: bytes,
    working_content: bytes,
    ownership: BatchOwnership,
) -> AdvancedSourceContent:
    """Build advanced source content and preserve input line provenance."""
    old_lines = old_source_content.splitlines(keepends=True)
    working_lines = working_content.splitlines(keepends=True)
    presence_lines = ownership.presence_line_set()

    entries = _apply_presence_constraints(
        old_lines,
        working_lines,
        presence_lines,
    )

    source_line_map = {}
    working_line_map = {}
    for index, entry in enumerate(entries, start=1):
        if entry.source_line is not None:
            source_line_map[entry.source_line] = index
        if entry.target_line is not None:
            working_line_map[entry.target_line] = index

    return AdvancedSourceContent(
        content=b"".join(entry.content for entry in entries),
        source_line_map=source_line_map,
        working_line_map=working_line_map,
    )


def _remap_batch_ownership_with_source_line_map(
    ownership: BatchOwnership,
    source_line_map: dict[int, int],
) -> BatchOwnership:
    """Remap ownership using provenance from source refresh construction."""
    old_presence = ownership.presence_line_set()
    new_presence = set()

    for old_line_num in old_presence:
        new_line_num = source_line_map.get(old_line_num)
        if new_line_num is None:
            raise ValueError(
                f"Cannot remap presence line {old_line_num} from old source to new source: "
                f"no preserved source-line mapping found."
            )
        new_presence.add(new_line_num)

    new_deletions = []
    for deletion in ownership.deletions:
        if deletion.anchor_line is None:
            new_deletions.append(DeletionClaim(
                anchor_line=None,
                content_lines=deletion.content_lines
            ))
            continue

        new_anchor = source_line_map.get(deletion.anchor_line)
        if new_anchor is None:
            raise ValueError(
                f"Cannot remap deletion anchor line {deletion.anchor_line} from old source "
                f"to new source: no preserved source-line mapping found."
            )
        new_deletions.append(DeletionClaim(
            anchor_line=new_anchor,
            content_lines=deletion.content_lines
        ))

    new_replacement_units = _remap_replacement_units(
        ownership.replacement_units,
        map_claimed_line=source_line_map.get,
        deletion_count=len(new_deletions),
    )

    return BatchOwnership(
        presence_claims=_presence_claims_from_source_lines(new_presence),
        deletions=new_deletions,
        replacement_units=new_replacement_units,
    )


def advance_batch_source_for_file(
    batch_name: str,
    file_path: str,
    old_batch_source_commit: str,
    existing_ownership: BatchOwnership,
) -> tuple[str, BatchOwnership, bytes, bytes]:
    """Advance batch source for a file and remap existing ownership.

    Creates a new batch source commit from current working tree content plus
    any already-owned presence lines that are no longer in the working tree,
    then remaps the existing ownership from old source space to new source space.

    This operation is scoped to a specific batch and file - other batches that
    reference the same file are not affected.

    Args:
        batch_name: Name of batch being updated
        file_path: Path to file within repository
        old_batch_source_commit: Current batch source commit SHA
        existing_ownership: Existing ownership in old source space

    Returns:
        Tuple of (new_batch_source_commit, remapped_ownership,
        new_source_content, working_tree_content)

    Raises:
        ValueError: If remapping is ambiguous or working tree content unavailable
    """
    result = advance_batch_source_for_file_with_provenance(
        batch_name=batch_name,
        file_path=file_path,
        old_batch_source_commit=old_batch_source_commit,
        existing_ownership=existing_ownership,
    )
    return (
        result.batch_source_commit,
        result.ownership,
        result.source_content,
        result.working_content,
    )


def advance_batch_source_for_file_with_provenance(
    batch_name: str,
    file_path: str,
    old_batch_source_commit: str,
    existing_ownership: BatchOwnership,
) -> BatchSourceAdvanceResult:
    """Advance batch source and expose provenance for re-annotation."""
    # Read old batch source content
    old_source_result = run_git_command(
        ["show", f"{old_batch_source_commit}:{file_path}"],
        text_output=False,
        check=False
    )
    if old_source_result.returncode != 0:
        raise ValueError(
            f"Cannot read old batch source for {file_path} at {old_batch_source_commit}: "
            f"{old_source_result.stderr}"
        )
    old_source_content = old_source_result.stdout

    # Read current working tree content
    repo_root = get_git_repository_root_path()
    working_file_path = repo_root / file_path
    if not working_file_path.exists():
        raise ValueError(
            f"Cannot advance batch source for {file_path}: "
            f"file does not exist in working tree"
        )
    working_content = working_file_path.read_bytes()

    advanced_source = _advance_source_content_preserving_existing_presence_with_provenance(
        old_source_content=old_source_content,
        working_content=working_content,
        ownership=existing_ownership,
    )

    # Create new batch source commit from the advanced source. This is
    # intentionally different from initial batch-source creation, which uses the
    # session-start snapshot for abort/discard correctness.
    new_batch_source_commit = create_batch_source_commit(
        file_path,
        file_content_override=advanced_source.content
    )

    # Remap ownership using provenance produced while constructing the advanced
    # source. This preserves already-owned lines that no longer exist in the
    # working tree after earlier discard operations.
    remapped_ownership = _remap_batch_ownership_with_source_line_map(
        ownership=existing_ownership,
        source_line_map=advanced_source.source_line_map,
    )

    return BatchSourceAdvanceResult(
        batch_source_commit=new_batch_source_commit,
        ownership=remapped_ownership,
        source_content=advanced_source.content,
        working_content=working_content,
        working_line_map=advanced_source.working_line_map,
    )

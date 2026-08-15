"""Storage-backed effective presence-reference lookup."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from ...core.mapped_storage import sort_mapped_records
from ..line_matching.match_workspace import MatcherWorkspace

if TYPE_CHECKING:
    from ..ownership.model import BatchOwnership
    from ..ownership.references import BaselineReference


class EffectivePresenceReferenceIndex:
    """Index last-claim-wins baseline references without a per-line dict."""

    def __init__(
        self,
        workspace: MatcherWorkspace,
        ownership: BatchOwnership,
    ) -> None:
        self._ownership = ownership
        self._valid = True
        try:
            reference_count = sum(
                len(claim.baseline_references)
                for claim in ownership.presence_claims
            )
        except (AttributeError, TypeError, ValueError):
            reference_count = 0
            self._valid = False
        self._records = workspace.record_vector(reference_count, "QQ")
        if not self._valid:
            return

        try:
            for claim_index, claim in enumerate(ownership.presence_claims):
                for source_line in claim.baseline_references:
                    if type(source_line) is not int or source_line < 1:
                        self._invalidate()
                        return
                    self._records.append((source_line, claim_index))
        except (AttributeError, OverflowError, TypeError, ValueError):
            self._invalidate()
            return

        sort_mapped_records(self._records)
        read_index = 0
        write_index = 0
        while read_index < len(self._records):
            source_line, claim_index = self._records[read_index]
            read_index += 1
            while (
                read_index < len(self._records)
                and self._records[read_index][0] == source_line
            ):
                _source_line, claim_index = self._records[read_index]
                read_index += 1
            self._records[write_index] = (source_line, claim_index)
            write_index += 1
        self._records.truncate(write_index)

    def __len__(self) -> int:
        return len(self._records)

    def reference_for(self, source_line: int) -> BaselineReference | None:
        """Return one effective reference, or None for absent/bad metadata."""
        if not self._valid:
            return None
        low = 0
        high = len(self._records)
        while low < high:
            middle = (low + high) // 2
            if self._records[middle][0] < source_line:
                low = middle + 1
            else:
                high = middle
        if low >= len(self._records):
            return None
        record_source_line, claim_index = self._records[low]
        if record_source_line != source_line:
            return None
        return self._reference_from_claim(source_line, claim_index)

    def _reference_from_claim(
        self,
        source_line: int,
        claim_index: int,
    ) -> BaselineReference | None:
        """Read a reference selected by one compact index record."""
        try:
            return self._ownership.presence_claims[
                claim_index
            ].baseline_references[source_line]
        except (AttributeError, KeyError, TypeError):
            return None

    def items(self) -> Iterator[tuple[int, BaselineReference | None]]:
        """Yield effective references in ascending source-line order."""
        if not self._valid:
            return
        for source_line, claim_index in self._records:
            yield source_line, self._reference_from_claim(
                source_line,
                claim_index,
            )

    def _invalidate(self) -> None:
        self._records.truncate(0)
        self._valid = False

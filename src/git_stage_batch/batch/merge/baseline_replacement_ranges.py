"""Storage-backed source ranges for baseline replacement units."""

from __future__ import annotations

from collections.abc import Sequence

def replacement_source_range_capacity(
    range_specs: Sequence[str],
) -> int:
    """Return an upper bound for records parsed from range specifications."""
    return sum(range_spec.count(",") + 1 for range_spec in range_specs)

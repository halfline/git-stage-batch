"""Shared deterministic cleanup for explicitly owned resources."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from typing import Iterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class CloseableResource(Protocol):
    """Resource with explicit lifetime management."""

    def close(self) -> None: ...


def close_resources_preserving_first(
    resources: Iterable[CloseableResource | None],
    *,
    suppress_errors: bool = False,
) -> None:
    """Close every resource, then optionally re-raise the first failure."""
    first_error: BaseException | None = None
    for resource in resources:
        if resource is None:
            continue
        try:
            resource.close()
        except BaseException as error:
            if first_error is None:
                first_error = error
    if first_error is not None and not suppress_errors:
        raise first_error


@contextmanager
def close_resources_on_exit(
    resources: Iterable[CloseableResource | None],
) -> Iterator[None]:
    """Close resources on scope exit without consulting ambient exception state."""
    try:
        yield
    except BaseException:
        close_resources_preserving_first(resources, suppress_errors=True)
        raise
    else:
        close_resources_preserving_first(resources)

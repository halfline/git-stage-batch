"""Tests for shared deterministic resource cleanup."""

import pytest

from git_stage_batch.core.resource_cleanup import (
    close_resources_on_exit,
    close_resources_preserving_first,
)


class _Closeable:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        if self.error is not None:
            raise self.error


def test_close_resources_closes_all_and_preserves_first_failure() -> None:
    """A failed close must not strand later owned resources."""
    first = _Closeable(KeyboardInterrupt("first"))
    later = _Closeable()

    with pytest.raises(KeyboardInterrupt, match="first"):
        close_resources_preserving_first((first, later))

    assert first.close_count == 1
    assert later.close_count == 1


def test_close_resources_can_preserve_an_error_already_in_flight() -> None:
    """Cleanup cancellation may be suppressed when a primary failure exists."""
    cancelled = _Closeable(KeyboardInterrupt("cleanup"))
    later = _Closeable()

    close_resources_preserving_first(
        (cancelled, later),
        suppress_errors=True,
    )

    assert cancelled.close_count == 1
    assert later.close_count == 1


def test_close_scope_does_not_mistake_an_outer_handler_for_a_body_error() -> None:
    """A successful local scope must still report its own cleanup failure."""
    failed_close = _Closeable(KeyboardInterrupt("cleanup"))

    try:
        raise RuntimeError("outer")
    except RuntimeError:
        with pytest.raises(KeyboardInterrupt, match="cleanup"):
            with close_resources_on_exit((failed_close,)):
                pass

    assert failed_close.close_count == 1


def test_close_scope_preserves_body_error_and_closes_all_resources() -> None:
    """The body failure wins while every resource still gets a close attempt."""
    failed_close = _Closeable(KeyboardInterrupt("cleanup"))
    later = _Closeable()

    with pytest.raises(RuntimeError, match="body"):
        with close_resources_on_exit((failed_close, later)):
            raise RuntimeError("body")

    assert failed_close.close_count == 1
    assert later.close_count == 1

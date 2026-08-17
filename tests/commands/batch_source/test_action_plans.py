"""Tests for batch-source action plan records."""

import pytest

import git_stage_batch.commands.batch_source.action_plans as action_plans


class _CloseCountingBuffer:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.close_count = 0
        self.error = error

    def close(self) -> None:
        self.close_count += 1
        if self.error is not None:
            raise self.error


def test_apply_text_file_action_plan_closes_buffer():
    """Apply text plans should close held merged content."""
    buffer = _CloseCountingBuffer()
    plan = action_plans.ApplyTextFileActionPlan(
        "notes.txt",
        buffer,
        "100644",
        "modified",
    )

    plan.close()

    assert buffer.close_count == 1


def test_apply_text_file_action_plan_allows_missing_buffer():
    """Apply text deletion plans should allow absent content buffers."""
    plan = action_plans.ApplyTextFileActionPlan(
        "notes.txt",
        None,
        None,
        "deleted",
    )

    plan.close()


def test_include_text_file_action_plan_closes_distinct_buffers():
    """Include text plans should close distinct index and worktree content."""
    index_buffer = _CloseCountingBuffer()
    working_buffer = _CloseCountingBuffer()
    plan = action_plans.IncludeTextFileActionPlan(
        "notes.txt",
        index_buffer,
        working_buffer,
        "100644",
        "100644",
        "modified",
        "modified",
    )

    plan.close()

    assert index_buffer.close_count == 1
    assert working_buffer.close_count == 1


def test_include_text_file_action_plan_closes_shared_buffer_once():
    """Include text plans should avoid closing shared content twice."""
    buffer = _CloseCountingBuffer()
    plan = action_plans.IncludeTextFileActionPlan(
        "notes.txt",
        buffer,
        buffer,
        "100644",
        "100644",
        "modified",
        "modified",
    )

    plan.close()

    assert buffer.close_count == 1


def test_include_text_file_action_plan_closes_both_buffers_after_failure():
    """A failing index close must not leak the worktree buffer."""
    index_buffer = _CloseCountingBuffer(error=KeyboardInterrupt())
    working_buffer = _CloseCountingBuffer()
    plan = action_plans.IncludeTextFileActionPlan(
        "notes.txt",
        index_buffer,
        working_buffer,
        "100644",
        "100644",
        "modified",
        "modified",
    )

    try:
        plan.close()
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected close failure")

    assert index_buffer.close_count == 1
    assert working_buffer.close_count == 1


def test_discard_text_file_action_plan_closes_buffer():
    """Discard text plans should close held worktree content."""
    buffer = _CloseCountingBuffer()
    plan = action_plans.DiscardTextFileActionPlan(
        "notes.txt",
        buffer,
        "100644",
        "modified",
    )

    plan.close()

    assert buffer.close_count == 1


def test_discard_text_file_action_plan_allows_missing_buffer():
    """Discard text deletion plans should allow absent content buffers."""
    plan = action_plans.DiscardTextFileActionPlan(
        "notes.txt",
        None,
        None,
        "deleted",
    )

    plan.close()


def test_close_action_plans_closes_later_plans_after_failure():
    """Aggregate cleanup must close every plan after an earlier failure."""
    first_buffer = _CloseCountingBuffer(error=RuntimeError("close failed"))
    second_buffer = _CloseCountingBuffer()
    plans = [
        action_plans.ApplyTextFileActionPlan(
            "first.txt", first_buffer, "100644", "modified"
        ),
        action_plans.ApplyTextFileActionPlan(
            "second.txt", second_buffer, "100644", "modified"
        ),
    ]

    try:
        action_plans.close_action_plans(plans)
    except RuntimeError as error:
        assert str(error) == "close failed"
    else:
        raise AssertionError("expected close failure")

    assert first_buffer.close_count == 1
    assert second_buffer.close_count == 1


def test_resource_cleanup_callback_closes_once_before_context_exit():
    """Publishers may move successful teardown inside their transaction."""
    buffer = _CloseCountingBuffer()

    with action_plans.resource_cleanup((buffer,)) as close_resources:
        close_resources()

    assert buffer.close_count == 1


def test_resource_cleanup_preserves_primary_error_when_close_is_cancelled():
    """A cleanup cancellation must not replace the publication failure."""
    buffer = _CloseCountingBuffer(error=KeyboardInterrupt("close cancelled"))

    with pytest.raises(RuntimeError, match="publication failed"):
        with action_plans.resource_cleanup((buffer,)):
            raise RuntimeError("publication failed")

    assert buffer.close_count == 1

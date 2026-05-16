import pytest

from git_stage_batch.batch.source_selector import (
    BatchSourceSelector,
    parse_batch_source_selector,
)
from git_stage_batch.exceptions import CommandError


def test_parse_plain_batch_selector():
    assert parse_batch_source_selector("cleanup") == BatchSourceSelector("cleanup")


@pytest.mark.parametrize(
    ("raw", "operation", "ordinal"),
    [
        ("cleanup:apply", "apply", None),
        ("cleanup:apply:2", "apply", 2),
        ("cleanup:include", "include", None),
        ("cleanup:include:2", "include", 2),
    ],
)
def test_parse_candidate_selectors(raw, operation, ordinal):
    assert parse_batch_source_selector(raw) == BatchSourceSelector(
        "cleanup",
        operation,
        ordinal,
    )


@pytest.mark.parametrize(
    "raw",
    [
        "cleanup:0",
        "cleanup:1",
        "cleanup:apply:0",
        "cleanup:include:0",
        "cleanup:apply:-1",
        "cleanup:include:foo",
        "cleanup::apply:1",
        "cleanup:1:apply",
    ],
)
def test_reject_invalid_candidate_selectors(raw):
    with pytest.raises(CommandError):
        parse_batch_source_selector(raw)

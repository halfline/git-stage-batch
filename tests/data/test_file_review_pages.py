"""Tests for file-review page selection helpers."""

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.data.file_review.pages import (
    normalize_page_spec,
    parse_page_selection,
)


def test_parse_page_selection_accepts_all_pages():
    pages = parse_page_selection("all", 3, "file.txt")

    assert pages == (1, 2, 3)


def test_parse_page_selection_normalizes_ranges_and_duplicates():
    pages = parse_page_selection("3,2,2", 4, "file.txt")

    assert pages == (2, 3)


@pytest.mark.parametrize(
    ("page_spec", "message"),
    (
        ("99", "Available pages: 1-3"),
        ("all,3", "cannot be combined"),
        ("1-all", "Invalid page selection"),
        ("", "empty"),
        ("1,,2", "empty"),
    ),
)
def test_parse_page_selection_rejects_invalid_specs(page_spec, message):
    with pytest.raises(CommandError, match=message):
        parse_page_selection(page_spec, 3, "file.txt")


def test_normalize_page_spec_returns_all_for_complete_selection():
    page_spec = normalize_page_spec((1, 2, 3), 3)

    assert page_spec == "all"


def test_normalize_page_spec_formats_partial_selection():
    page_spec = normalize_page_spec((2, 3), 4)

    assert page_spec == "2-3"

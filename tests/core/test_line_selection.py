"""Tests for line selection parsing."""

import pytest

from git_stage_batch.core.line_selection import (
    LineRanges,
    format_line_ids,
    parse_line_selection,
    parse_line_selection_ranges,
)


class TestParseLineSelection:
    """Tests for parse_line_selection function."""

    def test_single_ids(self):
        """Test parsing individual line IDs."""
        result = parse_line_selection("1,2,3")
        assert result == [1, 2, 3]

    def test_simple_range(self):
        """Test parsing a simple range."""
        result = parse_line_selection("5-7")
        assert result == [5, 6, 7]

    def test_mixed_ids_and_ranges(self):
        """Test parsing mixed individual IDs and ranges."""
        result = parse_line_selection("1,3,5-7")
        assert result == [1, 3, 5, 6, 7]

    def test_complex_mixed(self):
        """Test parsing complex mixed selection."""
        result = parse_line_selection("1-3,5,7-9,11")
        assert result == [1, 2, 3, 5, 7, 8, 9, 11]

    def test_whitespace_handling(self):
        """Test that whitespace is handled correctly."""
        result = parse_line_selection(" 1 , 3 , 5 - 7 ")
        assert result == [1, 3, 5, 6, 7]

    def test_duplicate_ids(self):
        """Test that duplicate IDs are deduplicated."""
        result = parse_line_selection("1,2,1,3,2")
        assert result == [1, 2, 3]

    def test_overlapping_ranges(self):
        """Test that overlapping ranges are handled correctly."""
        result = parse_line_selection("1-5,3-7")
        assert result == [1, 2, 3, 4, 5, 6, 7]

    def test_single_element_range(self):
        """Test a range with start == end."""
        result = parse_line_selection("5-5")
        assert result == [5]

    def test_single_id(self):
        """Test parsing a single ID."""
        result = parse_line_selection("42")
        assert result == [42]

    def test_empty_string_raises_error(self):
        """Test that empty string raises ValueError."""
        with pytest.raises(ValueError, match="Selection string cannot be empty"):
            parse_line_selection("")

    def test_whitespace_only_raises_error(self):
        """Test that whitespace-only string raises ValueError."""
        with pytest.raises(ValueError, match="Selection string cannot be empty"):
            parse_line_selection("   ")

    def test_invalid_format_raises_error(self):
        """Test that invalid format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid line ID"):
            parse_line_selection("1,abc,3")

    def test_invalid_range_format_raises_error(self):
        """Test that invalid range format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid range"):
            parse_line_selection("1-2-3")

    def test_non_numeric_range_raises_error(self):
        """Test that non-numeric range raises ValueError."""
        with pytest.raises(ValueError, match="Invalid range"):
            parse_line_selection("a-b")

    def test_negative_id_raises_error(self):
        """Test that negative line ID raises ValueError."""
        with pytest.raises(ValueError, match="Line ID must be positive"):
            parse_line_selection("-5")

    def test_zero_id_raises_error(self):
        """Test that zero line ID raises ValueError."""
        with pytest.raises(ValueError, match="Line ID must be positive"):
            parse_line_selection("0")

    def test_negative_range_raises_error(self):
        """Test that range with negative number raises ValueError."""
        with pytest.raises(ValueError, match="Line IDs must be positive"):
            parse_line_selection("-5-7")

    def test_zero_in_range_raises_error(self):
        """Test that range with zero raises ValueError."""
        with pytest.raises(ValueError, match="Line IDs must be positive"):
            parse_line_selection("0-5")

    def test_reversed_range_raises_error(self):
        """Test that range with start > end raises ValueError."""
        with pytest.raises(ValueError, match="Range start must be <= end"):
            parse_line_selection("7-5")

    def test_large_range(self):
        """Test parsing a large range."""
        result = parse_line_selection("1-100")
        assert result == list(range(1, 101))
        assert len(result) == 100

    def test_mixed_with_trailing_comma(self):
        """Test that trailing comma doesn't cause issues."""
        result = parse_line_selection("1,2,3,")
        assert result == [1, 2, 3]


class TestFormatLineIds:
    """Tests for format_line_ids function."""

    def test_single_id(self):
        """Test formatting a single line ID."""
        assert format_line_ids([1]) == "1"

    def test_consecutive_ids_as_range(self):
        """Test that consecutive IDs are formatted as a range."""
        assert format_line_ids([1, 2, 3]) == "1-3"

    def test_non_consecutive_ids(self):
        """Test that non-consecutive IDs are comma-separated."""
        assert format_line_ids([1, 3, 5]) == "1,3,5"

    def test_mixed_ranges_and_singles(self):
        """Test mixed consecutive and non-consecutive IDs."""
        assert format_line_ids([1, 2, 3, 5, 7, 8, 9]) == "1-3,5,7-9"

    def test_empty_list(self):
        """Test formatting an empty list."""
        assert format_line_ids([]) == ""

    def test_accepts_strings(self):
        """Test that function accepts string IDs."""
        assert format_line_ids(["1", "2", "3"]) == "1-3"

    def test_sorts_unordered_ids(self):
        """Test that unordered IDs are sorted before formatting."""
        assert format_line_ids([3, 1, 2]) == "1-3"

    def test_handles_duplicates(self):
        """Test that duplicate IDs are handled correctly."""
        assert format_line_ids([1, 2, 2, 3]) == "1-3"

    def test_large_range(self):
        """Test formatting a large consecutive range."""
        assert format_line_ids(list(range(1, 101))) == "1-100"

    def test_complex_mixed(self):
        """Test complex mixed ranges and singles."""
        assert format_line_ids([1, 2, 5, 6, 7, 10, 15, 16]) == "1-2,5-7,10,15-16"


class TestLineRanges:
    """Tests for range-backed line selections."""

    def test_parse_selection_ranges_does_not_expand_ranges(self):
        selection = parse_line_selection_ranges("1-1000000,1000002")

        assert selection.ranges() == ((1, 1000000), (1000002, 1000002))
        assert len(selection) == 1000001
        assert 999999 in selection
        assert 1000001 not in selection

    def test_count_intersection_and_difference_use_ranges(self):
        selection = parse_line_selection_ranges("1-10,20-30")

        assert selection.count() == 21
        assert selection.count(5, 22) == 9
        assert selection.intersection(LineRanges.from_ranges([(8, 25)])).ranges() == (
            (8, 10),
            (20, 25),
        )
        assert selection.difference(LineRanges.from_ranges([(3, 8), (25, 40)])).ranges() == (
            (1, 2),
            (9, 10),
            (20, 24),
        )

    def test_formats_ranges_without_line_expansion(self):
        selection = LineRanges.from_ranges([(10, 12), (1, 3), (4, 5)])

        assert selection.ranges() == ((1, 5), (10, 12))
        assert selection.to_line_spec() == "1-5,10-12"
        assert selection.to_range_strings() == ["1-5,10-12"]

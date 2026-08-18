"""Tests for line selection parsing."""

import gc
import tracemalloc

import pytest

from git_stage_batch.core.line_selection import (
    LineRangeBuilder,
    LineRanges,
    coerce_line_ranges,
    format_line_ids,
    parse_line_selection,
    parse_line_selection_ranges,
    parse_positive_selection,
    scan_line_range_specs,
    sorted_line_ranges_contain,
)


_LINE_SCALE_HEAP_LIMIT = 64 * 1024


@pytest.mark.parametrize(
    ("line_number", "expected"),
    [(0, False), (1, True), (3, True), (4, False), (8, True), (10, False)],
)
def test_sorted_line_range_records_support_binary_membership(
    line_number,
    expected,
) -> None:
    """Mapped records may carry fields after their inclusive range."""
    ranges = ((1, 3, 100), (7, 8, 200))

    assert sorted_line_ranges_contain(ranges, line_number) is expected


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

    def test_range_builder_coalesces_ordered_additions(self):
        builder = LineRangeBuilder()
        builder.add_line(1)
        builder.add_range(2, 4)
        builder.add_range(8, 9)

        assert builder.finish().ranges() == ((1, 4), (8, 9))

    def test_range_builder_normalizes_out_of_order_ranges_at_finish(self):
        builder = LineRangeBuilder()
        builder.add_range(10, 12)
        builder.add_range(1, 10)

        assert builder.finish().ranges() == ((1, 12),)

    def test_range_builder_keeps_large_range_compact(self):
        builder = LineRangeBuilder()
        builder.add_range(1, 10_000_000)

        assert builder.finish().ranges() == ((1, 10_000_000),)

    def test_coerce_line_ranges_keeps_range_instance(self):
        selection = LineRanges.from_ranges([(1, 3)])

        assert coerce_line_ranges(selection) is selection

    def test_coerce_line_ranges_uses_selection_ranges_without_expansion(self):
        class RangeOnlySelection:
            def ranges(self):
                return ((1, 1_000_000),)

            def __iter__(self):
                raise AssertionError("range-backed selection should not expand")

        selection = coerce_line_ranges(RangeOnlySelection())

        assert selection.ranges() == ((1, 1_000_000),)

    def test_coerce_line_ranges_accepts_line_iterables(self):
        selection = coerce_line_ranges([3, 1, 2])

        assert selection.ranges() == ((1, 3),)

    def test_from_lines_coalesces_contiguous_input_without_line_scale_heap(self):
        """A long contiguous line stream should become one range as it is read."""
        line_count = 16_384

        gc.collect()
        tracemalloc.start()
        try:
            selection = LineRanges.from_lines(range(1, line_count + 1))
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert selection.ranges() == ((1, line_count),)
        assert peak_heap < _LINE_SCALE_HEAP_LIMIT

    def test_set_equality_does_not_duplicate_selected_lines_on_heap(self):
        """Comparing an existing set must not build a second line-sized set."""
        line_count = 16_384
        selected_lines = set(range(1, line_count + 1))
        selection = LineRanges.from_ranges(((1, line_count),))

        gc.collect()
        tracemalloc.start()
        try:
            assert selection == selected_lines
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert peak_heap < _LINE_SCALE_HEAP_LIMIT

    def test_parse_selection_ranges_does_not_expand_ranges(
        self,
        monkeypatch,
    ):
        original_from_ranges = LineRanges.from_ranges

        def require_range_stream(cls, ranges):
            assert not isinstance(ranges, (list, tuple))
            return original_from_ranges(ranges)

        monkeypatch.setattr(
            LineRanges,
            "from_ranges",
            classmethod(require_range_stream),
        )

        selection = parse_line_selection_ranges("1-1000000,1000002")

        assert selection.ranges() == ((1, 1000000), (1000002, 1000002))
        assert len(selection) == 1000001
        assert 999999 in selection
        assert 1000001 not in selection

    def test_scans_range_specs_without_reading_ahead(self):
        def specifications():
            yield "1-3,5"
            raise AssertionError("range scanning read the next specification")

        ranges = scan_line_range_specs(specifications())

        assert next(ranges) == (1, 3)
        assert next(ranges) == (5, 5)
        with pytest.raises(
            AssertionError,
            match="range scanning read the next specification",
        ):
            next(ranges)

    def test_scans_raw_ranges_without_normalizing_them(self):
        assert tuple(scan_line_range_specs(["5-7,1", 3])) == (
            (5, 7),
            (1, 1),
            (3, 3),
        )

    def test_from_specs_preserves_single_blank_rejection(self):
        with pytest.raises(ValueError, match="Selection string cannot be empty"):
            LineRanges.from_specs([" "])

        assert LineRanges.from_specs([]) == LineRanges.empty()
        assert LineRanges.from_specs(["", ""]) == LineRanges.empty()

    @pytest.mark.parametrize(
        ("specification", "message"),
        [
            ("0", "Line ID must be positive: 0"),
            ("0-2", "Line IDs must be positive: 0-2"),
            ("2-1", "Range start must be <= end: 2-1"),
        ],
    )
    def test_from_specs_preserves_validation_messages(
        self,
        specification,
        message,
    ):
        with pytest.raises(ValueError, match=message):
            LineRanges.from_specs([specification])

    def test_positive_selection_uses_explicit_plural_item_label(self):
        with pytest.raises(ValueError, match="Pages must be positive: 0-2"):
            parse_positive_selection(
                "0-2",
                item_name="Page",
                item_name_plural="Pages",
            )

    def test_from_specs_streams_into_range_scanner(self, monkeypatch):
        scanned_specs = []

        def scan_specs(specs):
            assert not isinstance(specs, (list, tuple))
            for specification in specs:
                scanned_specs.append(specification)
                line = int(specification)
                yield line, line

        monkeypatch.setattr(
            "git_stage_batch.core.line_selection.scan_line_range_specs",
            scan_specs,
        )

        selection = LineRanges.from_specs(specification for specification in ("3", "1"))

        assert scanned_specs == ["3", "1"]
        assert selection.ranges() == ((1, 1), (3, 3))

    def test_count_intersection_and_difference_use_ranges(self):
        selection = parse_line_selection_ranges("1-10,20-30")

        assert selection.count() == 21
        assert selection.count(5, 22) == 9
        assert selection.contains_range(2, 9)
        assert selection.contains_range(8, 20) is False
        assert selection.contains_range(31, 31) is False
        assert selection.contains_range(5, 4) is False
        assert selection.intersects_range(0, 1)
        assert selection.intersects_range(10, 20)
        assert selection.intersects_range(30, 31)
        assert selection.intersects_range(11, 19) is False
        assert selection.intersects_range(20, 19) is False
        assert selection.intersection_with_range(5, 22).ranges() == (
            (5, 10),
            (20, 22),
        )
        assert not selection.intersection_with_range(11, 19)
        assert not selection.intersection_with_range(20, 19)
        assert selection.intersection(LineRanges.from_ranges([(8, 25)])).ranges() == (
            (8, 10),
            (20, 25),
        )
        assert selection.difference(
            LineRanges.from_ranges([(3, 8), (25, 40)])
        ).ranges() == (
            (1, 2),
            (9, 10),
            (20, 24),
        )

    @pytest.mark.parametrize(
        ("line_number", "expected"),
        [
            (0, None),
            (1, 1),
            (2, 1),
            (4, 1),
            (5, 5),
            (8, 5),
            (9, 9),
        ],
    )
    def test_nearest_unselected_line_uses_range_boundaries(
        self,
        line_number,
        expected,
    ):
        selection = LineRanges.from_ranges([(2, 4), (6, 8)])

        assert selection.nearest_unselected_at_or_before(line_number) == expected

    def test_subset_check_uses_normalized_range_coverage(self):
        superset = LineRanges.from_ranges([(1, 10), (20, 30)])

        assert LineRanges.from_ranges([(2, 4), (22, 29)]).is_subset_of(superset)
        assert not LineRanges.from_ranges([(2, 4), (11, 11)]).is_subset_of(
            superset
        )
        assert LineRanges.empty().is_subset_of(superset)
        assert not LineRanges.from_ranges([(1, 1)]).is_subset_of(
            LineRanges.empty()
        )

    def test_formats_ranges_without_line_expansion(self):
        selection = LineRanges.from_ranges([(10, 12), (1, 3), (4, 5)])

        assert selection.ranges() == ((1, 5), (10, 12))
        assert selection.to_line_spec() == "1-5,10-12"
        assert selection.to_range_strings() == ["1-5,10-12"]

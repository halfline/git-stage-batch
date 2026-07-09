"""Tests for shared CLI file argument handling."""

import argparse

from git_stage_batch.cli.file_arguments import normalize_parsed_file_arguments


def test_normalize_parsed_file_arguments_flattens_files_groups():
    args = argparse.Namespace(
        file=None,
        file_patterns=[["*.py"], ["docs/*.md", "*.txt"]],
    )

    normalize_parsed_file_arguments(args)

    assert args.file_patterns == ["*.py", "docs/*.md", "*.txt"]


def test_normalize_parsed_file_arguments_preserves_absent_file_patterns():
    args = argparse.Namespace(file=None, file_patterns=None)

    normalize_parsed_file_arguments(args)

    assert args.file_patterns is None


def test_normalize_parsed_file_arguments_marks_pathless_file_argument():
    args = argparse.Namespace(file=[["src/parser.py"], []], file_patterns=None)

    normalize_parsed_file_arguments(args)

    assert args.file == ""


def test_normalize_parsed_file_arguments_collapses_single_file_value():
    args = argparse.Namespace(file=[["src/parser.py"]], file_patterns=None)

    normalize_parsed_file_arguments(args)

    assert args.file == "src/parser.py"


def test_normalize_parsed_file_arguments_keeps_multiple_file_values():
    args = argparse.Namespace(
        file=[["src/parser.py"], ["tests/test_parser.py"]],
        file_patterns=None,
    )

    normalize_parsed_file_arguments(args)

    assert args.file == ["src/parser.py", "tests/test_parser.py"]

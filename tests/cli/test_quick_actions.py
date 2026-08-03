"""Tests for CLI quick action expansion."""

import pytest

from git_stage_batch.cli.quick_actions import expand_quick_actions


@pytest.mark.parametrize(
    ("shortcut", "expanded"),
    [
        ("?", ["--help"]),
        ("if", ["include", "--file"]),
        ("il", ["include", "--line"]),
        ("sf", ["skip", "--file"]),
        ("sl", ["skip", "--line"]),
        ("df", ["discard", "--file"]),
        ("dl", ["discard", "--line"]),
    ],
)
def test_expand_quick_actions_expands_shortcuts(shortcut, expanded):
    assert expand_quick_actions([shortcut]) == expanded


def test_expand_quick_actions_preserves_regular_arguments():
    assert expand_quick_actions(["show", "--file", "src/parser.py"]) == [
        "show",
        "--file",
        "src/parser.py",
    ]


def test_expand_quick_actions_expands_tokens_in_place():
    assert expand_quick_actions(["-C", "repo", "il", "1-3"]) == [
        "-C",
        "repo",
        "include",
        "--line",
        "1-3",
    ]


def test_expand_quick_actions_preserves_shortcut_shaped_command_arguments():
    """Batch names and other values must not be interpreted as commands."""
    assert expand_quick_actions(["include", "--to", "if"]) == [
        "include",
        "--to",
        "if",
    ]


def test_expand_quick_actions_supports_attached_working_directory_option():
    assert expand_quick_actions(["-Crepo", "dl", "2"]) == [
        "-Crepo",
        "discard",
        "--line",
        "2",
    ]

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

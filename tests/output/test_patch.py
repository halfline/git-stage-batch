"""Tests for patch printing."""

from unittest.mock import patch

from git_stage_batch.core.models import GitlinkChange
from git_stage_batch.output.colors import Colors
from git_stage_batch.output.patch import print_gitlink_change


def test_print_gitlink_change_modified(capsys):
    """Test printing modified gitlink changes."""
    print_gitlink_change(
        GitlinkChange(
            old_path="sub",
            new_path="sub",
            old_oid="1111111111111111111111111111111111111111",
            new_oid="2222222222222222222222222222222222222222",
            change_type="modified",
        )
    )

    captured = capsys.readouterr()
    assert "sub :: Submodule pointer modified" in captured.out
    assert "old 111111111111" in captured.out
    assert "new 222222222222" in captured.out


def test_print_gitlink_change_modified_with_color(capsys):
    """Modified atomic changes use a defined terminal color."""
    with patch("git_stage_batch.output.patch.Colors.enabled", return_value=True):
        print_gitlink_change(
            GitlinkChange(
                old_path="sub",
                new_path="sub",
                old_oid="1" * 40,
                new_oid="2" * 40,
                change_type="modified",
            )
        )

    assert Colors.YELLOW in capsys.readouterr().out


def test_print_gitlink_change_added(capsys):
    """Test printing added gitlink changes."""
    print_gitlink_change(
        GitlinkChange(
            old_path="/dev/null",
            new_path="sub",
            old_oid=None,
            new_oid="2222222222222222222222222222222222222222",
            change_type="added",
        )
    )

    captured = capsys.readouterr()
    assert "sub :: Submodule added at 222222222222" in captured.out

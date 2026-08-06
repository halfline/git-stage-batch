"""Terminal-safety coverage for ordinary file action output."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from git_stage_batch.commands.block_file import command_block_file
from git_stage_batch.commands.discard import command_discard_file
from git_stage_batch.commands.file_scope.discard_file import discard_file_changes
from git_stage_batch.commands.file_scope.include_file import include_file_changes
from git_stage_batch.commands.include import command_include_file
from git_stage_batch.commands.skip import command_skip_file
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.unblock_file import command_unblock_file
from git_stage_batch.commands.undo import command_undo
from git_stage_batch.git_paths import display_path, terminal_safe_shell_quote


UNUSUAL_PATHS = [
    "line\nbreak.txt",
    "escape\x1b[31mred.txt",
    "bidi-\u202espoof.txt",
    " leading-and-trailing.txt ",
]
if os.name == "posix" and sys.platform != "darwin":
    UNUSUAL_PATHS.append(os.fsdecode(b"non-utf8-\xff.txt"))


@pytest.fixture
def action_repo(tmp_path, monkeypatch):
    """Create a repository with an initial commit."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "README").write_text("base\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-qm", "Initial"], check=True)
    return tmp_path


def _assert_safe_path_label(output: str, file_path: str) -> None:
    assert display_path(file_path) in output
    assert "\x1b" not in output
    assert "\u202e" not in output
    if "\n" in file_path or any(0xDC80 <= ord(char) <= 0xDCFF for char in file_path):
        assert file_path not in output


@pytest.mark.parametrize("file_path", UNUSUAL_PATHS)
def test_noop_file_action_messages_escape_paths(action_repo, capsys, file_path):
    """No-change messages use the same safe path labels as success output."""
    include_file_changes(file_path, _prepared_changes=())
    _assert_safe_path_label(capsys.readouterr().err, file_path)

    discard_file_changes(file_path, _prepared_changes=())
    _assert_safe_path_label(capsys.readouterr().err, file_path)


@pytest.mark.parametrize("file_path", UNUSUAL_PATHS)
def test_ordinary_file_actions_and_undo_escape_paths(
    action_repo,
    capsys,
    file_path,
):
    """File labels and persisted command descriptions stay on safe lines."""
    path = action_repo / file_path
    path.write_text("base\n")
    subprocess.run(
        ["git", "--literal-pathspecs", "add", "--", file_path],
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "Add unusual path"], check=True)
    path.write_text("changed\n")

    command_start(quiet=True, auto_advance=False)
    capsys.readouterr()

    command_include_file(file_path, advance=False, auto_advance=False)
    _assert_safe_path_label(capsys.readouterr().err, file_path)
    command_undo()
    undo_output = capsys.readouterr().err
    assert terminal_safe_shell_quote(file_path) in undo_output
    assert "\x1b" not in undo_output
    assert "\u202e" not in undo_output

    command_skip_file(file_path, advance=False, auto_advance=False)
    _assert_safe_path_label(capsys.readouterr().err, file_path)
    command_undo()
    undo_output = capsys.readouterr().err
    assert terminal_safe_shell_quote(file_path) in undo_output
    assert "\x1b" not in undo_output
    assert "\u202e" not in undo_output

    command_discard_file(file_path, auto_advance=False)
    discard_output = capsys.readouterr().err
    _assert_safe_path_label(discard_output, file_path)
    assert terminal_safe_shell_quote(file_path) in discard_output
    command_undo()
    undo_output = capsys.readouterr().err
    assert terminal_safe_shell_quote(file_path) in undo_output
    assert "\x1b" not in undo_output
    assert "\u202e" not in undo_output

    if "\n" not in file_path and "\r" not in file_path:
        command_block_file(file_path, local_only=True)
        _assert_safe_path_label(capsys.readouterr().err, file_path)
        command_unblock_file(file_path)
        _assert_safe_path_label(capsys.readouterr().err, file_path)

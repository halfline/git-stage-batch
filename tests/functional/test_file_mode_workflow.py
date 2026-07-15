"""End-to-end executable-mode action coverage."""

import os
import re
import stat
import subprocess

from git_stage_batch.batch.state.query import read_batch_metadata
from git_stage_batch.commands.sift import command_sift_batch

from .conftest import git_stage_batch


def _commit_script(repo, *, executable: bool = False):
    path = repo / "script.sh"
    path.write_text("#!/bin/sh\necho old\n")
    path.chmod(0o755 if executable else 0o644)
    subprocess.run(["git", "add", path.name], check=True, cwd=repo)
    subprocess.run(["git", "commit", "-m", "Add script"], check=True, cwd=repo)
    return path


def _index_mode(repo, path: str) -> str:
    return subprocess.run(
        ["git", "ls-files", "-s", "--", path],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.split()[0]


def test_include_mode_only_change(functional_repo):
    path = _commit_script(functional_repo)
    path.chmod(0o755)

    git_stage_batch("start")
    shown = git_stage_batch("show")
    assert "Executable bit added" in shown.stdout
    git_stage_batch("include")

    assert _index_mode(functional_repo, path.name) == "100755"


def test_discard_mode_only_change(functional_repo):
    path = _commit_script(functional_repo, executable=True)
    path.chmod(0o644)

    git_stage_batch("start")
    git_stage_batch("discard")

    assert os.access(path, os.X_OK)


def test_include_executable_bit_removal(functional_repo):
    path = _commit_script(functional_repo, executable=True)
    path.chmod(0o644)

    git_stage_batch("start")
    git_stage_batch("show")
    git_stage_batch("include")

    assert _index_mode(functional_repo, path.name) == "100644"


def test_content_is_presented_before_independent_mode_action(functional_repo):
    path = _commit_script(functional_repo)
    path.write_text("#!/bin/sh\necho new\n")
    path.chmod(0o755)

    started = git_stage_batch("start")
    line_ids = re.findall(r"\[#(\d+)\]", started.stdout)
    assert line_ids
    assert "Executable bit" not in started.stdout

    git_stage_batch("include", "--line", ",".join(line_ids))
    assert _index_mode(functional_repo, path.name) == "100644"

    shown = git_stage_batch("show")
    assert "Executable bit added" in shown.stdout
    git_stage_batch("include")
    assert _index_mode(functional_repo, path.name) == "100755"


def test_mode_change_round_trips_through_batch(functional_repo):
    path = _commit_script(functional_repo)
    git_stage_batch("new", "modes")
    path.chmod(0o755)

    git_stage_batch("start")
    git_stage_batch("show")
    git_stage_batch("include", "--to", "modes")

    path.chmod(0o644)
    shown = git_stage_batch("show", "--from", "modes", "--file", path.name)
    assert "Executable bit added" in shown.stdout
    git_stage_batch("include", "--from", "modes")
    assert os.access(path, os.X_OK)
    assert _index_mode(functional_repo, path.name) == "100755"

    git_stage_batch("discard", "--from", "modes", "--file", path.name)
    assert not os.access(path, os.X_OK)


def test_mode_batch_refuses_symlinked_worktree_parent(functional_repo):
    """Applying a mode batch must not chmod through a worktree parent symlink."""
    directory = functional_repo / "dir"
    directory.mkdir()
    path = directory / "tool.sh"
    path.write_text("#!/bin/sh\necho old\n")
    path.chmod(0o644)
    subprocess.run(
        ["git", "add", "dir/tool.sh"],
        check=True,
        cwd=functional_repo,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add nested tool"],
        check=True,
        cwd=functional_repo,
    )

    path.chmod(0o755)
    git_stage_batch("start")
    git_stage_batch("show", "--file", "dir/tool.sh")
    git_stage_batch("discard", "--to", "modes")
    assert not os.access(path, os.X_OK)

    outside_directory = functional_repo.parent / "outside"
    outside_directory.mkdir()
    outside_path = outside_directory / path.name
    outside_path.write_bytes(path.read_bytes())
    outside_path.chmod(0o644)
    directory.rename(functional_repo / "original-dir")
    os.symlink(outside_directory, directory, target_is_directory=True)

    result = git_stage_batch(
        "apply",
        "--from",
        "modes",
        "--file",
        "dir/tool.sh",
        check=False,
    )

    assert result.returncode != 0
    assert "Cannot safely apply Git file mode" in result.stderr
    assert stat.S_IMODE(outside_path.stat().st_mode) == 0o644


def test_mode_actions_support_skip_undo_redo_and_abort(functional_repo):
    path = _commit_script(functional_repo)
    path.chmod(0o755)

    git_stage_batch("start")
    git_stage_batch("show", "--file", path.name)
    git_stage_batch("include")
    assert _index_mode(functional_repo, path.name) == "100755"

    git_stage_batch("undo")
    assert _index_mode(functional_repo, path.name) == "100644"
    git_stage_batch("redo")
    assert _index_mode(functional_repo, path.name) == "100755"
    git_stage_batch("abort")
    assert _index_mode(functional_repo, path.name) == "100644"
    assert os.access(path, os.X_OK)

    git_stage_batch("start")
    git_stage_batch("show", "--file", path.name)
    git_stage_batch("skip")
    assert os.access(path, os.X_OK)


def test_core_file_mode_false_hides_mode_actions(functional_repo):
    path = _commit_script(functional_repo)
    subprocess.run(
        ["git", "config", "core.fileMode", "false"],
        check=True,
        cwd=functional_repo,
    )
    path.chmod(0o755)

    result = git_stage_batch("start", check=False)

    assert result.returncode != 0
    assert "No changes" in result.stderr or "No hunks" in result.stderr


def test_mode_action_refuses_line_selection(functional_repo):
    path = _commit_script(functional_repo)
    path.chmod(0o755)
    git_stage_batch("start")
    git_stage_batch("show")

    result = git_stage_batch("include", "--line", "1", check=False)

    assert result.returncode != 0
    assert "mode" in result.stderr.lower()


def test_mode_batch_reset_and_sift(functional_repo):
    path = _commit_script(functional_repo)
    path.chmod(0o755)
    git_stage_batch("start")
    git_stage_batch("show")
    git_stage_batch("include", "--to", "modes")

    assert read_batch_metadata("modes")["files"][path.name]["presence_claims"] == []
    git_stage_batch("reset", "--from", "modes", "--file", path.name)
    assert path.name not in read_batch_metadata("modes")["files"]

    git_stage_batch("again")
    git_stage_batch("show")
    git_stage_batch("include", "--to", "modes")
    command_sift_batch("modes", "sifted")
    assert read_batch_metadata("sifted")["files"] == {}

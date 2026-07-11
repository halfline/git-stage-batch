"""End-to-end executable-mode action coverage."""

import os
import re
import subprocess

from git_stage_batch.batch.query import read_batch_metadata
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

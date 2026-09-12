"""Index-backed batch baselines survive reuse and object pruning."""

import json
import subprocess

from .conftest import git_stage_batch


def _git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def test_parked_index_baseline_survives_garbage_collection(functional_repo):
    path = functional_repo / "sample.txt"
    path.write_text("old\nkeep\n")
    _git("add", ".")
    _git("commit", "-m", "Add baseline")
    path.write_text("staged\nkeep\n")
    _git("add", ".")
    path.write_text("unstaged\nkeep\n")
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("discard", "--to", "later", "--file", path.name)
    metadata = json.loads(_git("show", "refs/git-stage-batch/state/later:batch.json"))
    baseline = metadata["baseline"]
    git_stage_batch("stop")
    _git("reset", "--mixed", "HEAD")
    _git("write-tree")
    _git("reflog", "expire", "--expire=now", "--all")
    _git("gc", "--prune=now")

    assert _git("cat-file", "-t", baseline).strip() == "commit"
    index = _git("ls-files", "--stage")
    git_stage_batch("apply", "--from", "later", "--file", path.name)
    assert path.read_text() == "unstaged\nkeep\n"
    assert _git("ls-files", "--stage") == index

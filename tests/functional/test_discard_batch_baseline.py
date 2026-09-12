"""Index-backed batch baselines survive reuse and object pruning."""

import json
import subprocess

import pytest

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


@pytest.mark.parametrize("selector", ["--file", "--files"])
@pytest.mark.parametrize("same_file", [False, True], ids=["other-file", "same-file"])
def test_existing_batch_keeps_prior_claims_when_parking_staged_replacement(
    functional_repo,
    selector,
    same_file,
):
    path = functional_repo / "sample.txt"
    previous = path if same_file else functional_repo / "previous.txt"
    unrelated = functional_repo / "unrelated.txt"
    context = "".join(f"context {number}\n" for number in range(12))
    baseline = "old\n" + context + "base tail\n"
    path.write_text(baseline)
    previous.write_text(baseline)
    unrelated.write_text("unrelated baseline\n")
    _git("add", ".")
    _git("commit", "-m", "Add baselines")
    previous.write_text(baseline.replace("base tail", "saved tail"))
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("discard", "--to", "later", "--file", previous.name)
    git_stage_batch("stop")
    indexed = "staged\n" + context + "base tail\n"
    path.write_text(indexed)
    _git("add", "--", path.name)
    unrelated.write_text("unrelated staged content\n")
    _git("add", "--", unrelated.name)
    final = "unstaged\n" + context + "base tail\n"
    path.write_text(final)
    git_stage_batch("start", "--no-auto-advance")
    index = _git("ls-files", "--stage")

    git_stage_batch("discard", "--to", "later", selector, path.name)

    assert path.read_text() == indexed
    assert _git("ls-files", "--stage") == index
    metadata = json.loads(_git("show", "refs/git-stage-batch/state/later:batch.json"))
    assert (
        _git("show", f"{metadata['baseline']}:{unrelated.name}")
        == "unrelated baseline\n"
    )
    git_stage_batch("stop")
    _git("reset", "--mixed", "HEAD")
    _git("write-tree")
    _git("reflog", "expire", "--expire=now", "--all")
    _git("gc", "--prune=now")
    index = _git("ls-files", "--stage")
    git_stage_batch("apply", "--from", "later", "--files", "**")
    assert path.read_text() == (
        final.replace("base tail", "saved tail") if same_file else final
    )
    if not same_file:
        assert previous.read_text() == baseline.replace("base tail", "saved tail")
    assert unrelated.read_text() == "unrelated staged content\n"
    assert _git("ls-files", "--stage") == index

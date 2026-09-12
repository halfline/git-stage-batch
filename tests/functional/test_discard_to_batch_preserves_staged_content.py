"""Parking a file's unstaged edits must preserve its staged edits."""

import re
import subprocess

import pytest

from .conftest import git_stage_batch


def _git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(
    params=[("--file", "sample.txt"), ("--files", "sample.txt"), ("--files", "**")],
    ids=["file", "files", "glob"],
)
def parked_file(functional_repo, request):
    path = functional_repo / "sample.txt"
    baseline = "".join(f"context {number}\n" for number in range(20))
    indexed = "staged addition\n" + baseline
    final = indexed + "unstaged addition\n"
    path.write_text(baseline)
    _git("add", "--", path.name)
    _git("commit", "-m", "Add file with separated edit locations")
    head = _git("rev-parse", "HEAD")
    path.write_text(final)
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("show", "--file", path.name)
    git_stage_batch("include", "--line", "1")
    assert _git("show", f":{path.name}") == indexed
    assert path.read_text() == final
    index_before = _git("ls-files", "--stage")

    git_stage_batch("discard", "--to", "later", *request.param)

    assert _git("rev-parse", "HEAD") == head
    assert _git("ls-files", "--stage") == index_before
    return path, indexed, final


@pytest.mark.parametrize("selector", ["--file", "--files"])
@pytest.mark.parametrize(
    "baseline,indexed,final",
    [
        ("old\nkeep\n", "staged\nkeep\n", "unstaged\nkeep\n"),
        ("old\nkeep\n", "staged\nkeep\n", "old\nkeep\n"),
        ("", "staged\n", "unstaged\n"),
    ],
    ids=["replace-staged", "reverse-staged", "replace-added-content"],
)
def test_discard_to_batch_replays_replacement_of_staged_text(
    functional_repo, selector, baseline, indexed, final,
):
    path = functional_repo / "replacement.txt"
    path.write_text(baseline)
    _git("add", "--", path.name)
    _git("commit", "-m", "Add replacement baseline")
    path.write_text(indexed)
    _git("add", "--", path.name)
    path.write_text(final)
    git_stage_batch("start", "--no-auto-advance")
    index_before = _git("ls-files", "--stage")

    git_stage_batch("discard", "--to", "later", selector, path.name)

    assert path.read_text() == indexed
    assert _git("ls-files", "--stage") == index_before
    git_stage_batch("apply", "--from", "later", "--file", path.name)
    assert path.read_text() == final
    assert _git("ls-files", "--stage") == index_before


def test_discard_to_batch_leaves_worktree_matching_index(parked_file):
    path, indexed, final = parked_file
    assert path.read_text() == indexed
    assert _git("diff", "--", path.name) == ""

    git_stage_batch("apply", "--from", "later", "--file", path.name)

    assert path.read_text() == final
    assert _git("show", f":{path.name}") == indexed


def test_discard_to_batch_saves_only_unstaged_addition(parked_file):
    path, _indexed, _final = parked_file
    saved = git_stage_batch(
        "show", "--from", "later", "--file", path.name, "--page", "all"
    ).stdout
    additions = re.findall(r"^\[#\d+\] \+ (.*)$", saved, re.MULTILINE)
    assert additions == ["unstaged addition"], saved


@pytest.mark.parametrize("selector", ["--file", "--files"])
@pytest.mark.parametrize("indexed", ["", "staged content\n"])
def test_discard_to_batch_preserves_staged_new_file(functional_repo, selector, indexed):
    path = functional_repo / "new.txt"
    path.write_text(indexed)
    _git("add", "--", path.name)
    final = indexed + "unstaged addition\n"
    path.write_text(final)
    git_stage_batch("start", "--no-auto-advance")
    index_before = _git("ls-files", "--stage")

    git_stage_batch("discard", "--to", "later", selector, path.name)

    assert path.read_text() == indexed
    assert _git("ls-files", "--stage") == index_before
    assert _git("diff", "--", path.name) == ""

    git_stage_batch("apply", "--from", "later", "--file", path.name)

    assert path.read_text() == final
    assert _git("ls-files", "--stage") == index_before

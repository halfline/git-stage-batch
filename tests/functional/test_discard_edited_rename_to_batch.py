"""Saving a renamed text file must retain its edits and original pathname."""

import json
import subprocess

import pytest

from .conftest import git_stage_batch


def _git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def _persistent_refs():
    return _git(
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        "refs/heads/",
        "refs/tags/",
        "refs/git-stage-batch/batches/",
        "refs/git-stage-batch/state/",
        "refs/batches/",
    )


def _start_rename(functional_repo, edited, *, crlf=False):
    old = functional_repo / "scope_test.c"
    new = functional_repo / "outputs_test.c"
    baseline = """static void test_scope(void)
{
    assert(create(33) == E2BIG);
    assert(validate(33) == E2BIG);
    assert(create(0) == 0);
    assert(validate(0) == 0);
    assert(create(1) == 0);
    assert(validate(1) == 0);
    assert(create(2) == 0);
    assert(validate(2) == 0);
}
"""
    if crlf:
        _git("config", "core.autocrlf", "true")
        baseline = baseline.replace("\n", "\r\n")
    old.write_bytes(baseline.encode())
    subprocess.run(["git", "add", old.name], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add output-list checks"],
        check=True,
        capture_output=True,
    )
    old.rename(new)
    target = baseline.replace("scope", "outputs") if edited else baseline
    new.write_bytes(target.encode())
    git_stage_batch("start", "--no-auto-advance")
    changes = _git("diff", "HEAD", "--name-status", "--find-renames").splitlines()
    assert len(changes) == 1
    assert changes[0].split("\t")[1:] == [old.name, new.name]
    assert changes[0].startswith("R")
    return old, new, baseline, target


@pytest.mark.parametrize("edited", [False, True], ids=["rename", "edited-rename"])
@pytest.mark.parametrize(
    "paths",
    [
        ("**",),
        ("scope_test.c", "outputs_test.c"),
        ("outputs_test.c", "scope_test.c"),
    ],
    ids=["glob", "source-first", "destination-first"],
)
def test_discard_rename_to_batch_round_trip(functional_repo, edited, paths):
    old, new, baseline, target = _start_rename(functional_repo, edited)
    head = _git("rev-parse", "HEAD")
    refs = _persistent_refs()
    status = _git("status", "--porcelain")

    result = git_stage_batch(
        "discard", "--to", "output-rename", "--files", *paths, check=False
    )
    if result.returncode:
        assert not old.exists()
        assert new.read_text() == target
        assert _git("status", "--porcelain") == status
        assert _persistent_refs() == refs
    assert _git("rev-parse", "HEAD") == head
    assert _git("diff", "--cached", "--exit-code") == ""
    assert result.returncode == 0, result.stdout + result.stderr
    assert old.read_text() == baseline
    assert not new.exists()

    git_stage_batch("apply", "--from", "output-rename")
    assert not old.exists()
    assert new.read_text() == target
    assert _git("diff", "--cached", "--exit-code") == ""


@pytest.mark.parametrize("edited", [False, True], ids=["rename", "edited-rename"])
def test_apply_saved_rename_preserves_later_source_edit(functional_repo, edited):
    old, new, baseline, target = _start_rename(functional_repo, edited)
    git_stage_batch("discard", "--to", "output-rename", "--files", "**")
    later_edit = "// A later independent edit must follow the rename.\n"
    old.write_text(baseline + later_edit)

    git_stage_batch("apply", "--from", "output-rename")

    assert not old.exists()
    assert new.read_text() == target + later_edit
    assert _git("diff", "--cached", "--exit-code") == ""

    git_stage_batch("undo")
    assert old.read_text() == baseline + later_edit
    assert not new.exists()
    git_stage_batch("redo")
    assert not old.exists()
    assert new.read_text() == target + later_edit

    new.write_text(target + later_edit + "// Another destination edit.\n")
    git_stage_batch("apply", "--from", "output-rename")
    assert new.read_text() == target + later_edit + "// Another destination edit.\n"
def test_saved_rename_retains_path_and_content_provenance(functional_repo):
    old, new, baseline, target = _start_rename(functional_repo, True)
    git_stage_batch("discard", "--to", "output-rename", "--files", "**")

    state = "refs/git-stage-batch/state/output-rename"
    metadata = json.loads(_git("show", f"{state}:batch.json"))
    source = metadata["files"][old.name]
    destination = metadata["files"][new.name]
    assert source["rename_to"] == new.name
    assert destination["rename_from"] == old.name
    assert (
        _git("show", f"{state}:objects/{destination['rename_base_blob']}") == baseline
    )
    assert (
        _git("show", f"{state}:objects/{destination['rename_target_blob']}") == target
    )

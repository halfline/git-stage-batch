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


@pytest.mark.parametrize("operation", ["apply", "include"])
@pytest.mark.parametrize("edited", [False, True], ids=["rename", "edited-rename"])
@pytest.mark.parametrize("later_edit", [False, True], ids=["unchanged", "later-edit"])
def test_replay_saved_crlf_rename(functional_repo, operation, edited, later_edit):
    old, new, baseline, target = _start_rename(functional_repo, edited, crlf=True)
    git_stage_batch("discard", "--to", "output-rename", "--files", "**")
    assert old.read_bytes() == baseline.encode()
    staged = "// A later staged edit.\r\n" if later_edit else ""
    unstaged = "// A later unstaged edit.\r\n" if later_edit else ""
    old.write_bytes((baseline + staged).encode())
    _git("add", "--", old.name)
    old.write_bytes((baseline + staged + unstaged).encode())
    index_before = _git("ls-files", "--stage")

    git_stage_batch(operation, "--from", "output-rename")

    assert not old.exists()
    assert new.read_bytes() == (target + staged + unstaged).encode()
    if operation == "include":
        indexed = subprocess.check_output(["git", "show", f":{new.name}"])
        assert indexed == (target + staged).replace("\r\n", "\n").encode()
        assert _git("ls-files", "--", old.name) == ""
    else:
        assert _git("ls-files", "--stage") == index_before


@pytest.mark.parametrize("edited", [False, True], ids=["rename", "edited-rename"])
@pytest.mark.parametrize("later_edit", [False, True], ids=["unchanged", "later-edit"])
def test_discard_saved_crlf_rename_preserves_line_endings(
    functional_repo, edited, later_edit
):
    old, new, baseline, target = _start_rename(functional_repo, edited, crlf=True)
    git_stage_batch("discard", "--to", "output-rename", "--files", "**")
    suffix = "// A later independent edit.\r\n" if later_edit else ""
    old.rename(new)
    new.write_bytes((target + suffix).encode())

    git_stage_batch("discard", "--from", "output-rename")

    assert not new.exists()
    assert old.read_bytes() == (baseline + suffix).encode()
    assert _git("diff", "--cached", "--exit-code") == ""


@pytest.mark.parametrize("operation", ["apply", "include", "discard"])
def test_saved_crlf_rename_conflict_preserves_contents(functional_repo, operation):
    old, new, baseline, target = _start_rename(functional_repo, True, crlf=True)
    git_stage_batch("discard", "--to", "output-rename", "--files", "**")
    if operation == "discard":
        old.rename(new)
        changed_path = new
        conflicting = target.replace("test_outputs", "test_later").encode()
    else:
        changed_path = old
        conflicting = baseline.replace("test_scope", "test_later").encode()
    changed_path.write_bytes(conflicting)
    refs = _persistent_refs()
    index = _git("ls-files", "--stage")

    result = git_stage_batch(operation, "--from", "output-rename", check=False)

    assert result.returncode != 0
    assert "conflict" in result.stderr
    assert changed_path.read_bytes() == conflicting
    assert old.exists() != new.exists()
    assert _git("ls-files", "--stage") == index
    assert _persistent_refs() == refs


@pytest.mark.parametrize("operation", ["apply", "include", "discard"])
def test_saved_binary_rename_preserves_crlf_bytes(functional_repo, operation):
    old = functional_repo / "old.bin"
    new = functional_repo / "new.bin"
    _git("config", "core.autocrlf", "true")
    baseline = b"\x00binary\r\n" * 100
    target = baseline + b"saved edit\r\n"
    old.write_bytes(baseline)
    _git("add", "--", old.name)
    _git("commit", "-m", "Add binary file")
    old.rename(new)
    new.write_bytes(target)
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("discard", "--to", "binary-rename", "--files", "**")
    metadata = json.loads(_git("show", "refs/git-stage-batch/state/binary-rename:batch.json"))
    assert metadata["files"][new.name]["rename_from"] == old.name
    if operation == "discard":
        old.rename(new)
        new.write_bytes(target)

    git_stage_batch(operation, "--from", "binary-rename")

    if operation == "discard":
        assert old.read_bytes() == baseline
        assert not new.exists()
    else:
        assert new.read_bytes() == target
        assert not old.exists()
        if operation == "include":
            assert subprocess.check_output(["git", "show", f":{new.name}"]) == target
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
@pytest.mark.parametrize("edited", [False, True], ids=["rename", "edited-rename"])
@pytest.mark.parametrize(
    "later_staged_edit", [False, True], ids=["index-unchanged", "index-edited"]
)
def test_include_saved_rename_leaves_later_source_edit_unstaged(
    functional_repo, edited, later_staged_edit
):
    old, new, baseline, target = _start_rename(functional_repo, edited)
    git_stage_batch("discard", "--to", "output-rename", "--files", "**")
    staged_edit = "// An independent staged edit.\n" if later_staged_edit else ""
    if staged_edit:
        old.write_text(baseline + staged_edit)
        _git("add", "--", old.name)
    later_edit = "// This edit is not part of the saved batch.\n"
    old.write_text(baseline + staged_edit + later_edit)

    git_stage_batch("include", "--from", "output-rename")

    assert not old.exists()
    assert new.read_text() == target + staged_edit + later_edit
    assert _git("show", f":{new.name}") == target + staged_edit
    assert _git("ls-files", "--", old.name) == ""
@pytest.mark.parametrize("edited", [False, True], ids=["rename", "edited-rename"])
def test_discard_saved_rename_preserves_later_destination_edit(functional_repo, edited):
    old, new, baseline, target = _start_rename(functional_repo, edited)
    git_stage_batch("discard", "--to", "output-rename", "--files", "**")
    git_stage_batch("apply", "--from", "output-rename")
    later_edit = "// This later edit must survive reversal.\n"
    new.write_text(target + later_edit)

    git_stage_batch("discard", "--from", "output-rename")

    assert not new.exists()
    assert old.read_text() == baseline + later_edit
    assert _git("diff", "--cached", "--exit-code") == ""
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

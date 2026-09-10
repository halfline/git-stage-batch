"""Whole-file deletions must validate the content their batch actually removed."""

import subprocess

import pytest

from .conftest import git_stage_batch


def _git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)


def _save_deletion(functional_repo, *, crlf=False, binary=False):
    path = functional_repo / ("deleted.bin" if binary else "deleted.txt")
    baseline = b"first line\nsecond line\nthird line\n"
    if binary:
        baseline = b"\x00" + baseline
    if crlf:
        _git("config", "core.autocrlf", "true")
        baseline = baseline.replace(b"\n", b"\r\n")
    path.write_bytes(baseline)
    _git("add", "--", path.name)
    _git("commit", "-m", "Add file to delete")
    path.unlink()
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("discard", "--to", "deleted", "--file", path.name)
    assert path.read_bytes() == baseline
    assert _git("diff", "--exit-code") == b""
    return path, baseline


@pytest.mark.parametrize("operation", ["apply", "include"])
def test_saved_deletion_accepts_unchanged_crlf_worktree(functional_repo, operation):
    path, baseline = _save_deletion(functional_repo, crlf=True)
    assert _git("show", f"HEAD:{path.name}") == baseline.replace(b"\r\n", b"\n")

    git_stage_batch(operation, "--from", "deleted")

    assert not path.exists()
    if operation == "include":
        assert _git("ls-files", "--", path.name) == b""
    else:
        assert _git("diff", "--cached", "--exit-code") == b""


@pytest.mark.parametrize("operation", ["apply", "include"])
@pytest.mark.parametrize("crlf", [False, True], ids=["lf", "crlf"])
@pytest.mark.parametrize("captured_edit", ["append", "replace", "remove"])
def test_sifted_deletion_accepts_recorded_preimage(
    functional_repo, operation, crlf, captured_edit
):
    path, baseline = _save_deletion(functional_repo, crlf=crlf)
    ending = b"\r\n" if crlf else b"\n"
    if captured_edit == "append":
        captured = baseline + b"An edit captured by sift." + ending
    elif captured_edit == "replace":
        captured = baseline.replace(b"second line", b"A replacement captured by sift.")
    else:
        captured = baseline.replace(b"second line" + ending, b"")
    path.write_bytes(captured)
    git_stage_batch("sift", "--from", "deleted", "--to", "rebuilt")
    assert path.read_bytes() == captured
    assert _git("diff", "--cached", "--exit-code") == b""

    git_stage_batch(operation, "--from", "rebuilt")

    assert not path.exists()
    if operation == "include":
        assert _git("ls-files", "--", path.name) == b""
    else:
        assert _git("diff", "--cached", "--exit-code") == b""
    git_stage_batch("undo")
    assert path.read_bytes() == captured
    assert _git("diff", "--cached", "--exit-code") == b""


def test_sifted_deletion_accepts_staged_subset_of_recorded_preimage(functional_repo):
    path, baseline = _save_deletion(functional_repo, crlf=True)
    staged = baseline + b"An edit staged before sift.\r\n"
    path.write_bytes(staged)
    _git("add", "--", path.name)
    captured = staged + b"An unstaged edit also captured by sift.\r\n"
    path.write_bytes(captured)
    git_stage_batch("sift", "--from", "deleted", "--to", "rebuilt")

    git_stage_batch("include", "--from", "rebuilt")

    assert not path.exists()
    assert _git("ls-files", "--", path.name) == b""
    git_stage_batch("undo")
    assert path.read_bytes() == captured
    assert _git("show", f":{path.name}") == staged.replace(b"\r\n", b"\n")


@pytest.mark.parametrize("operation", ["apply", "include"])
@pytest.mark.parametrize("sifted", [False, True], ids=["saved", "sifted"])
def test_saved_deletion_refuses_later_worktree_edit(functional_repo, operation, sifted):
    path, baseline = _save_deletion(functional_repo, crlf=True)
    batch = "deleted"
    if sifted:
        baseline += b"An edit captured by sift.\r\n"
        path.write_bytes(baseline)
        git_stage_batch("sift", "--from", batch, "--to", "rebuilt")
        batch = "rebuilt"
    later = baseline + b"An edit made after saving.\r\n"
    path.write_bytes(later)
    refs = _git("for-each-ref", "refs/git-stage-batch/")
    index = _git("ls-files", "--stage")

    result = git_stage_batch(operation, "--from", batch, check=False)

    assert result.returncode != 0
    assert "changed since the batch was saved" in result.stderr
    assert path.read_bytes() == later
    assert _git("for-each-ref", "refs/git-stage-batch/") == refs
    assert _git("ls-files", "--stage") == index


@pytest.mark.parametrize("sifted", [False, True], ids=["saved", "sifted"])
def test_saved_deletion_refuses_later_index_edit(functional_repo, sifted):
    path, baseline = _save_deletion(functional_repo, crlf=True)
    batch = "deleted"
    if sifted:
        baseline += b"An edit captured by sift.\r\n"
        path.write_bytes(baseline)
        git_stage_batch("sift", "--from", batch, "--to", "rebuilt")
        batch = "rebuilt"
    path.write_bytes(baseline + b"An independent staged edit.\r\n")
    _git("add", "--", path.name)
    path.write_bytes(baseline)
    refs = _git("for-each-ref", "refs/git-stage-batch/")
    index = _git("ls-files", "--stage")

    result = git_stage_batch("include", "--from", batch, check=False)

    assert result.returncode != 0
    assert "changed since the batch was saved" in result.stderr
    assert path.read_bytes() == baseline
    assert _git("for-each-ref", "refs/git-stage-batch/") == refs
    assert _git("ls-files", "--stage") == index

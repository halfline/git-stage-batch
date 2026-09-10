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

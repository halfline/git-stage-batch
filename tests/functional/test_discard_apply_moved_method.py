"""Saving a whole file must allow replaying a method move onto its baseline."""

from pathlib import Path
import subprocess

import pytest

from .conftest import git_stage_batch


FIXTURES = Path(__file__).parent / "fixtures"


def _git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.mark.parametrize("selector", ["--file", "--files"])
def test_discard_apply_restores_moved_method(functional_repo, selector):
    # Real source text exercises repeated braces and context around a method
    # moved between impl blocks. The fixtures are data, not compiled Rust.
    baseline = (FIXTURES / "castkms_display_move_baseline.rs").read_bytes()
    target = (FIXTURES / "castkms_display_move_target.rs").read_bytes()
    path = functional_repo / "display.rs"
    path.write_bytes(baseline)
    _git("add", "--", path.name)
    _git("commit", "-m", "Add method move fixture")
    head = _git("rev-parse", "HEAD")
    index = _git("ls-files", "--stage")
    path.write_bytes(target)
    git_stage_batch("start", "--no-auto-advance")

    git_stage_batch("discard", "--to", "later", selector, path.name)

    assert path.read_bytes() == baseline
    assert _git("diff", "--", path.name) == ""
    assert _git("ls-files", "--stage") == index
    result = git_stage_batch(
        "apply", "--from", "later", "--file", path.name, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert path.read_bytes() == target
    assert _git("rev-parse", "HEAD") == head
    assert _git("ls-files", "--stage") == index


def test_moved_method_replay_does_not_overwrite_later_worktree_edit(functional_repo):
    baseline = (FIXTURES / "castkms_display_move_baseline.rs").read_bytes()
    target = (FIXTURES / "castkms_display_move_target.rs").read_bytes()
    path = functional_repo / "display.rs"
    path.write_bytes(baseline)
    _git("add", "--", path.name)
    _git("commit", "-m", "Add method move fixture")
    path.write_bytes(target)
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("discard", "--to", "later", "--file", path.name)
    later_edit = b"// Later independent worktree edit\n"
    path.write_bytes(later_edit + baseline)
    index = _git("ls-files", "--stage")

    result = git_stage_batch(
        "apply", "--from", "later", "--file", path.name, check=False
    )

    if result.returncode == 0:
        assert path.read_bytes() == later_edit + target
    else:
        assert path.read_bytes() == later_edit + baseline
    assert _git("ls-files", "--stage") == index

"""Replay real tool-created batches and rebuild an evolving file as atomic commits."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import PROJECT_ROOT, git_stage_batch


SKILL_NAME = "decompose-and-commit-unstaged-changes"
EARLY = "def transform(value):\n    return value.strip()\n"
FINAL = "def transform(value):\n    return value.strip().upper()\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _plan(helper: Path, repo: Path, *args: str) -> str:
    return subprocess.run(
        [sys.executable, str(helper), "--repo", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _proof(expected: str, value: str) -> str:
    return (
        "import sys\n"
        "import unittest\n"
        "sys.path.insert(0, 'src')\n"
        "from pipeline import transform\n\n"
        "class TransformTest(unittest.TestCase):\n"
        "    def test_transform(self):\n"
        f"        self.assertEqual(transform({value!r}), {expected!r})\n"
    )


def _check_snapshot(bundle: Path, repo: Path, state: Path, expected: str) -> None:
    spec = {
        "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
        "prerequisites": {"python": sys.version},
        "commands": [
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, 'src'); from pipeline import transform; assert transform(' abc ') == {expected!r}",
            ],
        ],
    }
    if _git(repo, "ls-tree", "-r", "--name-only", "HEAD", "tests"):
        spec["commands"].append(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
        )
    path = state / "checks.json"
    path.write_text(json.dumps(spec))
    subprocess.run(
        [
            sys.executable,
            str(bundle / "verify-head-snapshot.py"),
            "--repo",
            str(repo),
            "--checks",
            str(path),
            "--evidence-dir",
            str(state / "verification"),
            "--reuse",
        ],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.parametrize("platform", ["codex"])
def test_decompose_replay_and_atomic_reconstruction_use_real_batches(
    functional_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    repo = functional_repo
    bundle = PROJECT_ROOT / "assets" / f"{platform}-skills" / SKILL_NAME / "scripts"
    state = tmp_path / "evidence"
    monkeypatch.setenv("DECOMPOSE_STATE_DIR", str(state))
    (repo / "tests").mkdir()
    (repo / "src/pipeline.py").write_text(FINAL)
    (repo / "tests/test_strip.py").write_text(_proof("42", " 42 "))
    (repo / "tests/test_upper.py").write_text(_proof("ABC", " abc "))
    base = _git(repo, "rev-parse", "HEAD")
    manifest = _plan(bundle / "decompose-plan.py", repo, "capture")
    outer, repair, inner = (
        "decompose-01-uppercase",
        "decompose-01-uppercase-repair",
        "decompose-02-strip",
    )

    # Preserve the final implementation before introducing the minimal earlier version.
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("new", outer, "--note", "Uppercase stripped values")
    for path in ("src/pipeline.py", "tests/test_upper.py"):
        git_stage_batch("show", "--file", path)
        git_stage_batch("discard", "--file", path, "--to", outer, "--no-auto-advance")
    assert not (repo / "src/pipeline.py").exists()
    (repo / "src/pipeline.py").write_text(EARLY)
    git_stage_batch("show", "--file", "src/pipeline.py")
    git_stage_batch("new", repair, "--note", "Retain the earlier stripping contract")
    git_stage_batch(
        "include", "--file", "src/pipeline.py", "--to", repair, "--no-auto-advance"
    )
    git_stage_batch("new", inner, "--note", "Strip surrounding whitespace")
    for path in ("src/pipeline.py", "tests/test_strip.py"):
        git_stage_batch("show", "--file", path)
        git_stage_batch("discard", "--file", path, "--to", inner, "--no-auto-advance")
    git_stage_batch("stop")
    assert _git(repo, "rev-parse", "HEAD") == base
    assert not _git(repo, "status", "--porcelain")
    original_refs = _git(
        repo,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        "refs/git-stage-batch/batches",
        "refs/git-stage-batch/state",
    )

    # Gate 2 stages the actual replay increments through the tool in a disposable clone.
    replay = tmp_path / "replay"
    _git(tmp_path, "clone", "--quiet", "--no-local", str(repo), str(replay))
    _git(replay, "config", "user.name", "Test User")
    _git(replay, "config", "user.email", "test@example.com")
    _git(
        replay,
        "fetch",
        "--quiet",
        "origin",
        "+refs/git-stage-batch/*:refs/git-stage-batch/*",
    )
    monkeypatch.chdir(replay)
    for batch, paths in (
        (inner, ("src/pipeline.py", "tests/test_strip.py")),
        (outer, ("src/pipeline.py", "tests/test_upper.py")),
    ):
        if batch == outer:
            git_stage_batch("discard", "--from", repair)
        git_stage_batch("apply", "--from", batch)
        git_stage_batch("start", "--no-auto-advance")
        for path in paths:
            git_stage_batch("show", "--file", path)
            git_stage_batch("include", "--file", path, "--no-auto-advance")
        _git(replay, "commit", "-qm", f"Preservation replay: {batch}")
        git_stage_batch("stop")
    _plan(bundle / "decompose-plan.py", replay, "verify-target", manifest)
    assert (
        _git(
            repo,
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/git-stage-batch/batches",
            "refs/git-stage-batch/state",
        )
        == original_refs
    )

    # Phase 3 authors code and immediately following proof at their actual snapshots.
    monkeypatch.chdir(repo)
    for batch, test_path, source, expected in (
        (inner, "tests/test_strip.py", EARLY, "abc"),
        (outer, "tests/test_upper.py", FINAL, "ABC"),
    ):
        if batch == outer:
            git_stage_batch("discard", "--from", repair)
        git_stage_batch("apply", "--from", batch)
        git_stage_batch("start", "--no-auto-advance")
        git_stage_batch("show", "--file", "src/pipeline.py")
        git_stage_batch(
            "include",
            "--file",
            "src/pipeline.py",
            "--as-stdin",
            "--no-auto-advance",
            input_text=source,
        )
        assert _git(repo, "show", ":src/pipeline.py") == source.strip()
        _git(repo, "commit", "-qm", f"Implement {batch}")
        _check_snapshot(bundle, repo, state, expected)
        git_stage_batch("show", "--file", test_path)
        git_stage_batch("include", "--file", test_path, "--no-auto-advance")
        _git(repo, "commit", "-qm", f"tests: Validate {batch}")
        _check_snapshot(bundle, repo, state, expected)
        git_stage_batch("stop")
        git_stage_batch("drop", batch)
        if batch == outer:
            git_stage_batch("drop", repair)
    _plan(bundle / "decompose-plan.py", repo, "verify-target", manifest)
    assert len(_git(repo, "rev-list", f"{base}..HEAD").splitlines()) == 4
    assert not _git(repo, "status", "--porcelain")
    assert not _git(repo, "for-each-ref", "refs/git-stage-batch/batches")

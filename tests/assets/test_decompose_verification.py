"""Exercise grouped snapshot checks and retained verification evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


SKILL_NAME = "decompose-and-commit-unstaged-changes"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@pytest.fixture(params=["codex"])
def helper(request: pytest.FixtureRequest) -> Path:
    return (
        PROJECT_ROOT
        / "assets"
        / f"{request.param}-skills"
        / SKILL_NAME
        / "scripts"
        / "verify-head-snapshot.py"
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text("generated/\n__pycache__/\n")
    (root / "value.txt").write_text("committed")
    (root / "probe.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "assert Path('value.txt').read_text() == 'committed'\n"
        "assert Path('generated/ready').read_text() == 'ready'\n"
        "with Path(sys.argv[1]).open('a') as log:\n"
        "    log.write(str(Path.cwd()) + '\\n')\n"
        "print('verified')\n"
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "Snapshot")
    return root


def _spec(counter: Path) -> dict:
    return {
        "setup": [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('generated').mkdir(); Path('generated/ready').write_text('ready')",
            ]
        ],
        "commands": [[sys.executable, "probe.py", str(counter)]] * 2,
        "prerequisites": {"python": sys.version},
    }


def _run(
    helper: Path,
    repo: Path,
    tmp_path: Path,
    spec: dict,
    *flags: str,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps(spec))
    result = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--repo",
            str(repo),
            "--checks",
            str(checks),
            "--evidence-dir",
            str(tmp_path / "evidence"),
            *flags,
        ],
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    return result, json.loads(result.stdout) if result.stdout else {}


def test_grouped_checks_use_one_clean_worktree(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    (repo / "value.txt").write_text("future dirty work")
    counter = tmp_path / "runs"
    result, report = _run(helper, repo, tmp_path, _spec(counter))
    assert result.returncode == 0, result.stderr
    assert report["passed"] and not report["reused"]
    directories = counter.read_text().splitlines()
    assert len(directories) == 2 and directories[0] == directories[1]
    assert directories[0] != str(repo) and not Path(directories[0]).exists()
    assert (repo / "value.txt").read_text() == "future dirty work"
    receipt = json.loads(Path(report["receipt"]).read_text())
    assert [r["kind"] for r in receipt["results"]] == ["setup", "commands", "commands"]
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_reworded_commit_reuses_evidence_for_identical_tree(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    counter = tmp_path / "runs"
    spec = _spec(counter)
    first, old = _run(helper, repo, tmp_path, spec, "--reuse")
    assert first.returncode == 0, first.stderr
    original_logs = {
        p.name: p.read_bytes() for p in Path(old["receipt"]).parent.iterdir()
    }
    _git(repo, "commit", "--amend", "-qm", "Reworded snapshot")
    second, new = _run(helper, repo, tmp_path, spec, "--reuse")
    assert second.returncode == 0, second.stderr
    assert new["commit"] != old["commit"] and new["tree"] == old["tree"]
    assert new["reused"] and new["receipt"] == old["receipt"]
    assert len(counter.read_text().splitlines()) == 2
    assert original_logs == {
        p.name: p.read_bytes() for p in Path(old["receipt"]).parent.iterdir()
    }


@pytest.mark.parametrize(
    "changed", ["tree", "command", "environment", "prerequisite", "harness"]
)
def test_changed_check_inputs_invalidate_reuse(
    helper: Path,
    repo: Path,
    tmp_path: Path,
    changed: str,
) -> None:
    counter = tmp_path / "runs"
    harness = tmp_path / "harness.py"
    harness.write_text("version one\n")
    spec = _spec(counter)
    spec["inputs"] = [str(harness)]
    first, old = _run(helper, repo, tmp_path, spec, "--reuse")
    assert first.returncode == 0, first.stderr
    if changed == "tree":
        (repo / "new-contract.txt").write_text("new behavior\n")
        _git(repo, "add", "new-contract.txt")
        _git(repo, "commit", "-qm", "New contract")
    elif changed == "command":
        spec["commands"][0].append("different argument")
    elif changed == "environment":
        spec["environment"] = {"DECOMPOSE_CHECK_VARIANT": "second"}
    elif changed == "prerequisite":
        spec["prerequisites"]["python"] = "Different dependency identity"
    else:
        harness.write_text("version two\n")
    second, new = _run(helper, repo, tmp_path, spec, "--reuse")
    assert second.returncode == 0, second.stderr
    assert not new["reused"] and new["receipt"] != old["receipt"]
    assert len(counter.read_text().splitlines()) == 4


def test_failed_check_is_retained_and_only_retried_with_diagnosis(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    counter = tmp_path / "attempts"
    later = tmp_path / "should-not-run"
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; p = Path({str(counter)!r}); p.write_text(p.read_text() + 'attempt\\n' if p.exists() else 'attempt\\n'); raise SystemExit(3)",
    ]
    spec = {
        "commands": [
            command,
            [sys.executable, "-c", f"open({str(later)!r}, 'w').close()"],
        ]
    }
    first, old = _run(helper, repo, tmp_path, spec, "--reuse")
    assert first.returncode == 1 and not old["passed"]
    receipt = Path(old["receipt"])
    original = receipt.read_bytes()
    second, reused = _run(helper, repo, tmp_path, spec, "--reuse")
    assert second.returncode == 1 and reused["reused"]
    assert counter.read_text().splitlines() == ["attempt"]
    third, retry = _run(
        helper,
        repo,
        tmp_path,
        spec,
        "--reuse",
        "--retry",
        "Investigated transient fixture failure",
    )
    assert third.returncode == 1 and not retry["reused"]
    assert retry["receipt"] != old["receipt"] and receipt.read_bytes() == original
    assert counter.read_text().splitlines() == ["attempt", "attempt"]
    assert not later.exists()
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("alteration", ["tracked", "untracked", "external-input"])
def test_checks_cannot_claim_success_after_changing_bound_inputs(
    helper: Path,
    repo: Path,
    tmp_path: Path,
    alteration: str,
) -> None:
    harness = tmp_path / "harness.txt"
    harness.write_text("original")
    if alteration == "external-input":
        code = f"from pathlib import Path; Path({str(harness)!r}).write_text('changed')"
    else:
        name = "value.txt" if alteration == "tracked" else "new-source.py"
        code = f"from pathlib import Path; Path({name!r}).write_text('changed')"
    result, report = _run(
        helper,
        repo,
        tmp_path,
        {"commands": [[sys.executable, "-c", code]], "inputs": [str(harness)]},
    )
    assert result.returncode == 1 and not report["passed"]
    receipt = json.loads(Path(report["receipt"]).read_text())
    if alteration == "external-input":
        assert not receipt["identity_unchanged"]
    else:
        assert not receipt["results"][0]["source_unchanged"]
    assert (repo / "value.txt").read_text() == "committed"
    assert not (repo / "new-source.py").exists()


@pytest.mark.parametrize(
    "corruption",
    ["changed-log", "missing-log", "inconsistent-receipt", "changed-allocation"],
)
def test_corrupt_evidence_cannot_be_reused(
    helper: Path,
    repo: Path,
    tmp_path: Path,
    corruption: str,
) -> None:
    counter = tmp_path / "runs"
    spec = _spec(counter)
    result, report = _run(helper, repo, tmp_path, spec, "--reuse")
    assert result.returncode == 0, result.stderr
    receipt = Path(report["receipt"])
    log = receipt.parent / "0.log"
    if corruption == "changed-log":
        log.write_text("different proof")
    elif corruption == "missing-log":
        log.unlink()
    else:
        data = json.loads(receipt.read_text())
        if corruption == "changed-allocation":
            data["results"][0]["command"] = [sys.executable, "-c", "pass"]
        else:
            data["identity_unchanged"] = False
        receipt.write_text(json.dumps(data))
    failed, _ = _run(helper, repo, tmp_path, spec, "--reuse")
    assert failed.returncode != 0
    assert "verification" in failed.stderr
    assert len(counter.read_text().splitlines()) == 2


@pytest.mark.parametrize("invalid", [False, True])
def test_default_syntax_check_needs_no_cache_ignores(
    helper: Path,
    repo: Path,
    invalid: bool,
) -> None:
    (repo / ".gitignore").unlink()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/sample.py").write_text("def broken(:\n" if invalid else "value = 1\n")
    (repo / "tests/test_sample.py").write_text("assert 1 == 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Source without cache ignores")
    result = subprocess.run(
        [sys.executable, str(helper), "--repo", str(repo)],
        text=True,
        capture_output=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == (1 if invalid else 0), result.stderr
    assert report["passed"] is not invalid
    if invalid:
        assert "SyntaxError" in result.stderr
    assert "introduced nonignored files" not in result.stderr
    assert not _git(repo, "status", "--porcelain")
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1

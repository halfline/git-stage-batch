"""Behavioral checks for preparation without constructing prototype history."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


SKILL_NAME = "decompose-and-commit-unstaged-changes"


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    ).stdout


def _commit(repo: Path, subject: str) -> str:
    """Native staging is confined to fixture setup and target-tree assertions."""
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", subject)
    return _git(repo, "rev-parse", "HEAD").decode().strip()


@pytest.fixture(params=["codex"])
def helper(request: pytest.FixtureRequest) -> Path:
    return (
        PROJECT_ROOT
        / "assets"
        / f"{request.param}-skills"
        / SKILL_NAME
        / "scripts"
        / "decompose-plan.py"
    )


def _run(
    helper: Path, repo: Path, state: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(helper), "--repo", str(repo), *args],
        env={**os.environ, "DECOMPOSE_STATE_DIR": str(state)},
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text("__pycache__/\n.git-stage-batch/\n")
    (root / "README.md").write_text("# Records\n")
    _commit(root, "Base")
    (root / "decoder.py").write_text(
        "import json\n\ndef decode(value):\n    return json.loads(value)\n"
    )
    (root / "test_decoder.py").write_text(
        "from decoder import decode\n\ndef test_decode():\n    assert decode('1') == 1\n"
    )
    (root / "cli.py").write_text("from decoder import decode\n\nprint(decode('1'))\n")
    (root / "test_cli.py").write_text("# Focused command proof\n")
    (root / "NOTES.md").write_text("Document the existing record format.\n")
    return root


def _capture(helper: Path, repo: Path, state: Path) -> str:
    result = _run(helper, repo, state, "capture")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _plan(repo: Path, state: Path, manifest: str) -> dict:
    concerns = []
    for number, slug, step, provider in (
        (1, "decode-command", 3, 3),
        (2, "format-notes", 2, None),
        (3, "record-decoder", 1, None),
    ):
        implementation = {"slug": "code", "purpose": slug, "role": "implementation"}
        commits = (
            [
                implementation,
                {
                    "slug": "proof",
                    "purpose": f"Validate {slug}",
                    "role": "verification",
                    "validates": f"{slug}/code",
                },
            ]
            if number != 2
            else [{"slug": "notes", "purpose": slug, "role": "documentation"}]
        )
        concerns.append(
            {
                "number": number,
                "name": f"decompose-{number:02d}-{slug}",
                "slug": slug,
                "purpose": slug,
                "evolution_step": step,
                "depends_on": [provider] if provider else [],
                "dependency_evidence": [
                    {
                        "provider": provider,
                        "anchor": "cli.py: decode import",
                        "contract": "decode(value)",
                    }
                ]
                if provider
                else [],
                "expected_commits": commits,
            }
        )
    return {
        "schema": 2,
        "base": _git(repo, "rev-parse", "HEAD").decode().strip(),
        "input_manifest": manifest,
        "input_digest": hashlib.sha256((state / manifest).read_bytes()).hexdigest(),
        "evolution_ladder": [
            {"step": 1, "behavior_after": "Decode records directly"},
            {"step": 2, "behavior_after": "Describe the existing format"},
            {"step": 3, "behavior_after": "Decode a supplied record from the CLI"},
        ],
        "concerns": concerns,
        "ownership_ledger": [
            {"path": path, "anchor": anchor, "concern": number, "kind": "owned"}
            for path, anchor, number in [
                ("decoder.py", "decode", 3),
                ("test_decoder.py", "test_decode", 3),
                ("NOTES.md", "format", 2),
                ("cli.py", "handler/import", 1),
                ("test_cli.py", "command proof", 1),
            ]
        ],
        "peel_order": [1, 2, 3],
        "rebuild_order": [3, 2, 1],
    }


def _write(
    state: Path, plan: dict, name: str = "decompose-plan.candidate.json"
) -> Path:
    path = state / name
    path.write_text(json.dumps(plan))
    return path


def _identity(repo: Path) -> tuple:
    return (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-files", "--stage", "-z"),
        _git(repo, "diff", "--binary", "--full-index"),
        _git(repo, "for-each-ref"),
        {
            str(p.relative_to(repo / ".git" / "objects")): p.read_bytes()
            for p in (repo / ".git" / "objects").rglob("*")
            if p.is_file()
        },
        {
            str(p.relative_to(repo)): p.read_bytes()
            for p in repo.iterdir()
            if p.is_file()
        },
    )


def test_capture_preserves_input_without_mutation(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    before = _identity(repo)
    manifest = _capture(helper, repo, state)
    assert _identity(repo) == before
    assert not (state / "snapshots").exists()
    saved = json.loads((state / manifest).read_text())
    assert len(saved["changed_paths"]) == 5
    for index, name in enumerate(saved["changed_paths"]):
        assert (state / manifest).parent.joinpath(f"{index}.blob").read_bytes() == (
            repo / name
        ).read_bytes()


@pytest.mark.parametrize("mutation", ["source", "index", "head", "batch"])
def test_input_gate_detects_execution_during_preparation(
    helper: Path,
    repo: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    state = tmp_path / "state"
    manifest = _capture(helper, repo, state)
    if mutation == "source":
        (repo / "decoder.py").write_text("changed input\n")
    elif mutation == "index":
        _git(repo, "add", "decoder.py")
    elif mutation == "head":
        _git(repo, "commit", "--allow-empty", "-qm", "Unexpected history")
    else:
        _git(repo, "update-ref", "refs/git-stage-batch/batches/unexpected", "HEAD")
    result = _run(helper, repo, state, "verify-input", manifest)
    assert result.returncode != 0
    assert "changed since input capture" in result.stderr


def test_capture_preserves_deletions_modes_symlinks_and_git_filtered_contents(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    # Extend the base fixture before starting the observed preparation operation.
    for name in ("decoder.py", "test_decoder.py", "cli.py", "test_cli.py", "NOTES.md"):
        (repo / name).unlink()
    (repo / ".gitattributes").write_text("*.txt text eol=lf\n")
    (repo / "deleted.txt").write_text("remove me\n")
    (repo / "script").write_text("#!/bin/sh\nexit 0\n")
    (repo / "link").symlink_to("README.md")
    _commit(repo, "Base with special paths")
    (repo / "deleted.txt").unlink()
    (repo / "script").chmod(0o755)
    (repo / "link").unlink()
    (repo / "link").symlink_to("script")
    (repo / "filtered.txt").write_bytes(b"line\r\n")
    (repo / 'odd\n"\\é.txt').write_bytes(b"filtered\r\n")
    state = tmp_path / "state"
    manifest = _capture(helper, repo, state)
    entries = json.loads((state / manifest).read_text())["identity"]["entries"]
    assert "deleted.txt" not in entries
    assert entries["script"]["mode"] == "100755"
    assert entries["link"]["mode"] == "120000"
    assert 'odd\n"\\é.txt' in entries
    assert (
        entries["filtered.txt"]["raw_sha256"] == hashlib.sha256(b"line\r\n").hexdigest()
    )
    _commit(repo, "Exact target")
    result = _run(helper, repo, state, "verify-target", manifest)
    assert result.returncode == 0, result.stderr
    (repo / "script").chmod(0o644)
    _commit(repo, "Wrong mode")
    assert _run(helper, repo, state, "verify-target", manifest).returncode != 0


@pytest.mark.parametrize("initialized", [True, False])
def test_capture_preserves_gitlinks_without_reading_parent_as_submodule(
    helper: Path,
    repo: Path,
    tmp_path: Path,
    initialized: bool,
) -> None:
    source = tmp_path / "submodule-source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.com")
    (source / "data").write_text("old\n")
    _commit(source, "Submodule base")
    _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(source),
        "nested",
    )
    _commit(repo, "Base with submodule")
    nested = repo / "nested"
    if initialized:
        (source / "data").write_text("new\n")
        target = _commit(source, "Submodule target")
        _git(nested, "fetch", "-q")
        _git(nested, "checkout", "-q", target)
    else:
        target = _git(nested, "rev-parse", "HEAD").decode().strip()
        _git(repo, "submodule", "deinit", "-f", "nested")
        (repo / "README.md").write_text(
            "A source change with an uninitialized submodule.\n"
        )
    state = tmp_path / "state"
    manifest = _capture(helper, repo, state)
    saved = json.loads((state / manifest).read_text())
    assert saved["identity"]["entries"]["nested"] == {
        "mode": "160000",
        "object": target,
        "raw_sha256": target,
    }
    if initialized:
        (nested / "data").write_text("uncaptured nested work\n")
        result = _run(helper, repo, state, "capture")
        assert result.returncode != 0 and "dirty submodule: nested" in result.stderr
        _git(nested, "restore", "data")
    _commit(repo, "Exact target with gitlink")
    result = _run(helper, repo, state, "verify-target", manifest)
    assert result.returncode == 0, result.stderr


def test_capture_respects_core_filemode_false(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    _git(repo, "config", "core.filemode", "false")
    (repo / "README.md").chmod(0o755)
    state = tmp_path / "state"
    manifest = _capture(helper, repo, state)
    saved = json.loads((state / manifest).read_text())
    assert saved["identity"]["entries"]["README.md"]["mode"] == "100644"
    assert "README.md" not in saved["changed_paths"]


def test_capture_rejects_staged_work_and_preserves_previous_input(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    manifest = _capture(helper, repo, state)
    original = (state / manifest).read_bytes()
    _git(repo, "add", "decoder.py")
    result = _run(helper, repo, state, "capture")
    assert result.returncode != 0 and "empty staged diff" in result.stderr
    assert (state / manifest).read_bytes() == original

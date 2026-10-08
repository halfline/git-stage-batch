"""Behavioral checks for preparation without constructing prototype history."""

from __future__ import annotations

import copy
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


@pytest.fixture(params=["codex", "claude"])
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


def test_preparation_accepts_multiple_atomic_slices_without_mutation(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    before = _identity(repo)
    manifest = _capture(helper, repo, state)
    plan = _write(state, _plan(repo, state, manifest))
    result = _run(helper, repo, state, "validate", str(plan))
    assert result.returncode == 0, result.stderr
    assert _run(helper, repo, state, "verify-input", manifest).returncode == 0
    assert _identity(repo) == before
    assert not (state / "snapshots").exists()
    saved = json.loads((state / manifest).read_text())
    assert len(saved["changed_paths"]) == 5
    for index, name in enumerate(saved["changed_paths"]):
        assert (state / manifest).parent.joinpath(f"{index}.blob").read_bytes() == (
            repo / name
        ).read_bytes()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("digest", "input digest"),
        ("dependency", "dependency order"),
        ("evidence", "dependency evidence"),
        ("proof", "immediately follow"),
        ("ownership", "cover captured changes"),
        ("duplicate", "duplicate owned region"),
        ("milestone", "milestone order"),
        ("schema", "schema 2"),
    ],
)
def test_invalid_preparation_is_rejected(
    helper: Path,
    repo: Path,
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    state = tmp_path / "state"
    plan = _plan(repo, state, _capture(helper, repo, state))
    if mutation == "digest":
        plan["input_digest"] = "stale"
    elif mutation == "dependency":
        plan["concerns"][0]["depends_on"] = [1]
    elif mutation == "evidence":
        plan["concerns"][0]["dependency_evidence"] = []
    elif mutation == "proof":
        plan["concerns"][0]["expected_commits"][1]["validates"] = "record-decoder/code"
    elif mutation == "ownership":
        plan["ownership_ledger"].pop()
    elif mutation == "duplicate":
        plan["ownership_ledger"].append(copy.deepcopy(plan["ownership_ledger"][0]))
    elif mutation == "milestone":
        plan["concerns"][0]["evolution_step"] = 1
    else:
        plan["schema"] = 1
    result = _run(helper, repo, state, "validate", str(_write(state, plan)))
    assert result.returncode != 0
    assert message in result.stderr


def test_existing_contract_proof_does_not_require_new_implementation(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    plan = _plan(repo, state, _capture(helper, repo, state))
    plan["concerns"][1]["expected_commits"] = [
        {
            "slug": "existing-proof",
            "purpose": "Validate the documented base format",
            "role": "verification",
            "validates": "base:README.md:record format",
        }
    ]
    result = _run(helper, repo, state, "validate", str(_write(state, plan)))
    assert result.returncode == 0, result.stderr


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


def _affected(helper: Path, repo: Path, state: Path, old: dict, new: dict) -> list[str]:
    before = _write(state, old, "old.json")
    after = _write(state, new, "new.json")
    result = _run(helper, repo, state, "affected", str(before), str(after))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("helper", ["codex"], indirect=True)
def test_local_correction_keeps_independent_concern_accepted(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    old = _plan(repo, state, _capture(helper, repo, state))
    new = copy.deepcopy(old)
    new["concerns"][2]["purpose"] = "Decode only supported JSON records"
    assert _affected(helper, repo, state, old, new) == [
        "record-decoder",
        "decode-command",
    ]


@pytest.mark.parametrize("helper", ["codex"], indirect=True)
def test_shared_file_only_invalidates_later_owners(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    old = _plan(repo, state, _capture(helper, repo, state))
    old["ownership_ledger"].append(
        {
            "path": "cli.py",
            "anchor": "format comment",
            "concern": 2,
            "kind": "owned",
        }
    )
    new = copy.deepcopy(old)
    new["concerns"][1]["purpose"] = "Clarify the format"
    assert _affected(helper, repo, state, old, new) == [
        "format-notes",
        "decode-command",
    ]


@pytest.mark.parametrize("helper", ["codex"], indirect=True)
def test_names_and_unrelated_insertion_do_not_reopen_accepted_concerns(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    old = _plan(repo, state, _capture(helper, repo, state))
    new = copy.deepcopy(old)
    # Add a separate documentation slice. Stable slugs make renumbering harmless.
    (repo / "EXTRA.md").write_text("An independent note.\n")
    new["input_manifest"] = _capture(helper, repo, state)
    new["input_digest"] = hashlib.sha256(
        (state / new["input_manifest"]).read_bytes()
    ).hexdigest()
    for concern in new["concerns"]:
        concern["number"] += 1
        concern["name"] = f"renamed-{concern['slug']}"
        concern["depends_on"] = [n + 1 for n in concern["depends_on"]]
        for evidence in concern["dependency_evidence"]:
            evidence["provider"] += 1
    for entry in new["ownership_ledger"]:
        entry["concern"] += 1
    new["concerns"].insert(
        0,
        {
            "number": 1,
            "slug": "extra-notes",
            "name": "decompose-extra-notes",
            "purpose": "Clarify a base comment",
            "evolution_step": 3,
            "depends_on": [],
            "dependency_evidence": [],
            "expected_commits": [
                {"slug": "notes", "purpose": "Clarify", "role": "documentation"}
            ],
        },
    )
    new["ownership_ledger"].append(
        {
            "path": "EXTRA.md",
            "anchor": "independent note",
            "kind": "owned",
            "concern": 1,
        }
    )
    new["peel_order"], new["rebuild_order"] = [1, 2, 3, 4], [4, 3, 2, 1]
    assert _affected(helper, repo, state, old, new) == ["extra-notes"]


@pytest.mark.parametrize("helper", ["codex"], indirect=True)
def test_changed_milestone_invalidates_its_actual_concerns(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    old = _plan(repo, state, _capture(helper, repo, state))
    new = copy.deepcopy(old)
    new["evolution_ladder"][1]["behavior_after"] = "Describe a corrected format"
    assert _affected(helper, repo, state, old, new) == ["format-notes"]


@pytest.mark.parametrize("helper", ["codex"], indirect=True)
def test_changed_input_invalidates_owners_and_consumers(
    helper: Path,
    repo: Path,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    old = _plan(repo, state, _capture(helper, repo, state))
    (repo / "decoder.py").write_text("def decode(value):\n    return value\n")
    new = copy.deepcopy(old)
    new["input_manifest"] = _capture(helper, repo, state)
    new["input_digest"] = hashlib.sha256(
        (state / new["input_manifest"]).read_bytes()
    ).hexdigest()
    assert _affected(helper, repo, state, old, new) == [
        "record-decoder",
        "decode-command",
    ]


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

"""Execute the documented Gate 1 check against retained Git snapshots."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_NAME = "decompose-and-commit-unstaged-changes"


def _git(repo: Path, *args: str) -> bytes:
    """Run Git without depending on a developer's diff configuration."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    ).stdout


def _commit(repo: Path, subject: str) -> str:
    """Capture one complete prototype version."""
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", subject)
    return _git(repo, "rev-parse", "HEAD").decode().strip()


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Build real provider/adopter snapshots and their binary evidence."""
    state = tmp_path / "state"
    repo = state / "snapshots"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (repo / "README.md").write_text("# Records\n", encoding="utf-8")
    base = _commit(repo, "Base")
    (repo / "decoder.py").write_text(
        "import json\n\ndef decode(value):\n    return json.loads(value)\n",
        encoding="utf-8",
    )
    provider = _commit(repo, "Decode records")
    (repo / "cli.py").write_text(
        "from decoder import decode\n\nprint(decode('{\"id\": 1}'))\n",
        encoding="utf-8",
    )
    adopter = _commit(repo, "Expose decoding")
    manifest = {"base": base, "target_commit": adopter}
    (state / "input.json").write_text(json.dumps(manifest), encoding="utf-8")
    concerns = []
    for number, slug, before, after, command in [
        (1, "decode-command", provider, adopter, [sys.executable, "cli.py"]),
        (
            2,
            "record-decoder",
            base,
            provider,
            [sys.executable, "-c", "from decoder import decode; assert decode('1') == 1"],
        ),
    ]:
        patch = f"{slug}.patch"
        (state / patch).write_bytes(
            _git(repo, "diff", "--binary", "--full-index", before, after)
        )
        # Run each proof at its own snapshot, including the provider before CLI.
        _git(repo, "checkout", "-q", "--detach", after)
        check = subprocess.run(command, cwd=repo, check=True, capture_output=True)
        log = f"{slug}.log"
        (state / log).write_bytes(check.stdout + check.stderr)
        concerns.append(
            {
                "number": number,
                "slug": slug,
                "name": f"decompose-{number:02d}-{slug}",
                "purpose": slug,
                "expected_commits": [slug],
                "evolution_step": 3 - number,
                "depends_on": [2] if number == 1 else [],
                "dependency_evidence": [{"provider": 2}] if number == 1 else [],
                "snapshot": {
                    "before": before,
                    "after": after,
                    "patch": patch,
                    "checks": [
                        {
                            "kind": "behavior",
                            "command": command,
                            "snapshot": after,
                            "exit_code": check.returncode,
                            "log": log,
                        }
                    ],
                },
            }
        )
    plan = {
        **manifest,
        "input_manifest": "input.json",
        "evidence_repo": "snapshots",
        "concerns": concerns,
        "evolution_ladder": [{"step": 1}, {"step": 2}],
        "ownership_ledger": [
            {"path": "decoder.py", "concern": 2},
            {"path": "cli.py", "concern": 1},
        ],
        "peel_order": [1, 2],
        "rebuild_order": [2, 1],
    }
    _write_plan(state, plan)
    return state


def _read_plan(state: Path) -> dict:
    return json.loads((state / "decompose-plan.candidate.json").read_text())


def _write_plan(state: Path, plan: dict) -> None:
    (state / "decompose-plan.candidate.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )


@pytest.fixture(params=["codex", "claude"])
def gate_code(request: pytest.FixtureRequest) -> str:
    """Extract executable instructions, without asserting their prose."""
    skill = PROJECT_ROOT / "assets" / f"{request.param}-skills" / SKILL_NAME / "SKILL.md"
    return skill.read_text().split("python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _gate(state: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DECOMPOSE_STATE_DIR"] = str(state)
    env.pop("DECOMPOSE_PLAN_PATH", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_gate_accepts_actual_snapshot_chain_without_mutation(
    state_dir: Path, gate_code: str
) -> None:
    """A provider and later adopter can pass with proof at their own versions."""
    repo = state_dir / "snapshots"
    before = (_git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain"))
    result = _gate(state_dir, gate_code)
    assert result.returncode == 0, result.stderr
    assert (_git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain")) == before


def test_gate_rejects_patch_different_from_snapshot(
    state_dir: Path, gate_code: str
) -> None:
    """A prose plan cannot legitimize stale or fabricated patch evidence."""
    (state_dir / "record-decoder.patch").write_text("stale patch\n")
    result = _gate(state_dir, gate_code)
    assert result.returncode != 0
    assert "patch differs" in result.stderr


def test_gate_rejects_nonchronological_snapshots(
    state_dir: Path, gate_code: str
) -> None:
    """Every proposed patch must start at the preceding accepted version."""
    plan = _read_plan(state_dir)
    plan["concerns"][0]["snapshot"]["before"] = plan["base"]
    _write_plan(state_dir, plan)
    result = _gate(state_dir, gate_code)
    assert result.returncode != 0
    assert "broken snapshot chain" in result.stderr


def test_gate_rejects_unreached_target(state_dir: Path, gate_code: str) -> None:
    """Partial history cannot pass merely because each listed patch is valid."""
    plan = _read_plan(state_dir)
    plan["target_commit"] = plan["concerns"][1]["snapshot"]["after"]
    _write_plan(state_dir, plan)
    result = _gate(state_dir, gate_code)
    assert result.returncode != 0
    assert "does not reach captured target" in result.stderr


@pytest.mark.parametrize("field", ["exit_code", "snapshot", "log"])
def test_gate_rejects_invalid_check_evidence(
    state_dir: Path, gate_code: str, field: str
) -> None:
    """A failed check, earlier snapshot, or missing log is not accepted proof."""
    plan = _read_plan(state_dir)
    check = plan["concerns"][0]["snapshot"]["checks"][0]
    check[field] = {
        "exit_code": 1,
        "snapshot": plan["base"],
        "log": "missing.log",
    }[field]
    _write_plan(state_dir, plan)
    assert _gate(state_dir, gate_code).returncode != 0


def test_gate_rejects_missing_dependency_evidence(
    state_dir: Path, gate_code: str
) -> None:
    """Ordering constraints must point to their actual provider contracts."""
    plan = _read_plan(state_dir)
    plan["concerns"][0]["dependency_evidence"] = []
    _write_plan(state_dir, plan)
    result = _gate(state_dir, gate_code)
    assert result.returncode != 0
    assert "missing dependency evidence" in result.stderr


def test_gate_rejects_mutable_snapshot_ref(state_dir: Path, gate_code: str) -> None:
    """Evidence must not silently change when a prototype branch advances."""
    plan = _read_plan(state_dir)
    plan["concerns"][1]["snapshot"]["after"] = "HEAD"
    _write_plan(state_dir, plan)
    result = _gate(state_dir, gate_code)
    assert result.returncode != 0
    assert "immutable full commit ID" in result.stderr


def test_gate_rejects_evidence_outside_state(
    state_dir: Path, gate_code: str, tmp_path: Path
) -> None:
    """A state-relative evidence path cannot escape through a symlink."""
    outside = tmp_path / "outside.patch"
    outside.write_bytes((state_dir / "record-decoder.patch").read_bytes())
    (state_dir / "record-decoder.patch").unlink()
    (state_dir / "record-decoder.patch").symlink_to(outside)
    result = _gate(state_dir, gate_code)
    assert result.returncode != 0
    assert "external evidence" in result.stderr


@pytest.fixture
def separate_proof_state(state_dir: Path) -> Path:
    """Place a real tests-only commit between provider code and CLI adoption."""
    repo = state_dir / "snapshots"
    plan = _read_plan(state_dir)
    adopter, provider = plan["concerns"]
    old_adopter = adopter["snapshot"]["after"]
    before = provider["snapshot"]["after"]
    _git(repo, "checkout", "-q", "--detach", before)
    (repo / "test_decoder.py").write_text(
        "import unittest\nfrom decoder import decode\n\n"
        "class DecoderTest(unittest.TestCase):\n"
        "    def test_decode(self):\n"
        "        self.assertEqual(decode('1'), 1)\n",
        encoding="utf-8",
    )
    proof = _commit(repo, "tests: Validate record decoding")
    command = [sys.executable, "-m", "unittest", "test_decoder"]
    result = subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (state_dir / "decoder-proof.log").write_bytes(result.stdout + result.stderr)
    (state_dir / "decoder-proof.patch").write_bytes(
        _git(repo, "diff", "--binary", "--full-index", before, proof)
    )
    _git(repo, "cherry-pick", old_adopter)
    after = _git(repo, "rev-parse", "HEAD").decode().strip()
    check = adopter["snapshot"]["checks"][0]
    result = subprocess.run(check["command"], cwd=repo, check=True, capture_output=True)
    (state_dir / check["log"]).write_bytes(result.stdout + result.stderr)
    adopter["snapshot"]["before"] = proof
    adopter["snapshot"]["after"] = after
    adopter["snapshot"]["checks"][0]["snapshot"] = after
    adopter["depends_on"] = [3]
    adopter["dependency_evidence"] = [{"provider": 3}]
    adopter["evolution_step"] = 3
    (state_dir / adopter["snapshot"]["patch"]).write_bytes(
        _git(repo, "diff", "--binary", "--full-index", proof, after)
    )
    provider["number"] = 3
    provider["name"] = "decompose-03-record-decoder"
    verification = {
        "number": 2,
        "slug": "decoder-proof",
        "name": "decompose-02-decoder-proof",
        "purpose": "Validate record decoding",
        "role": "verification",
        "validates": 3,
        "expected_commits": ["tests: Validate record decoding"],
        "evolution_step": 2,
        "depends_on": [3],
        "dependency_evidence": [{"provider": 3}],
        "snapshot": {
            "before": before,
            "after": proof,
            "patch": "decoder-proof.patch",
            "checks": [
                {
                    "kind": "behavior",
                    "command": command,
                    "snapshot": proof,
                    "exit_code": result.returncode,
                    "log": "decoder-proof.log",
                }
            ],
        },
    }
    plan["concerns"] = [adopter, verification, provider]
    plan["peel_order"] = [1, 2, 3]
    plan["rebuild_order"] = [3, 2, 1]
    plan["evolution_ladder"] = [{"step": 1}, {"step": 2}, {"step": 3}]
    plan["ownership_ledger"][0]["concern"] = 3
    plan["ownership_ledger"].append({"path": "test_decoder.py", "concern": 2})
    plan["target_commit"] = after
    _write_plan(state_dir, plan)
    return state_dir


def test_gate_accepts_separate_immediate_proof_commit(
    separate_proof_state: Path, gate_code: str
) -> None:
    """Repository-required tests can follow code without broadening its patch."""
    result = _gate(separate_proof_state, gate_code)
    assert result.returncode == 0, result.stderr


def test_gate_rejects_nonadjacent_proof_link(
    separate_proof_state: Path, gate_code: str
) -> None:
    """A proof concern cannot target code elsewhere in the sequence."""
    plan = _read_plan(separate_proof_state)
    plan["concerns"][0]["role"] = "verification"
    plan["concerns"][0]["validates"] = 3
    _write_plan(separate_proof_state, plan)
    result = _gate(separate_proof_state, gate_code)
    assert result.returncode != 0
    assert "proof must immediately follow" in result.stderr

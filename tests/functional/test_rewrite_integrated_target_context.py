"""A complete integration target keeps context after an earlier resolution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .conftest import git_stage_batch


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("last_materialization", ["EXACT", "RESOLVED"])
def test_integrated_target_keeps_insertion_context_after_resolved_prefix(
    functional_repo: Path,
    tmp_path: Path,
    last_materialization: str,
) -> None:
    modules = functional_repo / "modules.txt"
    capture = functional_repo / "capture.txt"
    base = _git("rev-parse", "HEAD")
    original_modules = "before\nafter\n"
    helper = "helper first\nhelper second\n"
    modules.write_text(original_modules, encoding="utf-8")
    _git("add", "modules.txt")
    _git("commit", "-m", "Introduce module list")

    target_modules = "before\ncapture\nafter\n"
    modules.write_text(target_modules, encoding="utf-8")
    capture.write_text(helper + "capture body\n", encoding="utf-8")
    _git("add", "modules.txt", "capture.txt")
    _git("commit", "-m", "Introduce capture")

    modules.write_text(helper + target_modules, encoding="utf-8")
    capture.write_text("capture body\n", encoding="utf-8")
    _git("commit", "-am", "Share helper with the module list")
    original_tip = _git("rev-parse", "HEAD")
    final_tree = _git("rev-parse", "HEAD^{tree}")

    plan = json.loads(git_stage_batch("rewrite", "scan", base, "--porcelain").stdout)
    first, target, repair = plan["plan"]["outputs"]
    repair_source = plan["snapshot"]["commits"][-1]
    by_path = {
        unit["path"]: unit["id"]
        for unit in repair_source["patch"]["units"]
    }
    assert set(by_path) == {"modules.txt", "capture.txt"}
    assert len(repair_source["patch"]["units"]) == 2
    for output, materialization, repair_path in (
        (first, "RESOLVED", "modules.txt"),
        (target, last_materialization, "capture.txt"),
    ):
        output["operation"] = "INTEGRATE"
        output["materialization"] = materialization
        output["source_commits"] += repair["source_commits"]
        output["source_unit_ids"].append(by_path[repair_path])
    plan["plan"]["outputs"] = [first, target]
    plan_path = tmp_path / "integration-plan.json"
    plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
    workspace = tmp_path / "resolution"
    git_stage_batch("rewrite", "lint", str(plan_path), "--porcelain")

    checkpoint = json.loads(
        git_stage_batch(
            "rewrite", "resolve", str(plan_path), "--workspace", str(workspace),
            "--porcelain",
        ).stdout
    )
    for _ in range(2):
        if checkpoint["status"] == "COMPLETE":
            break
        assert checkpoint["status"] == "NEEDS_RESOLUTION"
        result_path = Path(checkpoint["result_path"])
        result = json.loads(result_path.read_text(encoding="utf-8"))
        expected = (
            {"modules.txt": helper + original_modules}
            if checkpoint["output_index"] == 0
            else {"modules.txt": helper + target_modules, "capture.txt": "capture body\n"}
        )
        assert {entry["path"] for entry in result["paths"]} == set(expected)
        for entry in result["paths"]:
            artifact = Path(checkpoint["results_path"]) / entry["artifact"]
            artifact.write_text(expected[entry["path"]], encoding="utf-8")
            artifact.chmod(0o600)
            entry["state"] = "PRESENT"
            entry["mode"] = "100644"
        result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
        accepted = git_stage_batch(
            "rewrite", "resolve", str(plan_path), "--workspace", str(workspace),
            "--accept", "--porcelain", check=False,
        )
        assert _git("rev-parse", "HEAD") == original_tip
        assert _git("rev-parse", "HEAD^{tree}") == final_tree
        assert accepted.returncode == 0, accepted.stderr
        checkpoint = json.loads(accepted.stdout)

    assert checkpoint["status"] == "COMPLETE"
    validation = json.loads(
        git_stage_batch(
            "rewrite", "validate", str(plan_path), "--workspace", str(workspace),
            "--porcelain",
        ).stdout
    )
    assert validation["valid"] is True
    applied = json.loads(
        git_stage_batch(
            "rewrite", "apply", str(plan_path), "--workspace", str(workspace),
            "--porcelain",
        ).stdout
    )
    assert applied["phase"] == "COMPLETE"
    assert applied["verified"] is True
    assert _git("rev-parse", "HEAD^{tree}") == final_tree
    assert _git("rev-list", "--count", f"{base}..HEAD") == "2"
    assert _git("show", "HEAD^:modules.txt") == (helper + original_modules).strip()
    assert _git("show", "HEAD:modules.txt") == (helper + target_modules).strip()
    assert _git("show", "HEAD:capture.txt") == "capture body"
    assert json.loads(git_stage_batch("rewrite", "verify", "--porcelain").stdout)["verified"]

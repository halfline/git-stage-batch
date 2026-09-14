"""Rewrite authentication waits for another process using its workspace."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
import subprocess

import pytest

from .conftest import _git_stage_batch_command, git_stage_batch


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("command", ["apply", "verify"])
def test_rewrite_waits_for_resolution_reader(
    functional_repo: Path,
    tmp_path: Path,
    command: str,
) -> None:
    base = _git("rev-parse", "HEAD")
    (functional_repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git("add", "feature.txt")
    _git("commit", "-m", "Introduce feature")
    final_tree = _git("rev-parse", "HEAD^{tree}")
    plan = json.loads(git_stage_batch("rewrite", "scan", base, "--porcelain").stdout)
    plan["plan"]["outputs"][0]["materialization"] = "RESOLVED"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
    workspace = tmp_path / "resolution"
    checkpoint = json.loads(
        git_stage_batch(
            "rewrite", "resolve", str(plan_path), "--workspace", str(workspace),
            "--porcelain",
        ).stdout
    )
    result_path = Path(checkpoint["result_path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert len(result["paths"]) == 1
    entry = result["paths"][0]
    assert entry["path"] == "feature.txt"
    artifact = Path(checkpoint["results_path"]) / entry["artifact"]
    artifact.write_text("feature\n", encoding="utf-8")
    artifact.chmod(0o600)
    entry.update(state="PRESENT", mode="100644")
    result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
    resolved = json.loads(
        git_stage_batch(
            "rewrite", "resolve", str(plan_path), "--workspace", str(workspace),
            "--accept", "--porcelain",
        ).stdout
    )
    assert resolved["status"] == "COMPLETE"
    apply_args = (
        "rewrite", "apply", str(plan_path), "--workspace", str(workspace),
        "--porcelain",
    )
    arguments = apply_args
    if command != "apply":
        applied = json.loads(git_stage_batch(*apply_args).stdout)
        assert applied["phase"] == "COMPLETE" and applied["verified"]
        common = Path(_git("rev-parse", "--git-common-dir")).resolve()
        workspace = (
            common / "git-stage-batch" / "rewrite" / applied["operation_id"]
            / "resolutions"
        )
        arguments = ("rewrite", command, "--porcelain")

    # Model an independent authenticator without changing workspace contents.
    waited = False
    with (workspace / ".workspace.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        process = subprocess.Popen(
            _git_stage_batch_command(*arguments),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                waited = True
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            if waited:
                stdout, stderr = process.communicate(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    assert process.returncode == 0, stderr
    assert waited, "The command finished while another process held its workspace"
    response = json.loads(stdout)
    assert response["verified"]
    assert _git("rev-parse", "HEAD^{tree}") == final_tree
    assert _git("status", "--porcelain") == ""
    assert json.loads(git_stage_batch("rewrite", "verify", "--porcelain").stdout)["verified"]

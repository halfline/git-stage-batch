"""A concurrent workspace reader is not evidence of resolution corruption."""

from __future__ import annotations

import fcntl
import json
import subprocess
from pathlib import Path

from .conftest import _git_stage_batch_command, git_stage_batch


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_status_does_not_report_changed_resolution_when_workspace_is_busy(
    functional_repo: Path,
    tmp_path: Path,
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
    applied = json.loads(
        git_stage_batch(
            "rewrite", "apply", str(plan_path), "--workspace", str(workspace),
            "--porcelain",
        ).stdout
    )
    assert applied["phase"] == "COMPLETE" and applied["verified"]
    before = json.loads(git_stage_batch("rewrite", "status", "--porcelain").stdout)
    assert before["inspection"]["resolution_matches"] is True
    tip = _git("rev-parse", "HEAD")

    # Hold the same advisory lock used by an independent resolution reader.
    # No workspace bytes, checkpoint fields, Git objects or refs are changed.
    common = Path(_git("rev-parse", "--git-common-dir")).resolve()
    lock_path = (common / "git-stage-batch" / "rewrite" / applied["operation_id"]
                 / "resolutions" / ".workspace.lock")
    with lock_path.open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        process = subprocess.Popen(
            _git_stage_batch_command("rewrite", "status", "--porcelain"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                # Waiting for an existing reader is also a valid implementation.
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                stdout, stderr = process.communicate(timeout=30)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            if process.poll() is None:
                process.kill()
                process.communicate()

    assert process.returncode == 0, stderr
    during = json.loads(stdout)
    after = json.loads(git_stage_batch("rewrite", "status", "--porcelain").stdout)
    assert after["inspection"]["resolution_matches"] is True
    assert after["inspection"]["blockers"] == []
    assert _git("rev-parse", "HEAD") == tip
    assert _git("rev-parse", "HEAD^{tree}") == final_tree
    assert _git("status", "--porcelain") == ""
    assert json.loads(git_stage_batch("rewrite", "verify", "--porcelain").stdout)["verified"]
    assert "resolution-bundle-changed" not in during["inspection"]["blockers"], during

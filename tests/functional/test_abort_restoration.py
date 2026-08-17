"""Tests for abort restoration behavior."""

import json
import os
import subprocess

import pytest

from .conftest import git_stage_batch


def _damage_batch_ref_snapshot(snapshot_path, original_payload, damage):
    if damage == "missing":
        snapshot_path.unlink()
        return
    if damage == "empty":
        snapshot_path.write_text("", encoding="utf-8")
        return
    if damage == "malformed-json":
        snapshot_path.write_text("{", encoding="utf-8")
        return
    if damage == "top-level-array":
        snapshot_path.write_text("[]\n", encoding="utf-8")
        return

    snapshot = json.loads(original_payload)
    if damage == "batches-array":
        snapshot["batches"] = []
    elif damage == "missing-schema-version":
        del snapshot["schema_version"]
    else:
        entry = snapshot["batches"]["before-session"]
        if damage == "entry-array":
            snapshot["batches"]["before-session"] = []
        elif damage == "metadata-files-array":
            entry["metadata"]["files"] = []
        elif damage == "missing-object":
            entry["commit_sha"] = "0" * len(entry["commit_sha"])
        else:
            raise AssertionError(f"unknown damage mode: {damage}")
    snapshot_path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")


@pytest.fixture
def repo_with_staged_files(tmp_path, monkeypatch):
    """Create a repo with multiple staged files."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    # Initialize git
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    # Create multiple new files
    (repo / "file_a.py").write_text("# File A\ndef func_a():\n    return 'A'\n")
    (repo / "file_b.py").write_text("# File B\ndef func_b():\n    return 'B'\n")
    (repo / "file_c.py").write_text("# File C\ndef func_c():\n    return 'C'\n")

    # Add with intent-to-add so they appear in git diff (matches real scenario)
    # Files will show as "A " in git status
    subprocess.run(["git", "add", "-N", "file_a.py", "file_b.py", "file_c.py"],
                   check=True, capture_output=True)

    return repo


def test_abort_restores_all_files_not_just_one(repo_with_staged_files):
    """Test that abort restores all files that were present at session start."""
    # Record files before session
    status_before = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True
    )
    files_before = set(line.split()[-1] for line in status_before.stdout.strip().split('\n') if line)

    assert len(files_before) == 3, f"Expected 3 files, got {len(files_before)}: {files_before}"
    assert "file_a.py" in files_before
    assert "file_b.py" in files_before
    assert "file_c.py" in files_before

    # Start session
    git_stage_batch("start")

    # Create batch and discard one file
    git_stage_batch("new", "test-batch")
    git_stage_batch("discard", "--file", "--to", "test-batch")

    # Verify file was removed
    files_during = set(
        line.split()[-1]
        for line in subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip().split('\n')
        if line
    )
    assert len(files_during) < len(files_before), "File should have been removed during session"

    # Abort session
    git_stage_batch("abort")

    # Check files after abort
    status_after = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True
    )
    files_after = set(line.split()[-1] for line in status_after.stdout.strip().split('\n') if line)

    missing_files = files_before - files_after
    assert len(missing_files) == 0, (
        f"after abort, {len(missing_files)} file(s) were not restored\n"
        f"Missing files: {missing_files}\n"
        f"Files before: {files_before}\n"
        f"Files after: {files_after}\n"
        f"Git status after abort:\n{status_after.stdout}"
    )


def test_abort_preserves_file_git_status(repo_with_staged_files):
    """Test that abort restores files with their original git status."""
    # Record status before session
    status_before = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True
    )

    # Parse status - git status --porcelain format is "XY filename"
    # For intent-to-add files: " A filename" (space, A, space, filename)
    files_status_before = {}
    for line in status_before.stdout.strip().split('\n'):
        if line:
            # Get status code from first 2 chars (preserves spaces)
            status_code = line[0:2] if len(line) >= 2 else ""
            # Get filename by splitting on whitespace and taking last part
            # This handles the format correctly regardless of status code
            parts = line.split()
            if len(parts) > 0:
                filename = parts[-1]  # Last part is always the filename
                files_status_before[filename] = status_code

    # Intent-to-add files show as " A" in git status porcelain format
    status_code = files_status_before.get("file_a.py")
    assert status_code is not None, (
        f"file_a.py not found in git status.\n"
        f"Files found: {list(files_status_before.keys())}\n"
        f"Full status:\n{status_before.stdout}\n"
        f"Full status repr:\n{repr(status_before.stdout)}"
    )
    # Normalize: git add -N creates files with status " A" (index add) or "A " (both?)
    # Accept both formats for this test
    assert status_code in (" A", "A ", "AM"), \
        f"file_a.py should be staged with intent-to-add (status ' A' or 'A '), got '{status_code}'"

    # Start session, discard a file, and abort
    git_stage_batch("start")
    git_stage_batch("new", "test-batch")
    git_stage_batch("discard", "--file", "--to", "test-batch")
    git_stage_batch("abort")

    # Check status after abort
    status_after = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True
    )

    files_status_after = {}
    for line in status_after.stdout.strip().split('\n'):
        if line:
            status_code = line[0:2] if len(line) >= 2 else ""
            parts = line.split()
            if len(parts) > 0:
                filename = parts[-1]
                files_status_after[filename] = status_code

    for filename, status_code_before in files_status_before.items():
        status_code_after = files_status_after.get(filename)
        assert status_code_after == status_code_before, (
            f"file {filename} changed status after abort\n"
            f"Status before: '{status_code_before}'\n"
            f"Status after: '{status_code_after}'\n"
            f"Expected: Staged files should remain staged after abort"
        )


def test_abort_drops_batches_created_during_session(repo_with_staged_files):
    """Test that abort removes batches created during the session."""
    # Record batches before session
    list_before = git_stage_batch("list", check=False)
    batches_before = set()
    if list_before.returncode == 0:
        batches_before = set(list_before.stdout.strip().split('\n')) if list_before.stdout.strip() else set()

    # Start session and create batches
    git_stage_batch("start")
    git_stage_batch("new", "batch-from-session")
    git_stage_batch("new", "another-batch")

    # Verify batches exist
    list_during = git_stage_batch("list")
    assert "batch-from-session" in list_during.stdout
    assert "another-batch" in list_during.stdout

    # Discard something to one of the batches
    git_stage_batch("discard", "--file", "--to", "batch-from-session")

    # Abort session
    git_stage_batch("abort")

    # Check batches after abort
    list_after = git_stage_batch("list", check=False)
    batches_after = set()
    if list_after.returncode == 0:
        batches_after = set(list_after.stdout.strip().split('\n')) if list_after.stdout.strip() else set()

    session_batches_remaining = {"batch-from-session", "another-batch"} & batches_after
    assert len(session_batches_remaining) == 0, (
        f"after abort, {len(session_batches_remaining)} batch(es) were not dropped\n"
        f"Batches that should have been dropped: {session_batches_remaining}\n"
        f"Batches before session: {batches_before}\n"
        f"Batches after abort: {batches_after}\n"
        f"Note: Batches created during a session should be dropped on abort"
    )


def test_abort_multiple_files_discarded_all_restored(repo_with_staged_files):
    """Test that abort restores all files even when multiple were discarded.

    It discards multiple files to different batches and verifies they're all restored.
    """
    # Start session
    git_stage_batch("start")

    # Create multiple batches
    git_stage_batch("new", "batch-1")
    git_stage_batch("new", "batch-2")

    # Discard files to different batches
    git_stage_batch("discard", "--file", "--to", "batch-1")  # Discards first file
    git_stage_batch("discard", "--file", "--to", "batch-2")  # Discards second file

    # Verify files were removed
    status_during = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True
    ).stdout

    # Should have fewer files now
    files_during = [line.split()[-1] for line in status_during.strip().split('\n') if line]
    assert len(files_during) < 3, "Should have discarded at least 2 files"

    # Abort
    git_stage_batch("abort")

    # All 3 files should be back
    status_after = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True
    ).stdout

    files_after = [line.split()[-1] for line in status_after.strip().split('\n') if line]

    assert "file_a.py" in files_after, "file_a.py should be restored"
    assert "file_b.py" in files_after, "file_b.py should be restored"
    assert "file_c.py" in files_after, "file_c.py should be restored"
    assert len(files_after) == 3, f"Expected 3 files after abort, got {len(files_after)}: {files_after}"


def test_abort_stash_conflict_keeps_session_for_retry(functional_repo):
    """A failed stash restoration should retain the recovery session."""
    readme_path = functional_repo / "README.md"
    original_readme = "# Test Project\n\nChanged before the session.\n"
    readme_path.write_text(original_readme)

    staged_path = functional_repo / "new.txt"
    staged_path.write_text("original staged content\n")
    subprocess.run(["git", "add", "new.txt"], check=True, capture_output=True)

    git_stage_batch("start")

    subprocess.run(
        ["git", "rm", "--cached", "new.txt"],
        check=True,
        capture_output=True,
    )
    staged_path.write_text("colliding untracked content\n")

    failed_abort = git_stage_batch("abort", check=False)

    assert failed_abort.returncode != 0
    assert "The session remains active" in failed_abort.stderr
    assert staged_path.read_text() == "colliding untracked content\n"

    collision_path = functional_repo / "collision.txt"
    staged_path.rename(collision_path)

    git_stage_batch("abort")

    assert readme_path.read_text() == original_readme
    assert staged_path.read_text() == "original staged content\n"
    assert collision_path.read_text() == "colliding untracked content\n"
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    assert b" M README.md" in status
    assert b"A  new.txt" in status


def test_abort_restores_pre_session_applied_batch_provenance(functional_repo):
    """Abort should discard apply provenance created during the session."""
    file_path = functional_repo / "file.txt"
    file_path.write_text("before\n")
    subprocess.run(
        ["git", "add", "file.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        capture_output=True,
    )
    file_path.write_text("after\n")
    git_stage_batch("new", "restored-change")
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch(
        "discard",
        "--to",
        "restored-change",
        "--file",
        "file.txt",
        "--no-auto-advance",
    )
    git_stage_batch("stop")

    overlay_path = (
        functional_repo
        / ".git"
        / "git-stage-batch"
        / "applied-batch-overlays.json"
    )
    assert not overlay_path.exists()
    (functional_repo / "README.md").write_text("dirty before session\n")
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("apply", "--from", "restored-change")
    assert overlay_path.exists()

    git_stage_batch("abort")

    assert file_path.read_text() == "before\n"
    assert not overlay_path.exists()


def test_abort_preflights_applied_batch_provenance_snapshot(functional_repo):
    """Corrupt overlay recovery state must fail before abort mutates the tree."""
    readme_path = functional_repo / "README.md"
    readme_path.write_text("dirty at session start\n")
    git_stage_batch("start", "--no-auto-advance")
    readme_path.write_text("changed during session\n")

    git_dir = functional_repo / ".git"
    marker_path = git_dir / "git-stage-batch" / "session" / "abort" / "head.txt"
    snapshot_path = (
        git_dir
        / "git-stage-batch"
        / "session"
        / "abort"
        / "applied-batch-overlays.json"
    )
    absent_path = snapshot_path.with_suffix(".absent")
    absent_path.unlink()
    snapshot_path.write_text("{not-json", encoding="utf-8")

    failed_abort = git_stage_batch("abort", check=False)

    assert failed_abort.returncode != 0
    assert "Applied-batch state is corrupt" in failed_abort.stderr
    assert marker_path.exists()
    assert readme_path.read_text() == "changed during session\n"


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "empty",
        "malformed-json",
        "top-level-array",
        "batches-array",
        "missing-schema-version",
        "entry-array",
        "metadata-files-array",
        "missing-object",
    ],
)
def test_abort_batch_snapshot_preflight_is_strict_and_retryable(
    functional_repo,
    damage,
):
    """Damaged required batch state must fail before mutation and allow retry."""
    readme_path = functional_repo / "README.md"
    session_start_bytes = b"# dirty before session\n"
    readme_path.write_bytes(session_start_bytes)
    git_stage_batch("new", "before-session")
    git_stage_batch("start")

    git_dir = functional_repo / ".git"
    snapshot_path = (
        git_dir / "git-stage-batch" / "session" / "abort" / "batch-refs.json"
    )
    marker_path = git_dir / "git-stage-batch" / "session" / "abort" / "head.txt"
    owner_path = git_dir / "git-stage-batch" / "active-session.json"
    original_snapshot = snapshot_path.read_text(encoding="utf-8")

    git_stage_batch("drop", "before-session")
    git_stage_batch("new", "during-session")
    during_session_bytes = b"# changed during session\n"
    readme_path.write_bytes(during_session_bytes)

    head_before_abort = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    index_before_abort = subprocess.run(
        ["git", "write-tree"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    marker_before_abort = marker_path.read_bytes()
    owner_before_abort = owner_path.read_bytes()

    _damage_batch_ref_snapshot(snapshot_path, original_snapshot, damage)
    failed_abort = git_stage_batch("abort", check=False)

    assert failed_abort.returncode != 0
    assert "session remains active" in failed_abort.stderr.lower()
    assert "Session aborted" not in failed_abort.stderr
    assert "All changes reverted" not in failed_abort.stderr
    assert marker_path.read_bytes() == marker_before_abort
    assert owner_path.read_bytes() == owner_before_abort
    assert readme_path.read_bytes() == during_session_bytes
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == head_before_abort
    assert subprocess.run(
        ["git", "write-tree"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == index_before_abort
    assert subprocess.run(
        [
            "git",
            "show-ref",
            "--verify",
            "--quiet",
            "refs/git-stage-batch/batches/before-session",
        ],
        check=False,
    ).returncode != 0
    assert subprocess.run(
        [
            "git",
            "show-ref",
            "--verify",
            "--quiet",
            "refs/git-stage-batch/batches/during-session",
        ],
        check=False,
    ).returncode == 0

    snapshot_path.write_text(original_snapshot, encoding="utf-8")
    git_stage_batch("abort")

    assert readme_path.read_bytes() == session_start_bytes
    assert subprocess.run(
        [
            "git",
            "show-ref",
            "--verify",
            "--quiet",
            "refs/git-stage-batch/batches/before-session",
        ],
        check=False,
    ).returncode == 0
    assert subprocess.run(
        [
            "git",
            "show-ref",
            "--verify",
            "--quiet",
            "refs/git-stage-batch/batches/during-session",
        ],
        check=False,
    ).returncode != 0
    assert not marker_path.exists()
    assert not owner_path.exists()


@pytest.mark.parametrize("link_target", ["README.md", "missing-target"])
def test_abort_restores_discarded_untracked_symlink(functional_repo, link_target):
    """Abort should restore symlink identity without following its target."""
    link_path = functional_repo / "link"
    os.symlink(link_target, link_path)

    git_stage_batch("start")
    git_stage_batch("discard")
    assert not os.path.lexists(link_path)

    git_stage_batch("abort")

    assert link_path.is_symlink()
    assert os.readlink(link_path) == link_target

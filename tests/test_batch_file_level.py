"""Tests for file-level batch operations (--file flag)."""

import subprocess
from pathlib import Path


def test_skip_to_batch_with_file_flag(tmp_path, monkeypatch):
    """Test skip --to BATCH --file saves entire file to batch."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create initial file
    test_file = tmp_path / "file.txt"
    test_file.write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "initial"], check=True)

    # Modify file
    test_file.write_text("line1 modified\nline2 modified\nline3 modified\n")

    # Start session
    subprocess.run(["git-stage-batch", "start"], check=True)

    # Skip entire file to batch
    result = subprocess.run(
        ["git-stage-batch", "skip", "--to", "my-batch", "--file"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "Saved entire file 'file.txt' to batch 'my-batch'" in result.stdout

    # Verify batch contains the file
    result = subprocess.run(
        ["git-stage-batch", "show", "--from", "my-batch"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "file.txt" in result.stdout

    # Verify working tree still has modifications
    assert test_file.read_text() == "line1 modified\nline2 modified\nline3 modified\n"


def test_discard_to_batch_with_file_flag(tmp_path, monkeypatch):
    """Test discard --to BATCH --file saves entire file then discards it."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create initial file
    test_file = tmp_path / "file.txt"
    test_file.write_text("original content\n")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "initial"], check=True)

    # Modify file
    test_file.write_text("modified content\n")

    # Start session
    subprocess.run(["git-stage-batch", "start"], check=True)

    # Discard entire file to batch
    result = subprocess.run(
        ["git-stage-batch", "discard", "--to", "my-batch", "--file"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "Saved entire file 'file.txt' to batch 'my-batch' and discarded" in result.stdout

    # Verify batch contains the modified version
    result = subprocess.run(
        ["git-stage-batch", "show", "--from", "my-batch"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "file.txt" in result.stdout

    # Verify working tree was restored to original
    assert test_file.read_text() == "original content\n"


def test_include_from_batch_with_file_flag(tmp_path, monkeypatch):
    """Test include --from BATCH --file stages entire files (wholesale)."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create initial files
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file1.write_text("original1\n")
    file2.write_text("original2\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "initial"], check=True)

    # Modify files and save to batch
    file1.write_text("modified1\n")
    file2.write_text("modified2\n")
    subprocess.run(["git-stage-batch", "start"], check=True)
    subprocess.run(["git-stage-batch", "skip", "--to", "my-batch", "--file"], check=True)

    # Advance to next file
    subprocess.run(["git-stage-batch", "skip", "--to", "my-batch", "--file"], check=True)

    # Reset working tree
    subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True)
    assert file1.read_text() == "original1\n"
    assert file2.read_text() == "original2\n"

    # Include from batch with --file flag
    result = subprocess.run(
        ["git-stage-batch", "include", "--from", "my-batch", "--file"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "wholesale" in result.stdout

    # Verify both working tree and index have the changes
    assert file1.read_text() == "modified1\n"
    assert file2.read_text() == "modified2\n"

    # Check index
    result = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True)
    assert "modified1" in result.stdout
    assert "modified2" in result.stdout


def test_apply_from_batch_with_file_flag(tmp_path, monkeypatch):
    """Test apply --from BATCH --file applies files to working tree only (wholesale)."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create initial file
    test_file = tmp_path / "file.txt"
    test_file.write_text("original\n")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "initial"], check=True)

    # Modify and save to batch
    test_file.write_text("modified\n")
    subprocess.run(["git-stage-batch", "start"], check=True)
    subprocess.run(["git-stage-batch", "skip", "--to", "my-batch"], check=True)

    # Reset working tree
    subprocess.run(["git", "checkout", "HEAD", "--", "file.txt"], check=True)
    assert test_file.read_text() == "original\n"

    # Apply from batch with --file flag (working tree only)
    result = subprocess.run(
        ["git-stage-batch", "apply", "--from", "my-batch", "--file"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "wholesale" in result.stdout

    # Verify working tree has the changes
    assert test_file.read_text() == "modified\n"

    # Verify index does NOT have the changes
    result = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True)
    assert result.stdout.strip() == ""


def test_discard_from_batch_with_file_flag(tmp_path, monkeypatch):
    """Test discard --from BATCH --file restores files to baseline (wholesale)."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create initial file
    test_file = tmp_path / "file.txt"
    test_file.write_text("baseline\n")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], check=True)

    # Modify and save to batch
    test_file.write_text("modified\n")
    subprocess.run(["git-stage-batch", "start"], check=True)
    subprocess.run(["git-stage-batch", "skip", "--to", "my-batch"], check=True)

    # Further modify working tree (different from batch)
    test_file.write_text("further modified\n")

    # Discard from batch with --file flag (should restore to baseline)
    result = subprocess.run(
        ["git-stage-batch", "discard", "--from", "my-batch", "--file"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "wholesale" in result.stdout

    # Verify working tree was restored to baseline
    assert test_file.read_text() == "baseline\n"

    # Verify batch still exists
    result = subprocess.run(
        ["git-stage-batch", "list"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "my-batch" in result.stdout


def test_file_flag_with_multiple_files(tmp_path, monkeypatch):
    """Test --file flag with batch containing multiple files."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create multiple files
    files = []
    for i in range(3):
        f = tmp_path / f"file{i}.txt"
        f.write_text(f"original{i}\n")
        files.append(f)

    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "initial"], check=True)

    # Modify all files and save to batch
    for i, f in enumerate(files):
        f.write_text(f"modified{i}\n")

    subprocess.run(["git-stage-batch", "start"], check=True)

    # Skip all files to batch
    for _ in range(3):
        subprocess.run(["git-stage-batch", "skip", "--to", "multi-batch", "--file"], check=True)

    # Reset working tree
    subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True)
    for i, f in enumerate(files):
        assert f.read_text() == f"original{i}\n"

    # Include all files from batch
    subprocess.run(
        ["git-stage-batch", "include", "--from", "multi-batch", "--file"],
        check=True
    )

    # Verify all files were restored
    for i, f in enumerate(files):
        assert f.read_text() == f"modified{i}\n"


def test_file_flag_preserves_file_mode(tmp_path, monkeypatch):
    """Test --file flag preserves executable file mode."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create executable file
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/bash\necho original\n")
    script.chmod(0o755)
    subprocess.run(["git", "add", "script.sh"], check=True)
    subprocess.run(["git", "commit", "-m", "initial"], check=True)

    # Modify and save to batch
    script.write_text("#!/bin/bash\necho modified\n")
    subprocess.run(["git-stage-batch", "start"], check=True)
    subprocess.run(["git-stage-batch", "skip", "--to", "exec-batch", "--file"], check=True)

    # Reset working tree
    subprocess.run(["git", "checkout", "HEAD", "--", "script.sh"], check=True)

    # Include from batch with --file
    subprocess.run(
        ["git-stage-batch", "include", "--from", "exec-batch", "--file"],
        check=True
    )

    # Verify executable mode was preserved
    result = subprocess.run(
        ["git", "ls-files", "-s", "script.sh"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "100755" in result.stdout

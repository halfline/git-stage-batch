"""Shared fixtures for functional tests."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def functional_repo(tmp_path, monkeypatch):
    """Create a realistic git repository for functional testing.

    Sets up a repo with multiple files and realistic changes for testing
    the full command workflow.
    """
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    # Initialize git
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

    # Create initial commit with multiple files
    (repo / "README.md").write_text("# Test Project\n\nA test project.\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text(
        "def main():\n"
        "    print('Hello')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    (repo / "src" / "utils.py").write_text(
        "def helper():\n"
        "    return 42\n"
    )

    subprocess.run(["git", "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    return repo


@pytest.fixture
def repo_with_changes(functional_repo):
    """Repo with uncommitted changes ready for staging.

    Creates realistic changes:
    - Modified file with multiple changes
    - New file
    - Deleted lines in existing file
    """
    # Modify README
    (functional_repo / "README.md").write_text(
        "# Test Project\n"
        "\n"
        "A test project for git-stage-batch.\n"
        "\n"
        "## Features\n"
        "- Line-level staging\n"
        "- Batch operations\n"
    )

    # Modify main.py
    (functional_repo / "src" / "main.py").write_text(
        "import sys\n"
        "\n"
        "def main():\n"
        "    print('Hello, World!')\n"
        "    print('Welcome to git-stage-batch')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )

    # Add new file
    (functional_repo / "src" / "config.py").write_text(
        "CONFIG = {\n"
        "    'debug': True,\n"
        "    'version': '1.0.0',\n"
        "}\n"
    )

    return functional_repo


def git_stage_batch(*args, input_text=None, check=True):
    """Run git-stage-batch command from in-tree source.

    Args:
        *args: Command arguments (e.g., 'start', 'include', '1')
        input_text: Optional stdin input
        check: Whether to check return code

    Returns:
        subprocess.CompletedProcess
    """
    import sys
    import os

    # Find the project root (where pyproject.toml is)
    test_dir = Path(__file__).parent
    project_root = test_dir.parent.parent
    venv_gsb = project_root / ".venv" / "bin" / "git-stage-batch"

    # Use the venv git-stage-batch directly to ensure we get the in-tree version
    if venv_gsb.exists():
        cmd = [str(venv_gsb)] + list(args)
    else:
        # Fallback to uv run if venv not found
        cmd = ["uv", "run", "--", "git-stage-batch"] + list(args)

    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=check
    )
    return result


def get_git_status():
    """Get current git status."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout


def get_staged_files():
    """Get list of staged files."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True
    )
    return [f for f in result.stdout.strip().split("\n") if f]


def get_unstaged_diff(file_path=None):
    """Get unstaged diff."""
    cmd = ["git", "diff"]
    if file_path:
        cmd.append(file_path)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout


def get_staged_diff(file_path=None):
    """Get staged diff."""
    cmd = ["git", "diff", "--cached"]
    if file_path:
        cmd.append(file_path)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout

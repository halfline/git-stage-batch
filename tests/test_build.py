"""Tests for build system configuration."""

import subprocess
import sys
from pathlib import Path


def test_meson_build_works(tmp_path):
    """Test that meson can build the project."""
    # Get the project root
    project_root = Path(__file__).parent.parent

    # Setup build in temporary directory
    build_dir = tmp_path / "build"
    result = subprocess.run(
        ["meson", "setup", str(build_dir)],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"meson setup failed: {result.stderr}"

    # Compile
    result = subprocess.run(
        ["meson", "compile", "-C", str(build_dir)],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"meson compile failed: {result.stderr}"


def test_version_file_generated(tmp_path):
    """Test that _version.py is generated with correct content."""
    project_root = Path(__file__).parent.parent
    build_dir = tmp_path / "build"

    # Read expected version
    expected_version = (project_root / "VERSION").read_text().strip()

    # Build the project
    subprocess.run(["meson", "setup", str(build_dir)], cwd=project_root, check=True, capture_output=True)
    subprocess.run(["meson", "compile", "-C", str(build_dir)], cwd=project_root, check=True, capture_output=True)

    # Check version file was generated
    version_file = build_dir / "src/git_stage_batch/_version.py"
    assert version_file.exists(), "_version.py was not generated"

    # Read and validate content
    content = version_file.read_text()
    assert "__version__" in content
    assert expected_version in content


def test_validate_man_page_generated(tmp_path):
    """The metadata validation command should ship its dedicated manual."""
    project_root = Path(__file__).parent.parent
    build_dir = tmp_path / "build"

    subprocess.run(
        ["meson", "setup", str(build_dir)],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["meson", "compile", "-C", str(build_dir)],
        cwd=project_root,
        check=True,
        capture_output=True,
    )

    man_page = build_dir / "git-stage-batch-validate.1"
    assert man_page.exists()
    content = man_page.read_text(encoding="utf-8")
    assert "validate persisted batch metadata" in content
    assert "porcelain" in content


def test_journal_man_page_generated(tmp_path):
    """The diagnostic journal command should ship its dedicated manual."""
    project_root = Path(__file__).parent.parent
    build_dir = tmp_path / "build"

    subprocess.run(
        ["meson", "setup", str(build_dir)],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["meson", "compile", "-C", str(build_dir)],
        cwd=project_root,
        check=True,
        capture_output=True,
    )

    man_page = build_dir / "git-stage-batch-journal.1"
    assert man_page.exists()
    content = man_page.read_text(encoding="utf-8")
    assert "diagnostic journal data" in content
    assert "content-debug" in content


def test_package_importable_from_build(tmp_path):
    """Test that the package can be imported from the build directory."""
    project_root = Path(__file__).parent.parent
    build_dir = tmp_path / "build"

    # Read expected version
    expected_version = (project_root / "VERSION").read_text().strip()

    # Build the project
    subprocess.run(["meson", "setup", str(build_dir)], cwd=project_root, check=True, capture_output=True)
    subprocess.run(["meson", "compile", "-C", str(build_dir)], cwd=project_root, check=True, capture_output=True)

    # Try to import the package
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, '{project_root / 'src'}'); sys.path.insert(0, '{build_dir / 'src/git_stage_batch'}'); "
            "import git_stage_batch; print(git_stage_batch.__version__)"
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Failed to import package: {result.stderr}"
    assert expected_version in result.stdout

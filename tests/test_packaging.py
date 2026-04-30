"""Packaging and distribution tests.

These tests validate that the built wheel contains all necessary files
and that translations work after installation.

The wheel is automatically built as part of the test setup.
"""

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def build_wheel():
    """Build the wheel once for all tests in this module."""
    project_root = Path(__file__).parent.parent
    dist_dir = project_root / "dist"
    build_dir = project_root / "build-wheel"

    try:
        # Build the wheel using a dedicated build directory
        result = subprocess.run(
            ["uv", "build", "--wheel", f"-Cbuild-dir={build_dir}"],
            cwd=project_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            pytest.fail(f"Failed to build wheel: {result.stderr}")

        # Find the built wheel
        wheels = list(dist_dir.glob("*.whl"))
        if not wheels:
            pytest.fail("No wheel file found after build")

        return max(wheels, key=lambda p: p.stat().st_mtime)
    finally:
        # Clean up the build directory
        if build_dir.exists():
            shutil.rmtree(build_dir)


class TestWheelContents:
    """Test that the wheel package contains expected files."""

    def test_wheel_contains_all_source_files(self, build_wheel):
        """Test that wheel contains all Python source files."""
        with zipfile.ZipFile(build_wheel, 'r') as whl:
            files = whl.namelist()

        # Check for core modules
        expected_files = [
            'git_stage_batch/__init__.py',
            'git_stage_batch/i18n.py',
            'git_stage_batch/_version.py',
        ]

        for expected in expected_files:
            assert any(expected in f for f in files), f"Missing {expected}"

    def test_wheel_contains_packaged_man_page(self, build_wheel):
        """Test that wheel contains the packaged man page fallback."""
        with zipfile.ZipFile(build_wheel, 'r') as whl:
            files = whl.namelist()

        assert any(
            'git_stage_batch/assets/man/man1/git-stage-batch.1' in f
            for f in files
        ), "Missing packaged man page asset"

    def test_wheel_contains_claude_agent_asset(self, build_wheel):
        """Test that wheel contains the bundled Claude agent asset."""
        with zipfile.ZipFile(build_wheel, 'r') as whl:
            files = whl.namelist()

        assert any(
            'git_stage_batch/assets/claude-agents/commit-message-drafter.md' in f
            for f in files
        ), "Missing bundled Claude agent asset"
    def test_wheel_contains_entry_point_script(self, build_wheel):
        """Test that wheel contains the executable entry point."""
        with zipfile.ZipFile(build_wheel, 'r') as whl:
            files = whl.namelist()
            # Check for entry_points.txt
            assert any('entry_points.txt' in f for f in files), \
                "Missing entry_points.txt"

            # Find the entry_points.txt file dynamically
            entry_points_file = next(f for f in files if 'entry_points.txt' in f)

            # Verify entry_points.txt contains git-stage-batch
            entry_points_content = whl.read(entry_points_file).decode('utf-8')
            assert 'git-stage-batch' in entry_points_content, \
                "Missing git-stage-batch entry point in entry_points.txt"


class TestWheelInstallation:
    """Test that the wheel can be installed and works correctly."""

    @pytest.fixture
    def clean_venv(self):
        """Create a clean virtual environment for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv_path = Path(tmpdir) / "test_venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
            yield venv_path

    def test_wheel_installs_successfully(self, build_wheel, clean_venv):
        """Test that the wheel can be installed in a clean environment."""
        pip_path = clean_venv / "bin" / "pip"
        result = subprocess.run(
            [str(pip_path), "install", str(build_wheel)],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0, f"Install failed: {result.stderr}"

    def test_installed_package_imports(self, build_wheel, clean_venv):
        """Test that installed package can be imported."""
        pip_path = clean_venv / "bin" / "pip"
        python_path = clean_venv / "bin" / "python"

        # Install
        subprocess.run([str(pip_path), "install", str(build_wheel)], check=True)

        # Try to import
        result = subprocess.run(
            [str(python_path), "-c", "import git_stage_batch; print('OK')"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "OK" in result.stdout


class TestMesonInstallation:
    """Test that meson install works correctly."""

    def test_meson_install_to_prefix(self):
        """Test that meson can install to a custom prefix."""
        project_root = Path(__file__).parent.parent

        with tempfile.TemporaryDirectory() as tmpdir:
            build_dir = Path(tmpdir) / "build"
            install_prefix = Path(tmpdir) / "install"

            # Configure meson with custom prefix
            result = subprocess.run(
                ["meson", "setup", str(build_dir), f"--prefix={install_prefix}"],
                cwd=project_root,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                pytest.skip(f"Meson setup failed: {result.stderr}")

            # Compile
            result = subprocess.run(
                ["meson", "compile", "-C", str(build_dir)],
                capture_output=True,
                text=True
            )

            assert result.returncode == 0, f"Meson compile failed: {result.stderr}"

            # Install
            result = subprocess.run(
                ["meson", "install", "-C", str(build_dir)],
                capture_output=True,
                text=True
            )

            assert result.returncode == 0, f"Meson install failed: {result.stderr}"

            # Check that Python files were installed
            # Meson typically installs to prefix/lib/pythonX.Y/site-packages
            site_packages = list(install_prefix.glob("lib*/python*/site-packages"))
            assert len(site_packages) > 0, "No site-packages directory found in install prefix"

            package_dir = site_packages[0] / "git_stage_batch"
            assert package_dir.exists(), f"Package not installed to {package_dir}"

            # Check core modules exist
            assert (package_dir / "__init__.py").exists()
            assert (package_dir / "_version.py").exists()

    def test_meson_installed_package_imports(self):
        """Test that the meson-installed package can be imported."""
        project_root = Path(__file__).parent.parent

        with tempfile.TemporaryDirectory() as tmpdir:
            build_dir = Path(tmpdir) / "build"
            install_prefix = Path(tmpdir) / "install"

            # Configure, build, and install
            subprocess.run(
                ["meson", "setup", str(build_dir), f"--prefix={install_prefix}"],
                cwd=project_root,
                capture_output=True
            )
            subprocess.run(
                ["meson", "compile", "-C", str(build_dir)],
                capture_output=True
            )
            result = subprocess.run(
                ["meson", "install", "-C", str(build_dir)],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                pytest.skip(f"Meson install failed: {result.stderr}")

            # Find site-packages
            site_packages = list(install_prefix.glob("lib*/python*/site-packages"))
            assert len(site_packages) > 0

            # Try to import the package
            env = {"PYTHONPATH": str(site_packages[0])}
            result = subprocess.run(
                [sys.executable, "-c", "import git_stage_batch; print('OK')"],
                capture_output=True,
                text=True,
                env={**subprocess.os.environ, **env}
            )

            assert result.returncode == 0, f"Import failed: {result.stderr}"
            assert "OK" in result.stdout

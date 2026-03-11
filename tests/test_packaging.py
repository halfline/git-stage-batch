"""Packaging and distribution tests.

These tests validate that the built wheel contains all necessary files
and that translations work after installation.

Run these tests after building the wheel:
    uv build --wheel
    pytest tests/test_packaging.py
"""

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest


class TestWheelContents:
    """Test that the wheel package contains expected files."""

    def test_wheel_contains_all_source_files(self):
        """Test that wheel contains all Python source files."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        with zipfile.ZipFile(wheel_path, 'r') as whl:
            files = whl.namelist()

        # Check for core modules
        expected_files = [
            'git_stage_batch/__init__.py',
            'git_stage_batch/cli.py',
            'git_stage_batch/commands.py',
            'git_stage_batch/display.py',
            'git_stage_batch/editor.py',
            'git_stage_batch/hashing.py',
            'git_stage_batch/i18n.py',
            'git_stage_batch/line_selection.py',
            'git_stage_batch/models.py',
            'git_stage_batch/parser.py',
            'git_stage_batch/state.py',
            'git_stage_batch/_version.py',
        ]

        for expected in expected_files:
            assert any(expected in f for f in files), f"Missing {expected}"

    def test_wheel_contains_entry_point_script(self):
        """Test that wheel contains the executable entry point."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        with zipfile.ZipFile(wheel_path, 'r') as whl:
            files = whl.namelist()

        # Check for entry point in scripts or data
        assert any('git-stage-batch' in f and 'scripts' in f for f in files), \
            "Missing git-stage-batch entry point script"

    def _find_wheel(self):
        """Find the most recent wheel file in dist/."""
        dist_dir = Path(__file__).parent.parent / 'dist'
        if not dist_dir.exists():
            return None

        wheels = list(dist_dir.glob('*.whl'))
        if not wheels:
            return None

        # Return most recent
        return max(wheels, key=lambda p: p.stat().st_mtime)


class TestWheelInstallation:
    """Test that the wheel can be installed and works correctly."""

    @pytest.fixture
    def clean_venv(self):
        """Create a clean virtual environment for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv_path = Path(tmpdir) / "test_venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
            yield venv_path

    def test_wheel_installs_successfully(self, clean_venv):
        """Test that the wheel can be installed in a clean environment."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        pip_path = clean_venv / "bin" / "pip"
        result = subprocess.run(
            [str(pip_path), "install", str(wheel_path)],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0, f"Install failed: {result.stderr}"

    def test_installed_package_imports(self, clean_venv):
        """Test that installed package can be imported."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        pip_path = clean_venv / "bin" / "pip"
        python_path = clean_venv / "bin" / "python"

        # Install
        subprocess.run([str(pip_path), "install", str(wheel_path)], check=True)

        # Try to import
        result = subprocess.run(
            [str(python_path), "-c", "import git_stage_batch; print('OK')"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_cli_executable_works_after_install(self, clean_venv):
        """Test that git-stage-batch CLI works after installation."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        pip_path = clean_venv / "bin" / "pip"
        cli_path = clean_venv / "bin" / "git-stage-batch"

        # Install
        subprocess.run([str(pip_path), "install", str(wheel_path)], check=True)

        # Try to run CLI (outside git repo should fail gracefully)
        result = subprocess.run(
            [str(cli_path), "status"],
            capture_output=True,
            text=True,
            cwd="/tmp"
        )

        # Should fail but with expected error message
        assert "Not inside a git repository" in result.stderr or \
               "not a git repository" in result.stderr.lower()

    def _find_wheel(self):
        """Find the most recent wheel file in dist/."""
        dist_dir = Path(__file__).parent.parent / 'dist'
        if not dist_dir.exists():
            return None

        wheels = list(dist_dir.glob('*.whl'))
        if not wheels:
            return None

        return max(wheels, key=lambda p: p.stat().st_mtime)


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
            assert (package_dir / "cli.py").exists()
            assert (package_dir / "commands.py").exists()
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

    def test_meson_install_includes_executable(self):
        """Test that meson install includes the git-stage-batch executable."""
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

            # Check for executable in bin directory
            executable = install_prefix / "bin" / "git-stage-batch"
            assert executable.exists(), f"Executable not found at {executable}"

            # Check it's executable
            assert executable.stat().st_mode & 0o111, "Executable doesn't have execute permission"

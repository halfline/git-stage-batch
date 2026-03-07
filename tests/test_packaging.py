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

    def test_wheel_contains_translation_files(self):
        """Test that wheel contains .mo translation files."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        with zipfile.ZipFile(wheel_path, 'r') as whl:
            files = whl.namelist()

        # Check for translation files
        mo_files = [f for f in files if f.endswith('.mo')]
        assert len(mo_files) > 0, "No .mo translation files found in wheel"

        # Check for specific languages
        expected_languages = ['es', 'fr', 'de', 'ja', 'zh_CN', 'pt_BR', 'cs', 'it', 'hi']
        for lang in expected_languages:
            pattern = f'git_stage_batch/locale/{lang}/LC_MESSAGES/git-stage-batch.mo'
            assert any(pattern in f for f in files), f"Missing translation for {lang}"

    def test_wheel_mo_files_named_correctly(self):
        """Test that .mo files have the correct name (git-stage-batch.mo)."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        with zipfile.ZipFile(wheel_path, 'r') as whl:
            mo_files = [f for f in whl.namelist() if f.endswith('.mo')]

        # All should be named git-stage-batch.mo (not language-specific names)
        for mo_file in mo_files:
            assert mo_file.endswith('git-stage-batch.mo'), \
                f"MO file has wrong name: {mo_file}"

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

    def test_translations_loadable_after_install(self, clean_venv):
        """Test that .mo files can be loaded after installation."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        pip_path = clean_venv / "bin" / "pip"
        python_path = clean_venv / "bin" / "python"

        # Install
        subprocess.run([str(pip_path), "install", str(wheel_path)], check=True)

        # Try to load Spanish translation
        test_script = """
import gettext
import importlib.resources

locale_dir = str(importlib.resources.files('git_stage_batch') / 'locale')
t = gettext.translation('git-stage-batch', localedir=locale_dir, languages=['es'], fallback=False)
msg = t.gettext('No batch staging session in progress.')
print(msg)
"""

        result = subprocess.run(
            [str(python_path), "-c", test_script],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0, f"Translation load failed: {result.stderr}"
        # Should get Spanish translation, not English
        assert "sesión" in result.stdout or "preparación" in result.stdout, \
            f"Expected Spanish translation, got: {result.stdout}"

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


class TestTranslationFilesValidity:
    """Test that translation files are valid."""

    def test_mo_files_are_valid_gettext_format(self):
        """Test that .mo files in the wheel are valid gettext files."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        with zipfile.ZipFile(wheel_path, 'r') as whl:
            mo_files = [f for f in whl.namelist() if f.endswith('.mo')]

            for mo_file in mo_files:
                mo_data = whl.read(mo_file)

                # Check for gettext magic number
                # GNU gettext .mo files start with 0x950412de or 0xde120495
                magic = int.from_bytes(mo_data[:4], byteorder='little')
                assert magic in [0x950412de, 0xde120495], \
                    f"{mo_file} is not a valid gettext .mo file (bad magic number)"

    def test_all_expected_languages_present(self):
        """Test that all expected language translations are in the wheel."""
        wheel_path = self._find_wheel()
        if not wheel_path:
            pytest.skip("No wheel found in dist/")

        with zipfile.ZipFile(wheel_path, 'r') as whl:
            files = whl.namelist()

        # These are the languages we've added
        expected_langs = ['cs', 'de', 'es', 'fr', 'hi', 'it', 'ja', 'pt_BR', 'zh_CN']

        for lang in expected_langs:
            mo_path = f'git_stage_batch/locale/{lang}/LC_MESSAGES/git-stage-batch.mo'
            assert any(mo_path in f for f in files), \
                f"Missing translation for language: {lang}"

    def _find_wheel(self):
        """Find the most recent wheel file in dist/."""
        dist_dir = Path(__file__).parent.parent / 'dist'
        if not dist_dir.exists():
            return None

        wheels = list(dist_dir.glob('*.whl'))
        if not wheels:
            return None

        return max(wheels, key=lambda p: p.stat().st_mtime)

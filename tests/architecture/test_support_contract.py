"""Keep public compatibility metadata aligned with continuous integration."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_project_file(path: str) -> str:
    return (PROJECT_ROOT / path).read_text()


def test_python_metadata_matches_tested_interpreters():
    """Package metadata should stay open while CI covers current releases."""
    pyproject = _read_project_file("pyproject.toml")
    workflow = _read_project_file(".github/workflows/ci.yml")

    assert 'requires-python = ">=3.10"' in pyproject
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]' in workflow


def test_ci_exercises_minimum_git():
    """CI should cover the minimum supported Git release."""
    workflow = _read_project_file(".github/workflows/ci.yml")

    assert 'GIT_VERSION: "2.39.0"' in workflow
    assert 'repository: git/git' in workflow
    assert 'run: test "$(git version)" = "git version ${GIT_VERSION}"' in workflow


def test_ci_exercises_macos():
    """CI should cover the documented macOS platform."""
    workflow = _read_project_file(".github/workflows/ci.yml")

    assert "runs-on: macos-latest" in workflow


def test_ci_install_checks_both_distribution_formats():
    """Built wheels and source distributions should each be installed."""
    workflow = _read_project_file(".github/workflows/ci.yml")

    assert "dist/*.whl" in workflow
    assert "dist/*.tar.gz" in workflow


def test_ci_enforces_dead_code_for_complete_pull_request_stacks():
    """Intermediate pull requests in a stack should warn.

    Complete trees should retain strict dead-code enforcement.
    """
    workflow = _read_project_file(".github/workflows/ci.yml")

    assert "github.event.pull_request.stack == null" in workflow
    assert (
        "github.event.pull_request.stack.position == "
        "github.event.pull_request.stack.size"
    ) in workflow
    assert "Check for dead code (advisory)" in workflow
    assert "scripts/check_dead_code.py --advisory" in workflow
    assert (
        "github.event.pull_request.stack.position < "
        "github.event.pull_request.stack.size"
    ) in workflow


def test_release_uses_trusted_publishing():
    """The PyPI job should use OIDC without a stored publishing token."""
    workflow = _read_project_file(".github/workflows/release.yml")

    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "secrets." not in workflow
    assert "password:" not in workflow

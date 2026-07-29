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

    assert 'GIT_VERSION: "2.31.0"' in workflow
    assert 'repository: git/git' in workflow
    assert 'run: test "$(git version)" = "git version ${GIT_VERSION}"' in workflow

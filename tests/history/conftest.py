"""Fixtures for rewrite-plan tests."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest


def git(*arguments: str, check: bool = True, input_bytes: bytes | None = None) -> str:
    """Run Git in the current test repository."""
    result = subprocess.run(
        ["git", *arguments],
        check=check,
        input=input_bytes,
        capture_output=True,
        text=input_bytes is None,
    )
    stdout = result.stdout
    if isinstance(stdout, bytes):
        return stdout.decode("ascii").strip()
    return stdout.strip()


@pytest.fixture
def linear_history_repo(tmp_path, monkeypatch):
    """Create one clean two-commit linear topic after an excluded base."""
    monkeypatch.chdir(tmp_path)
    git("init", "-b", "topic")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")

    source = tmp_path / "example.txt"
    source.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    git("add", "example.txt")
    git("commit", "-m", "Base")
    base = git("rev-parse", "HEAD")

    source.write_text("alpha topic\nbeta\ngamma\n", encoding="utf-8")
    git("commit", "-am", "Change alpha")
    first = git("rev-parse", "HEAD")

    source.write_text("alpha topic\nbeta\ngamma topic\n", encoding="utf-8")
    git("commit", "-am", "Change gamma")
    tip = git("rev-parse", "HEAD")

    return SimpleNamespace(
        root=tmp_path,
        source=source,
        base=base,
        first=first,
        tip=tip,
    )

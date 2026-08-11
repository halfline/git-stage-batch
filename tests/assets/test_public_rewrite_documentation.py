"""Tests for public rewrite positioning and cache documentation."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_rewrite_manual_states_remote_and_abort_boundaries() -> None:
    """Installed rewrite help should describe its mutation limits."""
    manual = " ".join(_read("man/git-stage-batch-rewrite.1.in").split())

    assert "Apply does not contact a remote" in manual
    assert "never performs or authorizes a push or force-push" in manual

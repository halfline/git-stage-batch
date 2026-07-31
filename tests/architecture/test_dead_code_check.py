"""Tests for the maintained dead-code policy check."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "check_dead_code.py"
SPEC = importlib.util.spec_from_file_location("check_dead_code", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
check_dead_code = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_dead_code
SPEC.loader.exec_module(check_dead_code)


@pytest.fixture
def unexpected_finding(monkeypatch):
    finding = check_dead_code.Finding(
        identity=check_dead_code.FindingIdentity(
            path="src/git_stage_batch/example.py",
            kind="function",
            name="unused_helper",
        ),
        line=42,
        message="unused function 'unused_helper'",
        confidence=100,
    )
    monkeypatch.setattr(check_dead_code, "ALLOWED_FINDINGS", ())
    monkeypatch.setattr(check_dead_code, "find_unused_code", lambda: [finding])
    return finding


def test_strict_mode_rejects_unexpected_finding(unexpected_finding, capsys):
    assert check_dead_code.main([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "src/git_stage_batch/example.py:42" in captured.err
    assert "::warning" not in captured.err


def test_advisory_mode_warns_without_failing(unexpected_finding, capsys):
    assert check_dead_code.main(["--advisory"]) == 0

    captured = capsys.readouterr()
    assert "src/git_stage_batch/example.py:42" in captured.err
    assert captured.out == (
        "::warning file=src/git_stage_batch/example.py,"
        "title=Dead code in intermediate stack layer,line=42::"
        "unused function 'unused_helper' (100%25 confidence)\n"
    )


def test_advisory_mode_warns_for_stale_exception(monkeypatch, capsys):
    allowed = check_dead_code._allowed(
        "src/git_stage_batch/example.py",
        "function",
        "indirect_helper",
        "Called by generated code.",
    )
    monkeypatch.setattr(check_dead_code, "ALLOWED_FINDINGS", (allowed,))
    monkeypatch.setattr(check_dead_code, "find_unused_code", lambda: [])

    assert check_dead_code.main(["--advisory"]) == 0

    captured = capsys.readouterr()
    assert "stale or changed dead-code exception" in captured.err
    assert "::warning file=src/git_stage_batch/example.py" in captured.out
    assert "title=Dead-code exception in intermediate stack layer" in captured.out


def test_warning_escapes_properties_separately_from_message_data(capsys):
    check_dead_code._emit_warning(
        path="src/git_stage_batch/example,one.py",
        line=7,
        title="Dead code: advisory",
        message="100% confidence: unused, for now\nreview the stack",
    )

    assert capsys.readouterr().out == (
        "::warning file=src/git_stage_batch/example%2Cone.py,"
        "title=Dead code%3A advisory,line=7::"
        "100%25 confidence: unused, for now%0Areview the stack\n"
    )

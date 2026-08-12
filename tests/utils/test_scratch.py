"""Tests for disposable scratch-directory selection."""

from __future__ import annotations

from pathlib import Path

from git_stage_batch.utils import scratch


def _clear_scratch_environment(monkeypatch) -> None:
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.delenv(variable, raising=False)


def test_default_scratch_parent_reads_each_environment_override(monkeypatch) -> None:
    """Each call should use the first current nonempty standard override."""
    _clear_scratch_environment(monkeypatch)
    monkeypatch.setenv("TMP", "/scratch/tmp")
    assert scratch.default_scratch_parent() == Path("/scratch/tmp")

    monkeypatch.setenv("TEMP", "/scratch/temp")
    assert scratch.default_scratch_parent() == Path("/scratch/temp")

    monkeypatch.setenv("TMPDIR", "/scratch/tmpdir")
    assert scratch.default_scratch_parent() == Path("/scratch/tmpdir")

    monkeypatch.setenv("TMPDIR", "")
    assert scratch.default_scratch_parent() == Path("/scratch/temp")


def test_default_scratch_parent_uses_var_tmp_on_linux(monkeypatch) -> None:
    """Linux should keep large default scratch data off the usual /tmp mount."""
    _clear_scratch_environment(monkeypatch)
    monkeypatch.setattr(scratch.sys, "platform", "linux")

    assert scratch.default_scratch_parent() == Path("/var/tmp")


def test_default_scratch_parent_defers_to_tempfile_off_linux(monkeypatch) -> None:
    """Other platforms without an override should retain tempfile policy."""
    _clear_scratch_environment(monkeypatch)
    monkeypatch.setattr(scratch.sys, "platform", "darwin")

    assert scratch.default_scratch_parent() is None

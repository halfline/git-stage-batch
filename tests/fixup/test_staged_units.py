"""Tests for exact staged fixup-unit discovery."""

from __future__ import annotations

import gc
import stat
import subprocess
import tracemalloc

import pytest

from git_stage_batch.fixup.staged_units import acquire_staged_fixup_units


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def staged_unit_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    source = tmp_path / "file.txt"
    source.write_text("one\ntwo\n")
    binary = tmp_path / "asset.bin"
    binary.write_bytes(b"old\0content")
    _git("add", ".")
    _git("commit", "-m", "Base")
    return tmp_path, source, binary


def test_existing_file_addition_is_a_supported_text_unit(staged_unit_repo):
    _repo, source, _binary = staged_unit_repo
    source.write_text("one\ninserted\ntwo\n")
    _git("add", "file.txt")

    with acquire_staged_fixup_units() as units:
        assert len(units) == 1
        assert units[0].kind == "text-addition"
        assert units[0].is_supported_text is True
        assert units[0].anchor_line_numbers == (1, 2)


def test_staged_patch_buffer_is_scoped(staged_unit_repo):
    _repo, source, _binary = staged_unit_repo
    source.write_text("one\ninserted\ntwo\n")
    _git("add", "file.txt")

    with acquire_staged_fixup_units() as units:
        patch_buffer = units[0].patch_buffer
        assert patch_buffer is not None
        assert patch_buffer.byte_count > 0

    with pytest.raises(ValueError, match="closed"):
        next(patch_buffer.byte_chunks())


def test_whole_file_addition_is_explicitly_unsupported(staged_unit_repo):
    repo, _source, _binary = staged_unit_repo
    (repo / "new.txt").write_text("new\n")
    _git("add", "new.txt")

    with acquire_staged_fixup_units() as units:
        assert len(units) == 1
        assert units[0].kind == "text-file-addition"
        assert units[0].unsupported_reason == "whole-file-addition"


def test_text_file_deletion_is_explicitly_unsupported(staged_unit_repo):
    _repo, source, _binary = staged_unit_repo
    source.unlink()
    _git("add", "-u", "file.txt")

    with acquire_staged_fixup_units() as units:
        assert len(units) == 1
        assert units[0].kind == "text-file-deletion"
        assert units[0].unsupported_reason == "whole-file-deletion"


def test_binary_change_is_explicitly_unsupported(staged_unit_repo):
    _repo, _source, binary = staged_unit_repo
    binary.write_bytes(b"new\0content")
    _git("add", "asset.bin")

    with acquire_staged_fixup_units() as units:
        assert len(units) == 1
        assert units[0].kind == "binary"
        assert units[0].unsupported_reason == "binary-change"


def test_mode_change_is_explicitly_unsupported(staged_unit_repo):
    _repo, source, _binary = staged_unit_repo
    source.chmod(source.stat().st_mode | stat.S_IXUSR)
    _git("add", "file.txt")

    with acquire_staged_fixup_units() as units:
        assert len(units) == 1
        assert units[0].kind == "mode"
        assert units[0].unsupported_reason == "file-mode-change"


def test_rename_is_not_silently_analyzed(staged_unit_repo):
    repo, source, _binary = staged_unit_repo
    renamed = repo / "renamed.txt"
    source.rename(renamed)
    _git("add", "-A")

    with acquire_staged_fixup_units() as units:
        assert any(unit.kind == "rename" for unit in units)
        text_units = [unit for unit in units if unit.patch_buffer is not None]
        assert not text_units


def test_renamed_content_is_not_attributed_as_an_ordinary_hunk(staged_unit_repo):
    repo, source, _binary = staged_unit_repo
    original_lines = [f"line {number}\n" for number in range(1, 11)]
    source.write_text("".join(original_lines))
    _git("commit", "-am", "Expand file")

    renamed = repo / "renamed.txt"
    source.rename(renamed)
    changed_lines = list(original_lines)
    changed_lines[4] = "line five changed\n"
    renamed.write_text("".join(changed_lines))
    _git("add", "-A")

    with acquire_staged_fixup_units() as units:
        assert any(unit.kind == "rename" for unit in units)
        text_units = [unit for unit in units if unit.patch_buffer is not None]
        assert text_units
        assert all(
            unit.unsupported_reason == "rename-with-content" for unit in text_units
        )


def test_discovery_avoids_line_scale_python_heap(staged_unit_repo):
    """Large staged hunks stay in bounded, mmap-capable patch buffers."""
    repo, _source, _binary = staged_unit_repo
    line = b"x" * 511 + b"\n"
    heap_peaks: list[int] = []

    for line_count in (4096, 32768):
        path = repo / f"large-{line_count}.txt"
        path.write_bytes(line * line_count)
        _git("add", "--", path.name)

        gc.collect()
        tracemalloc.start()
        try:
            with acquire_staged_fixup_units() as units:
                assert len(units) == 1
                assert units[0].kind == "text-file-addition"
                assert units[0].patch_buffer is not None
                assert units[0].patch_buffer.byte_count > len(line) * line_count
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)

        _git("rm", "--cached", "--", path.name)
        path.unlink()

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 64 * 1024

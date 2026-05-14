"""Tests for mapped matcher storage primitives."""

from __future__ import annotations

import os

import pytest

from git_stage_batch.batch.match_storage import MatcherWorkspace
from git_stage_batch.utils.mapped_storage import MappedIntVector, MappedRecordVector


def _open_fd_count() -> int | None:
    fd_path = "/proc/self/fd"
    if not os.path.isdir(fd_path):
        return None
    return len(os.listdir(fd_path))


def test_mapped_int_vector_get_set_fill_and_close():
    """Mapped integer vectors expose fixed-width unsigned slots."""
    vector = MappedIntVector(4, width=4, fill=7)

    assert vector.typecode == "I"
    assert list(vector) == [7, 7, 7, 7]

    vector[1] = 9
    assert vector[1] == 9

    vector.fill(3)
    assert list(vector) == [3, 3, 3, 3]

    with pytest.raises(OverflowError):
        vector[0] = -1

    vector.close()
    vector.close()
    with pytest.raises(ValueError, match="closed"):
        vector[0]


def test_mapped_int_vector_uses_64_bit_slots():
    """Mapped integer vectors store values past the 32-bit range."""
    value = (1 << 40) + 3

    with MappedIntVector(1, width=8) as vector:
        assert vector.typecode == "Q"
        vector[0] = value
        assert vector[0] == value


def test_mapped_record_vector_append_and_indexed_write():
    """Mapped record vectors support append and pre-sized writes."""
    records = MappedRecordVector(3, "QQ")

    records.append((1, 2))
    records.append((3, 4))
    assert records[0] == (1, 2)
    assert list(records) == [(1, 2), (3, 4)]

    records[1] = (5, 6)
    assert records[1] == (5, 6)

    with pytest.raises(IndexError):
        records[2]

    records.close()
    with pytest.raises(ValueError, match="closed"):
        len(records)


def test_mapped_record_vector_can_start_presized():
    """Pre-sized record vectors allow indexed population."""
    with MappedRecordVector(2, "QQ", length=2) as records:
        records[0] = (10, 20)
        records[1] = (30, 40)
        assert list(records) == [(10, 20), (30, 40)]


def test_matcher_workspace_tracks_and_closes_resources():
    """Matcher workspaces close all vectors they allocate."""
    workspace = MatcherWorkspace()
    vector = workspace.int_vector(2, width=4, fill=1)
    records = workspace.record_vector(1, "QQ")
    records.append((2, 3))

    assert workspace.current_bytes == vector.byte_count + records.byte_count
    assert workspace.high_water_bytes == workspace.current_bytes

    workspace.close_resource(vector)
    assert vector.closed
    assert workspace.current_bytes == records.byte_count

    workspace.close()
    assert records.closed
    assert workspace.current_bytes == 0


def test_repeated_vector_open_close_does_not_leak_file_descriptors():
    """Mapped vectors close their temporary file descriptors."""
    before = _open_fd_count()

    for _ in range(25):
        with MappedIntVector(8, width=8) as vector:
            vector.fill(4)

    after = _open_fd_count()
    if before is not None and after is not None:
        assert after <= before + 2

"""Tests for invocation-private file-job artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import stat

import pytest

import git_stage_batch.core.mapped_storage as mapped_storage_module
from git_stage_batch.batch.line_matching.line_mapping import LineMapping
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.utils.file_job_workspace import FileJobWorkspace


@dataclass
class _MetadataRecord:
    marker: int


class _MetadataTuple(tuple):
    pass


class _MetadataBase:
    __slots__ = ("hidden",)


@dataclass(slots=True)
class _SlottedMetadataRecord(_MetadataBase):
    marker: int


@dataclass
class _ListMetadata(list):
    __slots__ = ()


@dataclass
class _CustomPickleMetadata:
    marker: int

    def __reduce__(self):
        return bytes, (b"content",)


class _CustomPickleMetadataMarker(Enum):
    ONE = 1

    def __reduce_ex__(self, _protocol):
        return bytes, (b"content",)


class _RecursiveMetadataMarker(Enum):
    ONE = 1


def test_workspace_owns_deterministic_job_paths_and_cleans_up(tmp_path):
    """Job directories should be ordinal-based and removed with the workspace."""
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        root = workspace.root
        first_job = workspace.job_directory(7)
        repeated_job = workspace.job_directory(7)
        scratch = workspace.scratch_directory(7)
        first_output = workspace.output_path(7, "result.json")
        second_output = workspace.output_path(7, "result.json")

        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert first_job == root / "jobs" / "00000007"
        assert repeated_job == first_job
        assert scratch == first_job / "scratch"
        assert first_output.parent == first_job / "outputs"
        assert second_output.parent == first_job / "outputs"
        assert first_output != second_output

    assert not root.exists()


@pytest.mark.parametrize(
    "name",
    (
        "",
        ".",
        "..",
        "../outside",
        "nested/artifact",
        "/absolute",
    ),
)
def test_workspace_rejects_artifact_path_traversal(tmp_path, name):
    """Artifact names must not escape or introduce path-derived directories."""
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        with pytest.raises(ValueError):
            workspace.artifact_path(0, name)


def test_workspace_rejects_symlinked_job_directory_escape(tmp_path):
    """Ordinal paths should be validated before creating escaped directories."""
    outside = tmp_path / "outside"
    outside.mkdir()
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        (workspace.root / "jobs").symlink_to(
            outside,
            target_is_directory=True,
        )

        with pytest.raises(
            ValueError,
            match="must stay inside",
        ):
            workspace.scratch_directory(0)

    assert list(outside.iterdir()) == []


def test_workspace_streams_buffer_without_materializing_it(tmp_path, monkeypatch):
    """Buffer artifacts should use chunk streaming rather than to_bytes."""
    source = LineBuffer.from_chunks(
        (b"line\n" for _ in range(2_000)),
        spool_dir=tmp_path,
    )
    try:
        monkeypatch.setattr(
            LineBuffer,
            "to_bytes",
            lambda self: (_ for _ in ()).throw(
                AssertionError("buffer was materialized")
            ),
        )
        with FileJobWorkspace(parent_directory=tmp_path) as workspace:
            artifact = workspace.write_buffer(0, "input.patch", source)

            with workspace.read_buffer(
                artifact,
                spool_dir=workspace.scratch_directory(0),
            ) as written:
                assert len(written) == 2_000
    finally:
        source.close()


def test_workspace_metadata_reads_reject_external_symlink_alias(tmp_path):
    """Worker output aliases should not redirect parent artifact reads."""
    outside = tmp_path / "outside"
    outside.write_text('{"outside":true}\n', encoding="utf-8")
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        output = workspace.output_path(0, "result.json")
        output.symlink_to(outside)

        with pytest.raises(ValueError, match="must stay inside"):
            workspace.read_json(output)


def test_workspace_metadata_reads_reject_internal_symlink_alias(tmp_path):
    """Even in-workspace aliases should not substitute worker output."""
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        artifact = workspace.write_json(0, "input.json", {"content": True})
        output = workspace.output_path(0, "result.json")
        output.symlink_to(artifact)

        with pytest.raises(ValueError, match="regular file"):
            workspace.read_json(output)


def test_workspace_supports_private_metadata_formats(tmp_path):
    """JSON, JSONL, and pickle metadata should round trip inside the workspace."""
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        json_path = workspace.write_json(1, "metadata.json", {"name": "one"})
        jsonl_path = workspace.write_jsonl(
            1,
            "records.jsonl",
            ({"ordinal": ordinal} for ordinal in range(3)),
        )
        pickle_path = workspace.write_pickle(
            1,
            "metadata.pickle",
            {"paths": (Path("one"), Path("two"))},
        )

        assert workspace.read_json(json_path) == {"name": "one"}
        assert list(workspace.stream_jsonl(jsonl_path)) == [
            {"ordinal": 0},
            {"ordinal": 1},
            {"ordinal": 2},
        ]
        assert workspace.read_pickle(pickle_path) == {
            "paths": (Path("one"), Path("two"))
        }


def test_workspace_json_formats_preserve_surrogateescaped_paths(tmp_path):
    """Private JSON metadata should support arbitrary Git path bytes."""
    file_path = "invalid-\udcff.txt"
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        json_path = workspace.write_json(
            1,
            "metadata.json",
            {"file_path": file_path},
        )
        jsonl_path = workspace.write_jsonl(
            1,
            "records.jsonl",
            ({"file_path": file_path},),
        )

        assert workspace.read_json(json_path) == {"file_path": file_path}
        assert list(workspace.stream_jsonl(jsonl_path)) == [
            {"file_path": file_path}
        ]


def test_workspace_pickle_rejects_content_and_resource_graphs(tmp_path):
    """Private pickle metadata must not become a content transport."""
    with (
        FileJobWorkspace(parent_directory=tmp_path) as workspace,
        LineBuffer.from_bytes(b"content") as buffer,
        LineMapping([0], [0]) as mapping,
    ):
        for value in (
            b"content",
            {"lines": [b"content"]},
            buffer,
            mapping,
        ):
            with pytest.raises(TypeError):
                workspace.write_pickle(0, "metadata.pickle", value)


def test_workspace_pickle_rejects_hidden_instance_state(tmp_path):
    """Private metadata must not hide content in undeclared pickle state."""
    record = _MetadataRecord(1)
    record.hidden = b"content"
    tuple_value = _MetadataTuple((1,))
    tuple_value.hidden = b"content"
    slotted_value = _SlottedMetadataRecord(1)
    slotted_value.hidden = b"content"
    list_value = _ListMetadata()
    list_value.append(b"content")

    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        for value in (record, tuple_value, slotted_value, list_value):
            with pytest.raises(TypeError):
                workspace.write_pickle(0, "metadata.pickle", value)


def test_workspace_pickle_rejects_custom_pickle_behavior(tmp_path):
    """Private metadata serialization must match the validated object graph."""
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        for value in (
            _CustomPickleMetadata(1),
            _CustomPickleMetadataMarker.ONE,
        ):
            with pytest.raises(TypeError, match="custom pickle behavior"):
                workspace.write_pickle(0, "metadata.pickle", value)


def test_workspace_pickle_rejects_recursive_enum(tmp_path, monkeypatch):
    """Enum value cycles should fail before pickle traversal."""
    monkeypatch.setattr(
        _RecursiveMetadataMarker.ONE,
        "_value_",
        _RecursiveMetadataMarker.ONE,
    )

    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        with pytest.raises(TypeError, match="recursive value"):
            workspace.write_pickle(
                0,
                "metadata.pickle",
                _RecursiveMetadataMarker.ONE,
            )


def test_workspace_with_relative_parent_cleans_up_after_cwd_change(
    tmp_path,
    monkeypatch,
):
    """Cleanup should not depend on the cwd used to create the workspace."""
    parent = tmp_path / "workspaces"
    parent.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)
    workspace = FileJobWorkspace(parent_directory="workspaces")
    root = workspace.root

    monkeypatch.chdir(elsewhere)
    workspace.close()

    assert not root.exists()


def test_workspace_cleanup_can_be_retried_after_failure(tmp_path, monkeypatch):
    """A failed removal should not permanently mark the workspace closed."""
    workspace = FileJobWorkspace(parent_directory=tmp_path)
    root = workspace.root
    real_cleanup = workspace._temporary_directory.cleanup
    cleanup_calls = 0

    def flaky_cleanup():
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise OSError("cleanup interrupted")
        real_cleanup()

    monkeypatch.setattr(
        workspace._temporary_directory,
        "cleanup",
        flaky_cleanup,
    )

    with pytest.raises(OSError, match="cleanup interrupted"):
        workspace.close()

    assert root.exists()
    assert workspace.job_directory(1).exists()

    workspace.close()

    assert cleanup_calls == 2
    assert not root.exists()


def test_path_backed_line_buffer_spools_indexes_inside_job_scratch(
    tmp_path,
    monkeypatch,
):
    """Path-backed line indexes should honor the invocation scratch directory."""
    temporary_directories = []
    real_temporary_file = mapped_storage_module._temporary_file

    def recording_temporary_file(spool_dir=None):
        temporary_directories.append(spool_dir)
        return real_temporary_file(spool_dir)

    monkeypatch.setattr(
        mapped_storage_module,
        "_temporary_file",
        recording_temporary_file,
    )

    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        artifact = workspace.write_buffer(
            2,
            "large.txt",
            (b"unchanged line\n" for _ in range(2_000)),
        )
        scratch = workspace.scratch_directory(2)
        with LineBuffer.from_path(artifact, spool_dir=scratch) as buffer:
            assert len(buffer) == 2_000

        assert temporary_directories
        assert all(
            directory is not None
            and directory.resolve() == scratch.resolve()
            for directory in temporary_directories
        )


def test_workspace_cleanup_runs_after_keyboard_interrupt(tmp_path):
    """Invocation artifacts should be removed when the owner is interrupted."""
    with pytest.raises(KeyboardInterrupt):
        with FileJobWorkspace(parent_directory=tmp_path) as workspace:
            root = workspace.root
            workspace.write_json(0, "metadata.json", {"ready": True})
            raise KeyboardInterrupt

    assert not root.exists()

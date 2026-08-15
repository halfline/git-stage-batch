"""Regressions for syntax context lost during batch-source remapping."""

from __future__ import annotations

import base64
from contextlib import ExitStack
import json
from pathlib import Path

from git_stage_batch.batch.ownership.metadata_loading import (
    ownership_from_metadata_dict,
)
from git_stage_batch.batch.ownership.merging import merge_batch_ownership
from git_stage_batch.batch.merge.merge import (
    merge_batch_from_line_sequences_as_buffer,
)
from git_stage_batch.batch.realized_file_content import (
    build_realized_buffer_from_lines,
)
from git_stage_batch.core.buffer import LineBuffer


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def _realize_fixture(name: str) -> tuple[str, bytes]:
    fixture = json.loads((FIXTURE_ROOT / name).read_text())
    blob_contents = {
        object_id: _decode(content)
        for object_id, content in fixture["blob_contents"].items()
    }
    metadata_parts = (
        fixture["metadata_parts"]
        if "metadata_parts" in fixture
        else [fixture["metadata"]]
    )
    deletion_blob_ids = {
        deletion["blob"]
        for metadata in metadata_parts
        for deletion in metadata["deletions"]
    }

    with ExitStack() as resources:
        deletion_buffers = {
            object_id: resources.enter_context(
                LineBuffer.from_bytes(blob_contents[object_id])
            )
            for object_id in deletion_blob_ids
        }
        ownership_parts = [
            ownership_from_metadata_dict(
                metadata,
                blob_contents=blob_contents,
                deletion_blob_buffers=deletion_buffers,
            )
            for metadata in metadata_parts
        ]
        ownership = ownership_parts[0]
        for next_ownership in ownership_parts[1:]:
            ownership = merge_batch_ownership(ownership, next_ownership)
        base = resources.enter_context(LineBuffer.from_bytes(_decode(fixture["base"])))
        source = resources.enter_context(
            LineBuffer.from_bytes(_decode(fixture["batch_source"]))
        )
        realized = resources.enter_context(
            build_realized_buffer_from_lines(base, source, ownership)
        )

        return fixture["path"], realized.to_bytes()


def _merge_fixture(name: str) -> tuple[str, bytes]:
    fixture = json.loads((FIXTURE_ROOT / name).read_text())
    blob_contents = {
        object_id: _decode(content)
        for object_id, content in fixture["blob_contents"].items()
    }

    with ExitStack() as resources:
        deletion_buffers = {
            deletion["blob"]: resources.enter_context(
                LineBuffer.from_bytes(blob_contents[deletion["blob"]])
            )
            for deletion in fixture["metadata"]["deletions"]
        }
        ownership = ownership_from_metadata_dict(
            fixture["metadata"],
            blob_contents=blob_contents,
            deletion_blob_buffers=deletion_buffers,
        )
        source = resources.enter_context(
            LineBuffer.from_bytes(_decode(fixture["batch_source"]))
        )
        target = resources.enter_context(
            LineBuffer.from_bytes(_decode(fixture["target"]))
        )
        merged = resources.enter_context(
            merge_batch_from_line_sequences_as_buffer(source, ownership, target)
        )

        return fixture["path"], merged.to_bytes()


def test_owned_replacement_keeps_preceding_call_delimiter():
    """A replacement must not consume an adjacent unchanged closing paren."""
    path, realized = _realize_fixture("remapping_owned_replacement_delimiter.json")

    compile(realized.decode(), path, "exec")


def test_mapped_target_index_keeps_constructor_delimiter():
    """An adjacent insertion must not consume the preceding constructor paren."""
    path, realized = _realize_fixture("remapping_mapped_target_delimiter.json")

    compile(realized.decode(), path, "exec")


def test_merged_source_claims_keep_preceding_continue():
    """Merging adjacent claim sets must retain an unowned guard body."""
    path, realized = _realize_fixture("remapping_adjacent_guard_context.json")

    compile(realized.decode(), path, "exec")


def test_copied_cleanup_context_does_not_block_owned_binary_replay():
    """Copied cleanup context must not make the owned binary edits stale."""
    path, merged = _merge_fixture("remapping_copied_cleanup_context.json")

    compile(merged.decode(), path, "exec")


def test_cleanup_wrapper_preserves_predecessor_executor_indentation():
    """Wrapping cleanup must not duplicate or misindent the predecessor body."""
    fixture_name = "remapping_cleanup_predecessor_indentation.json"
    path, realized = _realize_fixture(fixture_name)
    fixture = json.loads((FIXTURE_ROOT / fixture_name).read_text())

    compile(realized.decode(), path, "exec")
    assert realized == _decode(fixture["batch_source"])

"""Tests for scoped ownership metadata loading."""

from git_stage_batch.batch.ownership import metadata_loading
from git_stage_batch.core.buffer import LineBuffer


def test_deletion_content_uses_the_requested_spool_directory(
    tmp_path,
    monkeypatch,
):
    """Worker-owned deletion buffers should stay in invocation scratch."""
    calls = []
    buffer = LineBuffer.from_bytes(b"deleted\n", spool_dir=tmp_path)

    def load_blob(blob_sha, *, spool_dir=None):
        calls.append((blob_sha, spool_dir))
        return buffer

    monkeypatch.setattr(
        metadata_loading,
        "load_git_blob_as_buffer",
        load_blob,
    )
    monkeypatch.setattr(
        metadata_loading,
        "read_git_blobs_as_bytes",
        lambda object_ids: {},
    )

    acquired = metadata_loading.acquire_ownership_for_metadata_dict(
        {
            "deletions": [
                {
                    "blob": "a" * 40,
                    "after_source_line": 1,
                }
            ]
        },
        spool_dir=tmp_path,
    )
    acquired.close()

    assert calls == [("a" * 40, tmp_path)]

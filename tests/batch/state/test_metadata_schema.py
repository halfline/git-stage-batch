"""Tests for versioned batch metadata parsing and serialization."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from git_stage_batch.batch.state.metadata_schema import (
    CURRENT_BATCH_METADATA_SCHEMA_VERSION,
    decode_batch_metadata,
    encode_batch_metadata,
    metadata_from_application_dict,
)
from git_stage_batch.batch.state.compatibility_metadata import write_file_backed_batch_metadata
from git_stage_batch.exceptions import BatchMetadataError


def _oid(character: str = "a") -> str:
    return character * 40


@pytest.fixture(autouse=True)
def sha1_object_format(monkeypatch):
    monkeypatch.setattr(
        "git_stage_batch.batch.state.metadata_schema.object_id_hex_length",
        lambda: 40,
    )


def _v1_metadata() -> dict:
    return {
        "schema_version": 1,
        "revision": "revision-1",
        "batch": "feature",
        "note": "Feature work",
        "created_at": "2026-07-10T12:00:00+00:00",
        "baseline": _oid("a"),
        "content_ref": "refs/git-stage-batch/batches/feature",
        "content_commit": _oid("b"),
        "files": {
            "src/example.py": {
                "batch_source_commit": _oid("c"),
                "mode": "100644",
                "presence_claims": [{"source_lines": ["1-3"]}],
                "deletions": [],
            }
        },
    }


def test_v1_round_trip_is_deterministic_and_immutable():
    model = decode_batch_metadata(_v1_metadata(), expected_batch="feature")

    encoded = encode_batch_metadata(model)
    reparsed = decode_batch_metadata(encoded, expected_batch="feature")

    assert reparsed == model
    assert encoded == encode_batch_metadata(reparsed)
    with pytest.raises(FrozenInstanceError):
        model.note = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        model.files[0].values["mode"] = "100755"  # type: ignore[index]


def test_unversioned_state_metadata_migrates_deterministically():
    legacy = {
        "batch": "feature",
        "note": "Legacy",
        "created_at": "2026-07-10T12:00:00+00:00",
        "baseline_commit": _oid("a"),
        "content_ref": "refs/git-stage-batch/batches/feature",
        "content_commit": _oid("b"),
        "files": {},
    }

    first = decode_batch_metadata(legacy, expected_batch="feature")
    second = decode_batch_metadata(dict(reversed(list(legacy.items()))), expected_batch="feature")

    assert first == second
    assert first.revision.startswith("v0-")
    assert first.baseline == _oid("a")


def test_application_mapping_serializes_only_current_schema():
    model = metadata_from_application_dict(
        "feature",
        {
            "revision": "revision-1",
            "note": "Work",
            "created_at": "2026-07-10T12:00:00+00:00",
            "baseline": _oid("a"),
            "files": {},
        },
    )

    stored = json.loads(encode_batch_metadata(model))

    assert stored["schema_version"] == CURRENT_BATCH_METADATA_SCHEMA_VERSION
    assert stored["batch"] == "feature"
    assert "baseline_commit" not in stored


def test_file_backed_v0_migration_keeps_recovery_copy(tmp_path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    legacy = {
        "note": "Legacy",
        "created_at": "2026-07-10T12:00:00Z",
        "baseline": _oid("a"),
        "files": {},
    }
    original = json.dumps(legacy)
    metadata_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        "git_stage_batch.batch.state.compatibility_metadata.get_batch_metadata_file_path",
        lambda _batch_name: metadata_path,
    )

    write_file_backed_batch_metadata(
        "feature",
        decode_batch_metadata(legacy, expected_batch="feature").to_application_dict(),
    )

    assert metadata_path.with_name("metadata.v0.json").read_text() == original
    assert json.loads(metadata_path.read_text())["schema_version"] == 1


def test_file_backed_writer_refuses_to_replace_future_schema(tmp_path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    future = _v1_metadata()
    future["schema_version"] = CURRENT_BATCH_METADATA_SCHEMA_VERSION + 1
    original = json.dumps(future)
    metadata_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        "git_stage_batch.batch.state.compatibility_metadata.get_batch_metadata_file_path",
        lambda _batch_name: metadata_path,
    )

    with pytest.raises(BatchMetadataError, match="Upgrade git-stage-batch"):
        write_file_backed_batch_metadata("feature", {})

    assert metadata_path.read_text() == original


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version="1"), "must be an integer"),
        (lambda data: data.update(extra=True), "unknown top-level"),
        (lambda data: data.pop("baseline"), "missing required"),
        (lambda data: data.update(batch="other"), "identifies batch 'other'"),
        (lambda data: data.update(files=[]), "'files' must be an object"),
        (lambda data: data.update(baseline="not-an-oid"), "object ID"),
    ],
)
def test_v1_rejects_malformed_top_level_metadata(mutation, message):
    data = _v1_metadata()
    mutation(data)

    with pytest.raises(BatchMetadataError, match=message):
        decode_batch_metadata(data, expected_batch="feature")


def test_future_schema_reports_upgrade_without_rewriting():
    data = _v1_metadata()
    data["schema_version"] = CURRENT_BATCH_METADATA_SCHEMA_VERSION + 1

    with pytest.raises(BatchMetadataError, match="Upgrade git-stage-batch"):
        decode_batch_metadata(data, expected_batch="feature")


@pytest.mark.parametrize("payload", ["", "{", "[]", "null", "42"])
def test_decoder_rejects_truncated_or_non_object_json(payload):
    with pytest.raises(BatchMetadataError, match="valid JSON|top-level"):
        decode_batch_metadata(payload, expected_batch="feature")


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/absolute", "repository-relative"),
        ("parent/../escape", "repository-relative"),
        ("double//slash", "repository-relative"),
        ("nul\x00path", "without NUL"),
    ],
)
def test_v1_rejects_invalid_repository_paths(path, message):
    data = _v1_metadata()
    data["files"] = {path: {}}

    with pytest.raises(BatchMetadataError, match=message):
        decode_batch_metadata(data, expected_batch="feature")


def test_v1_rejects_duplicate_presence_claims():
    data = _v1_metadata()
    data["files"]["src/example.py"]["presence_claims"] *= 2

    with pytest.raises(BatchMetadataError, match="duplicate presence claims"):
        decode_batch_metadata(data, expected_batch="feature")


def test_v1_rejects_replacement_with_out_of_range_deletion_index():
    data = _v1_metadata()
    file_metadata = data["files"]["src/example.py"]
    file_metadata["deletions"] = [
        {"after_source_line": 1, "blob": _oid("d")}
    ]
    file_metadata["replacement_units"] = [
        {"presence_lines": ["1"], "deletion_indices": [1]}
    ]

    with pytest.raises(BatchMetadataError, match="replacement deletion indices"):
        decode_batch_metadata(data, expected_batch="feature")


def test_v1_rejects_unknown_nested_claim_field():
    data = _v1_metadata()
    data["files"]["src/example.py"]["presence_claims"][0]["typo"] = True

    with pytest.raises(BatchMetadataError, match="unknown field"):
        decode_batch_metadata(data, expected_batch="feature")


@pytest.mark.parametrize("line_range", ["0", "3-1", "one", "1--2"])
def test_v1_rejects_invalid_line_ranges(line_range):
    data = _v1_metadata()
    data["files"]["src/example.py"]["presence_claims"] = [
        {"source_lines": [line_range]}
    ]

    with pytest.raises(BatchMetadataError, match="range|non-positive"):
        decode_batch_metadata(data, expected_batch="feature")

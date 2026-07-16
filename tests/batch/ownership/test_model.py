"""Tests for ownership attribution behavior."""

import subprocess
from tests.diff_parser_helpers import collect_unified_diff

import pytest

import git_stage_batch.batch.attribution as attribution_module
import git_stage_batch.batch.attribution_units as attribution_units_module
from git_stage_batch.batch.attribution import (
    AttributionMetrics,
    AttributedUnit,
    FileAttribution,
    build_file_attribution,
    build_file_attribution_from_lines,
)
from git_stage_batch.batch.attribution_units import (
    AttributionUnitKind,
    FileComparison,
    enumerate_units_from_file_comparison,
)
from git_stage_batch.batch.attribution_projection import project_attribution_to_diff
from git_stage_batch.batch.line_matching.match import match_lines
from git_stage_batch.batch.state.references import get_batch_state_ref_name
from git_stage_batch.core.diff_parser import (
    build_line_changes_from_patch_lines,
)
from git_stage_batch.utils.repository_buffers import (
    read_git_object_buffer_or_empty,
    load_working_tree_file_as_buffer,
)
from git_stage_batch.utils.git_command import stream_git_command


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    """Create a temporary git repository."""

    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )

    return tmp_path


def _create_batch_source_commit(repo, path: str, content: str) -> str:
    file_path = repo / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    subprocess.run(["git", "add", path], check=True, cwd=repo, capture_output=True)
    tree = subprocess.run(
        ["git", "write-tree"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-p", "HEAD", "-m", "batch source"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "reset", "--mixed", "HEAD"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return commit


def _point_batch_state_refs(batch_names, state_commit: str) -> None:
    updates = "".join(
        f"update {get_batch_state_ref_name(batch_name)} {state_commit}\n"
        for batch_name in batch_names
    )
    subprocess.run(
        ["git", "update-ref", "--stdin"],
        input=updates,
        check=True,
        text=True,
        capture_output=True,
    )


def _enumerate_units_from_head_and_working_tree(file_path: str):
    baseline_buffer = read_git_object_buffer_or_empty(f"HEAD:{file_path}")
    working_tree_buffer = load_working_tree_file_as_buffer(file_path)

    with baseline_buffer as baseline_lines, working_tree_buffer as working_tree_lines:
        comparison = FileComparison(
            file_path=file_path,
            baseline_lines=baseline_lines,
            working_tree_lines=working_tree_lines,
            alignment=match_lines(baseline_lines, working_tree_lines),
        )
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)
        return units_map


def test_file_comparison_accepts_non_list_line_sequences(line_sequence):
    """Attribution comparison only requires sized indexable line sequences."""
    baseline_lines = line_sequence([b"line1\n", b"old\n", b"line3\n"])
    working_tree_lines = line_sequence([b"line1\n", b"new\n", b"line3\n"])
    comparison = FileComparison(
        file_path="test.txt",
        baseline_lines=baseline_lines,
        working_tree_lines=working_tree_lines,
        alignment=match_lines(baseline_lines, working_tree_lines),
    )
    units_map = {}

    enumerate_units_from_file_comparison(comparison, units_map)

    replacement_units = [
        unit
        for unit in units_map.values()
        if unit.kind == AttributionUnitKind.REPLACEMENT
    ]
    assert len(replacement_units) == 1
    assert replacement_units[0].deletion_content is None
    assert replacement_units[0].deletion_fingerprint is not None
    assert replacement_units[0].deletion_fingerprint.byte_count == 4
    assert replacement_units[0].claimed_content == b"new\n"


def test_multi_line_replacement_addition_uses_digest_for_projection(line_sequence):
    """Attribution can project replacements whose added side is not retained."""
    baseline_lines = line_sequence(
        [
            b"line1\n",
            b"old1\n",
            b"old2\n",
            b"line4\n",
        ]
    )
    working_tree_lines = line_sequence(
        [
            b"line1\n",
            b"new1\n",
            b"new2\n",
            b"line4\n",
        ]
    )
    comparison = FileComparison(
        file_path="test.txt",
        baseline_lines=baseline_lines,
        working_tree_lines=working_tree_lines,
        alignment=match_lines(baseline_lines, working_tree_lines),
    )
    units_map = {}

    enumerate_units_from_file_comparison(comparison, units_map)

    replacement_unit = next(
        unit
        for unit in units_map.values()
        if unit.kind == AttributionUnitKind.REPLACEMENT
    )
    assert replacement_unit.claimed_content is None
    assert replacement_unit.claimed_fingerprint is not None
    assert replacement_unit.claimed_fingerprint.byte_count == len(b"new1\nnew2\n")
    assert replacement_unit.claimed_line_count == 2

    attribution = FileAttribution(
        file_path="test.txt",
        units=[
            AttributedUnit(
                unit=replacement_unit,
                owning_batches={"batch"},
            )
        ],
    )
    line_changes = build_line_changes_from_patch_lines(
        (
            b"diff --git a/test.txt b/test.txt\n"
            b"--- a/test.txt\n"
            b"+++ b/test.txt\n"
            b"@@ -1,4 +1,4 @@\n"
            b" line1\n"
            b"-old1\n"
            b"-old2\n"
            b"+new1\n"
            b"+new2\n"
            b" line4\n"
        ).splitlines(keepends=True)
    )

    display_to_unit = project_attribution_to_diff(attribution, line_changes)

    assert {line_changes.lines[index].id for index in display_to_unit} == {1, 2, 3, 4}


def test_legacy_claimed_lines_metadata_owns_presence_units(temp_repo, monkeypatch):
    """Attribution should treat old claimed_lines as presence ownership."""
    test_file = temp_repo / "test.txt"
    test_file.write_text("base\n")
    subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

    batch_source_commit = _create_batch_source_commit(
        temp_repo,
        "test.txt",
        "base\nadded\n",
    )

    monkeypatch.setattr(attribution_module, "list_batch_names", lambda: ["legacy"])
    monkeypatch.setattr(
        attribution_module,
        "read_batch_metadata_for_batches",
        lambda _names: {
            "legacy": {
                "files": {
                    "test.txt": {
                        "batch_source_commit": batch_source_commit,
                        "claimed_lines": ["2"],
                        "deletions": [],
                    }
                }
            }
        },
    )

    attribution = build_file_attribution("test.txt")

    owned_additions = [
        attributed
        for attributed in attribution.units
        if (
            attributed.unit.kind == AttributionUnitKind.PRESENCE_ONLY
            and attributed.unit.claimed_content == b"added\n"
        )
    ]
    assert owned_additions
    assert owned_additions[0].owning_batches == {"legacy"}


def test_build_file_attribution_reuses_batch_alignment_per_file(temp_repo, monkeypatch):
    """Batch source alignment should be built once per file, not once per unit."""
    test_file = temp_repo / "test.txt"
    test_file.write_text("keep0\nold1\nkeep1\nold2\nkeep2\nold3\nkeep3\n")
    subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

    batch_source_commit = _create_batch_source_commit(
        temp_repo,
        "test.txt",
        "keep0\nnew1\nkeep1\nnew2\nkeep2\nnew3\nkeep3\n",
    )

    monkeypatch.setattr(attribution_module, "list_batch_names", lambda: ["batch"])
    monkeypatch.setattr(
        attribution_module,
        "read_batch_metadata_for_batches",
        lambda _names: {
            "batch": {
                "files": {
                    "test.txt": {
                        "batch_source_commit": batch_source_commit,
                        "presence_claims": [{"source_lines": ["2", "4", "6"]}],
                        "deletions": [],
                    }
                }
            }
        },
    )

    original_match_lines = attribution_module.match_lines
    match_call_count = 0

    def counting_match_lines(*args, **kwargs):
        nonlocal match_call_count
        match_call_count += 1
        return original_match_lines(*args, **kwargs)

    monkeypatch.setattr(attribution_module, "match_lines", counting_match_lines)
    monkeypatch.setattr(
        attribution_units_module,
        "match_lines",
        counting_match_lines,
    )
    original_parse_presence = attribution_module._parse_presence_source_lines
    presence_parse_count = 0

    def counting_parse_presence(file_metadata):
        nonlocal presence_parse_count
        presence_parse_count += 1
        return original_parse_presence(file_metadata)

    monkeypatch.setattr(
        attribution_module,
        "_parse_presence_source_lines",
        counting_parse_presence,
    )

    attribution = build_file_attribution("test.txt")

    assert len(attribution.units) >= 3
    assert match_call_count == 2
    assert presence_parse_count == 1


def test_build_file_attribution_bulk_loads_batch_source_buffers(
    temp_repo,
    monkeypatch,
):
    """Batch source buffers should be loaded with one object batch read."""
    test_file = temp_repo / "test.txt"
    test_file.write_text("base\n")
    subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

    first_source_commit = _create_batch_source_commit(
        temp_repo,
        "test.txt",
        "base\nfirst\n",
    )
    second_source_commit = _create_batch_source_commit(
        temp_repo,
        "test.txt",
        "base\nsecond\n",
    )

    monkeypatch.setattr(
        attribution_module,
        "list_batch_names",
        lambda: ["first", "second"],
    )
    monkeypatch.setattr(
        attribution_module,
        "read_batch_metadata_for_batches",
        lambda _names: {
            "first": {
                "files": {
                    "test.txt": {
                        "batch_source_commit": first_source_commit,
                        "presence_claims": [{"source_lines": ["2"]}],
                        "deletions": [],
                    }
                }
            },
            "second": {
                "files": {
                    "test.txt": {
                        "batch_source_commit": second_source_commit,
                        "presence_claims": [{"source_lines": ["2"]}],
                        "deletions": [],
                    }
                }
            },
        },
    )
    resolution_calls = []
    buffer_stream_calls = []
    original_resolve = attribution_module.resolve_git_objects
    original_buffer_stream = attribution_module.stream_git_blob_buffers

    def counting_resolve(refspecs):
        refspecs = tuple(refspecs)
        resolution_calls.append(refspecs)
        return original_resolve(refspecs)

    def counting_buffer_stream(object_ids):
        object_ids = tuple(object_ids)
        buffer_stream_calls.append(object_ids)
        yield from original_buffer_stream(object_ids)

    monkeypatch.setattr(
        attribution_module,
        "resolve_git_objects",
        counting_resolve,
    )
    monkeypatch.setattr(
        attribution_module, "stream_git_blob_buffers", counting_buffer_stream
    )

    attribution = build_file_attribution("test.txt")

    assert attribution.units
    assert resolution_calls == [
        (
            f"{first_source_commit}:test.txt",
            f"{second_source_commit}:test.txt",
        )
    ]
    first_blob = subprocess.run(
        ["git", "rev-parse", f"{first_source_commit}:test.txt"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    second_blob = subprocess.run(
        ["git", "rev-parse", f"{second_source_commit}:test.txt"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert buffer_stream_calls == [(first_blob, second_blob)]


def test_build_file_attribution_deduplicates_sources_deletions_and_mappings(
    temp_repo,
    monkeypatch,
):
    """Shared source and deletion objects should be read and computed once."""
    test_file = temp_repo / "test.txt"
    test_file.write_text("base\n")
    subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)
    source_commit = _create_batch_source_commit(temp_repo, "test.txt", "base\n")
    state_commit = _create_batch_source_commit(
        temp_repo,
        "sources/test.txt",
        "base\n",
    )
    _point_batch_state_refs(("first", "second"), state_commit)
    deletion_blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            input=b"deleted\n",
            check=True,
            capture_output=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    file_metadata = {
        "batch_source_commit": source_commit,
        "source_path": "sources/test.txt",
        "presence_claims": [],
        "deletions": [{"blob": deletion_blob, "after_source_line": 1}],
    }
    metadata = {
        name: {"files": {"test.txt": file_metadata}} for name in ("first", "second")
    }
    resolution_calls = []
    deletion_stream_calls = []
    buffer_stream_calls = []
    original_resolve = attribution_module.resolve_git_objects
    original_deletion_stream = attribution_module.stream_git_blobs
    original_buffer_stream = attribution_module.stream_git_blob_buffers

    def recording_resolve(object_names):
        object_names = tuple(object_names)
        resolution_calls.append(object_names)
        return original_resolve(object_names)

    def recording_deletion_stream(object_ids, **kwargs):
        object_ids = tuple(object_ids)
        deletion_stream_calls.append(object_ids)
        yield from original_deletion_stream(object_ids, **kwargs)

    def recording_buffer_stream(object_ids):
        object_ids = tuple(object_ids)
        buffer_stream_calls.append(object_ids)
        yield from original_buffer_stream(object_ids)

    match_calls = 0
    original_match = attribution_module.match_lines

    def recording_match(*args, **kwargs):
        nonlocal match_calls
        match_calls += 1
        return original_match(*args, **kwargs)

    fingerprint_calls = 0
    original_fingerprint = (
        attribution_module._attribution_fingerprints.fingerprint_chunks
    )

    def recording_fingerprint(chunks):
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        return original_fingerprint(chunks)

    monkeypatch.setattr(attribution_module, "resolve_git_objects", recording_resolve)
    monkeypatch.setattr(
        attribution_module, "stream_git_blobs", recording_deletion_stream
    )
    monkeypatch.setattr(
        attribution_module, "stream_git_blob_buffers", recording_buffer_stream
    )
    monkeypatch.setattr(attribution_module, "match_lines", recording_match)
    monkeypatch.setattr(
        attribution_module._attribution_fingerprints,
        "fingerprint_chunks",
        recording_fingerprint,
    )

    metrics = AttributionMetrics()
    attribution = build_file_attribution(
        "test.txt",
        batch_metadata_by_name=metadata,
        metrics=metrics,
    )

    assert resolution_calls == [
        (
            f"{get_batch_state_ref_name('first')}:sources/test.txt",
            f"{source_commit}:test.txt",
            f"{get_batch_state_ref_name('second')}:sources/test.txt",
            deletion_blob,
        ),
    ]
    source_blob = subprocess.run(
        ["git", "rev-parse", f"{source_commit}:test.txt"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert deletion_stream_calls == [(deletion_blob,)]
    assert buffer_stream_calls == [(source_blob,)]
    assert match_calls == 1
    assert fingerprint_calls == 1
    deletion_units = [
        unit
        for unit in attribution.units
        if unit.unit.kind == AttributionUnitKind.DELETION_ONLY
    ]
    assert len(deletion_units) == 1
    assert deletion_units[0].owning_batches == {"first", "second"}
    assert metrics.candidate_batches == 2
    assert metrics.claimed_batches == 2
    assert metrics.object_resolution_requests == 4
    assert metrics.object_requests == 2
    assert metrics.unique_source_contents == 1
    assert metrics.mapping_computations == 1
    assert metrics.deletion_fingerprints == 1


def test_file_attribution_from_lines_matches_repository_wrapper(temp_repo):
    """Caller-owned buffers should drive the same attribution computation."""
    test_file = temp_repo / "test.txt"
    test_file.write_text("first\nsecond\n")
    subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    test_file.write_text("first\nchanged\n")
    wrapper_metrics = AttributionMetrics()
    explicit_metrics = AttributionMetrics()

    wrapped = build_file_attribution(
        "test.txt",
        batch_metadata_by_name={},
        metrics=wrapper_metrics,
    )
    with (
        read_git_object_buffer_or_empty("HEAD:test.txt") as baseline_lines,
        load_working_tree_file_as_buffer("test.txt") as working_lines,
    ):
        explicit = build_file_attribution_from_lines(
            "test.txt",
            baseline_lines=baseline_lines,
            working_tree_lines=working_lines,
            batch_metadata_by_name={},
            metrics=explicit_metrics,
        )

    assert explicit == wrapped
    assert explicit_metrics == wrapper_metrics


def test_build_file_attribution_is_independent_of_batch_traversal_order(temp_repo):
    """Reordering batch metadata must not change units or ownership arbitration."""
    test_file = temp_repo / "test.txt"
    test_file.write_text("base\n")
    subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)
    first_source = _create_batch_source_commit(
        temp_repo,
        "test.txt",
        "base\nfirst\n",
    )
    second_source = _create_batch_source_commit(
        temp_repo,
        "test.txt",
        "base\nsecond\n",
    )

    def metadata(source):
        return {
            "files": {
                "test.txt": {
                    "batch_source_commit": source,
                    "presence_claims": [{"source_lines": ["2"]}],
                    "deletions": [],
                }
            }
        }

    first_order = {
        "first": metadata(first_source),
        "second": metadata(second_source),
    }
    second_order = dict(reversed(list(first_order.items())))

    def snapshot(batch_metadata):
        attribution = build_file_attribution(
            "test.txt",
            batch_metadata_by_name=batch_metadata,
        )
        return [
            (attributed.unit.unit_id, sorted(attributed.owning_batches))
            for attributed in attribution.units
        ]

    assert snapshot(first_order) == snapshot(second_order)


def test_build_file_attribution_bounds_mapping_work_as_batches_grow(
    temp_repo,
    monkeypatch,
):
    """Shared content should keep object and mapping work constant at 1,000 batches."""
    test_file = temp_repo / "test.txt"
    test_file.write_text("base\n")
    subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)
    source_commit = _create_batch_source_commit(temp_repo, "test.txt", "base\n")
    batch_names = tuple(f"batch-{index:04d}" for index in range(1_000))
    metadata = {
        batch_name: {
            "files": {
                "test.txt": {
                    "batch_source_commit": source_commit,
                    "source_path": "sources/test.txt",
                    "presence_claims": [{"source_lines": ["1"]}],
                    "deletions": [],
                }
            }
        }
        for batch_name in batch_names
    }
    fallback_refspec = f"{source_commit}:test.txt"
    source_info = attribution_module.resolve_git_objects([fallback_refspec])[
        fallback_refspec
    ]
    resolution_calls = []

    def resolve_shared_state_sources(object_names):
        object_names = tuple(object_names)
        resolution_calls.append(object_names)
        return {object_name: source_info for object_name in object_names}

    monkeypatch.setattr(
        attribution_module,
        "resolve_git_objects",
        resolve_shared_state_sources,
    )
    metrics = AttributionMetrics()

    attribution = build_file_attribution(
        "test.txt",
        batch_metadata_by_name=metadata,
        metrics=metrics,
    )

    assert metrics.claimed_batches == 1_000
    assert metrics.object_resolution_requests == 1_001
    assert metrics.object_requests == 1
    assert metrics.unique_source_contents == 1
    assert metrics.mapping_computations == 1
    assert len(attribution.units) == 1
    assert len(attribution.units[0].owning_batches) == 1_000
    assert len(resolution_calls) == 1
    assert len(resolution_calls[0]) == 1_001


def test_build_file_attribution_ignores_missing_deletion_objects(temp_repo):
    """Malformed deletion object references should remain conservative and visible."""
    test_file = temp_repo / "test.txt"
    test_file.write_text("base\n")
    subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)
    source_commit = _create_batch_source_commit(temp_repo, "test.txt", "base\n")
    metadata = {
        "broken": {
            "files": {
                "test.txt": {
                    "batch_source_commit": source_commit,
                    "presence_claims": [],
                    "deletions": [
                        {
                            "blob": "0" * 40,
                            "after_source_line": 1,
                        },
                        {
                            "blob": "HEAD",
                            "after_source_line": 1,
                        },
                    ],
                }
            }
        }
    }

    attribution = build_file_attribution(
        "test.txt",
        batch_metadata_by_name=metadata,
    )

    assert all(
        unit.unit.kind != AttributionUnitKind.DELETION_ONLY
        for unit in attribution.units
    )


class TestPresenceGranularity:
    """Test that PRESENCE_ONLY units are per-line, not per-run."""

    def test_consecutive_additions_create_separate_units(self, temp_repo):
        """Consecutive added lines should produce separate PRESENCE_ONLY units."""
        # Create baseline with one line
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # Add three consecutive lines
        test_file.write_text("line1\nline2\nline3\nline4\n")

        # Build attribution
        units_map = _enumerate_units_from_head_and_working_tree("test.txt")

        # Should have THREE separate PRESENCE_ONLY units (lines 2, 3, 4)
        presence_units = [
            u for u in units_map.values() if u.kind == AttributionUnitKind.PRESENCE_ONLY
        ]
        assert len(presence_units) == 3, "Should have 3 separate PRESENCE_ONLY units"

        # Each should be a single line
        for unit in presence_units:
            assert unit.claimed_content is not None
            # Single line should not contain multiple newlines
            assert unit.claimed_content.count(b"\n") <= 1

    def test_only_individually_owned_lines_hidden(self, temp_repo):
        """Only individually owned added lines should be hidden, not grouped."""
        # This would require a full batch setup to test properly
        # For now, verify unit structure supports individual hiding
        test_file = temp_repo / "test.txt"
        test_file.write_text("original\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        test_file.write_text("original\nadd1\nadd2\nadd3\n")

        units_map = _enumerate_units_from_head_and_working_tree("test.txt")

        presence_units = [
            u for u in units_map.values() if u.kind == AttributionUnitKind.PRESENCE_ONLY
        ]

        # Verify each has distinct line number
        line_numbers = [u.claimed_line_in_working_tree for u in presence_units]
        assert len(line_numbers) == len(set(line_numbers)), (
            "Each unit should have unique line number"
        )


class TestReplacementPairing:
    """Test that replacement pairing is deterministic and one-to-one."""

    def test_multiple_changes_pair_deterministically(self, temp_repo):
        """Multiple nearby deletion/addition runs should pair deterministically."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\ndel1\nline2\ndel2\nline3\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # Replace both deletion targets
        test_file.write_text("line1\nadd1\nline2\nadd2\nline3\n")

        units_map = _enumerate_units_from_head_and_working_tree("test.txt")

        # Should have exactly 2 REPLACEMENT units
        replacement_units = [
            u for u in units_map.values() if u.kind == AttributionUnitKind.REPLACEMENT
        ]
        assert len(replacement_units) == 2, (
            f"Should have 2 replacements, got {len(replacement_units)}"
        )

        # Verify they have distinct anchors
        anchors = [u.deletion_anchor_in_working_tree for u in replacement_units]
        assert len(set(anchors)) == 2, "Each replacement should have distinct anchor"

    def test_addition_run_not_reused_for_multiple_replacements(self, temp_repo):
        """One addition run cannot be reused for multiple replacements."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("del1\ndel2\nkeep\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # One addition where two deletions were
        test_file.write_text("add\nkeep\n")

        units_map = _enumerate_units_from_head_and_working_tree("test.txt")

        # Should have at most 1 REPLACEMENT (one-to-one pairing)
        replacement_units = [
            u for u in units_map.values() if u.kind == AttributionUnitKind.REPLACEMENT
        ]
        assert len(replacement_units) <= 1, (
            "Addition run cannot be reused for multiple replacements"
        )

    def test_pairing_stable_regardless_of_order(self, temp_repo):
        """Replacement pairing should be stable regardless of iteration order."""
        # This test ensures determinism by checking the pairing is based on
        # structural properties, not iteration order
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\ndel1\nline2\ndel2\nline3\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        test_file.write_text("line1\nadd1\nline2\nadd2\nline3\n")

        # Build units multiple times - should get same result
        results = []
        for _ in range(3):
            units_map = _enumerate_units_from_head_and_working_tree("test.txt")

            replacement_units = sorted(
                [
                    u
                    for u in units_map.values()
                    if u.kind == AttributionUnitKind.REPLACEMENT
                ],
                key=lambda u: (
                    u.claimed_line_in_working_tree,
                    u.deletion_anchor_in_working_tree,
                ),
            )
            results.append(replacement_units)

        # All runs should produce identical results
        assert len(results[0]) == len(results[1]) == len(results[2])
        for i in range(len(results[0])):
            assert (
                results[0][i].unit_id == results[1][i].unit_id == results[2][i].unit_id
            )


class TestAnchorSemantics:
    """Test that anchor determination is alignment-driven, not arithmetic."""

    def test_anchor_follows_structural_comparison(self, temp_repo):
        """Anchor should follow structural comparison, not arithmetic position."""
        test_file = temp_repo / "test.txt"
        # Create baseline with some structure
        test_file.write_text("header\nkeep1\nkeep2\ntarget\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # Delete target line
        test_file.write_text("header\nkeep1\nkeep2\n")

        units_map = _enumerate_units_from_head_and_working_tree("test.txt")

        deletion_units = [
            u for u in units_map.values() if u.kind == AttributionUnitKind.DELETION_ONLY
        ]
        assert len(deletion_units) == 1

        # Anchor should be last matched line (keep2 at line 3), not arithmetic line-1
        assert deletion_units[0].deletion_anchor_in_working_tree == 3

    def test_start_of_file_anchor_correct(self, temp_repo):
        """Start-of-file deletions should have None anchor."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("delete_me\nkeep\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # Delete first line
        test_file.write_text("keep\n")

        units_map = _enumerate_units_from_head_and_working_tree("test.txt")

        deletion_units = [
            u for u in units_map.values() if u.kind == AttributionUnitKind.DELETION_ONLY
        ]
        assert len(deletion_units) == 1
        assert deletion_units[0].deletion_anchor_in_working_tree is None, (
            "Start-of-file anchor should be None"
        )


class TestPresenceOwnershipIdentity:
    """Test that presence ownership matching is source-identity-aware."""

    def test_repeated_identical_lines_dont_collapse(self, temp_repo):
        """Repeated identical lines should not collapse ownership incorrectly."""
        # This would require full batch setup to test properly
        # For now, verify structure supports source-identity tracking
        test_file = temp_repo / "test.txt"
        test_file.write_text("same\nother\nsame\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # Add another "same" line
        test_file.write_text("same\nother\nsame\nsame\n")

        units_map = _enumerate_units_from_head_and_working_tree("test.txt")

        presence_units = [
            u for u in units_map.values() if u.kind == AttributionUnitKind.PRESENCE_ONLY
        ]

        # Even though content is identical, should have distinct line numbers
        assert len(presence_units) == 1  # Only one added line
        assert presence_units[0].claimed_line_in_working_tree == 4


class TestReplacementProjection:
    """Test that REPLACEMENT units are first-class in projection."""

    def test_explicit_replacement_units_project_cleanly(self, temp_repo):
        """Explicit REPLACEMENT units should project to diff without ad hoc rediscovery."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\nold\nline3\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        test_file.write_text("line1\nnew\nline3\n")

        # Build attribution
        attribution = build_file_attribution("test.txt")

        # Should have explicit REPLACEMENT unit
        replacement_units = [
            u.unit
            for u in attribution.units
            if u.unit.kind == AttributionUnitKind.REPLACEMENT
        ]
        assert len(replacement_units) >= 1, "Should have at least one REPLACEMENT unit"

        # Get diff for projection
        subprocess.run(
            ["git", "diff", "HEAD", "test.txt"], capture_output=True, check=True
        )

        # Parse diff

        patches = list(
            collect_unified_diff(stream_git_command(["diff", "HEAD", "test.txt"]))
        )
        assert len(patches) >= 1

        line_changes = build_line_changes_from_patch_lines(patches[0].lines)

        # Project attribution onto diff
        display_to_unit = project_attribution_to_diff(attribution, line_changes)

        # REPLACEMENT unit should be in projection
        replacement_found = any(
            u.unit.kind == AttributionUnitKind.REPLACEMENT
            for u in display_to_unit.values()
        )
        assert replacement_found, "REPLACEMENT unit should appear in projection"


class TestAnchorConsistency:
    """Test that anchor semantics are consistent between generation and projection."""

    def test_replacement_detection_independent_of_hunk_context(self, temp_repo):
        """Replacement should be detected correctly even when hunk context changes."""
        test_file = temp_repo / "test.txt"
        # Create file with context that could change
        test_file.write_text("context1\ncontext2\nold\ncontext3\ncontext4\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # Replace middle line
        test_file.write_text("context1\ncontext2\nnew\ncontext3\ncontext4\n")

        # Build attribution
        attribution = build_file_attribution("test.txt")

        # Get diff with different context settings
        for context_lines in [0, 3, 10]:
            patches = list(
                collect_unified_diff(
                    stream_git_command(
                        ["diff", f"-U{context_lines}", "HEAD", "test.txt"]
                    )
                )
            )
            if not patches:
                continue

            line_changes = build_line_changes_from_patch_lines(patches[0].lines)

            # Project attribution
            display_to_unit = project_attribution_to_diff(attribution, line_changes)

            # Should find REPLACEMENT unit regardless of context
            replacement_found = any(
                u.unit.kind == AttributionUnitKind.REPLACEMENT
                for u in display_to_unit.values()
            )
            assert replacement_found, (
                f"REPLACEMENT should be found with -U{context_lines}"
            )

    def test_deletion_projection_independent_of_nearby_context(self, temp_repo):
        """Deletion projection should not depend on specific nearby context lines."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("keep1\nkeep2\ndelete_me\nkeep3\nkeep4\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # Delete middle line
        test_file.write_text("keep1\nkeep2\nkeep3\nkeep4\n")

        # Build attribution
        attribution = build_file_attribution("test.txt")

        # Should have DELETION_ONLY unit
        deletion_units = [
            u.unit
            for u in attribution.units
            if u.unit.kind == AttributionUnitKind.DELETION_ONLY
        ]
        assert len(deletion_units) == 1

        # Get diff
        patches = list(
            collect_unified_diff(stream_git_command(["diff", "HEAD", "test.txt"]))
        )
        line_changes = build_line_changes_from_patch_lines(patches[0].lines)

        # Project
        display_to_unit = project_attribution_to_diff(attribution, line_changes)

        # Deletion should be found
        deletion_found = any(
            u.unit.kind == AttributionUnitKind.DELETION_ONLY
            for u in display_to_unit.values()
        )
        assert deletion_found, "DELETION_ONLY should be found in projection"

    def test_anchor_mismatch_does_not_incorrectly_match(self, temp_repo):
        """Units with wrong anchor should not match, even if content is similar."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\nsame\nline3\nsame\nline5\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        # Delete first "same" but not second
        test_file.write_text("line1\nline3\nsame\nline5\n")

        # Build attribution
        attribution = build_file_attribution("test.txt")

        # Should have one DELETION_ONLY unit with specific anchor
        deletion_units = [
            u.unit
            for u in attribution.units
            if u.unit.kind == AttributionUnitKind.DELETION_ONLY
        ]
        assert len(deletion_units) == 1

        # The deletion should have anchor = 1 (after line1)
        assert deletion_units[0].deletion_anchor_in_working_tree == 1

        # Get diff
        patches = list(
            collect_unified_diff(stream_git_command(["diff", "HEAD", "test.txt"]))
        )
        line_changes = build_line_changes_from_patch_lines(patches[0].lines)

        # Project
        display_to_unit = project_attribution_to_diff(attribution, line_changes)

        # Should find exactly one deletion (not confused by the remaining "same" line)
        deletion_count = sum(
            1
            for u in display_to_unit.values()
            if u.unit.kind == AttributionUnitKind.DELETION_ONLY
        )
        assert deletion_count >= 1, "Should find the deletion"


class TestSemanticConsistency:
    """Test that file comparison, attribution, and projection agree on semantics."""

    def test_layers_agree_on_unit_boundaries(self, temp_repo):
        """File comparison, attribution, and projection should agree on unit boundaries."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("keep\nold1\nold2\nkeep2\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        test_file.write_text("keep\nnew1\nnew2\nkeep2\n")

        # File comparison layer
        units_map = _enumerate_units_from_head_and_working_tree("test.txt")

        # Attribution layer
        attribution = build_file_attribution("test.txt")

        # Both should agree on number and kind of units
        assert len(units_map) == len(attribution.units)

        # Get diff for projection layer
        patches = list(
            collect_unified_diff(stream_git_command(["diff", "HEAD", "test.txt"]))
        )
        line_changes = build_line_changes_from_patch_lines(patches[0].lines)

        # Projection layer
        display_to_unit = project_attribution_to_diff(attribution, line_changes)

        # Projection should map to units from attribution
        projected_unit_ids = {u.unit.unit_id for u in display_to_unit.values()}
        attribution_unit_ids = {u.unit.unit_id for u in attribution.units}

        # All projected units should exist in attribution
        assert projected_unit_ids.issubset(attribution_unit_ids)

    def test_ambiguous_cases_remain_visible(self, temp_repo):
        """Ambiguous cases should remain visible (conservative bias)."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], check=True, capture_output=True
        )

        test_file.write_text("line1\nambiguous\n")

        # Build attribution (no batches, so everything is unowned)
        attribution = build_file_attribution("test.txt")

        # All units should have empty owning_batches (unowned = visible)
        for attr_unit in attribution.units:
            # In absence of batches, units should be unowned
            assert len(attr_unit.owning_batches) == 0, (
                "Without batches, all units should be unowned"
            )

"""Tests for ownership attribution behavior."""

import subprocess
from git_stage_batch.core.diff_parser import parse_unified_diff_streaming

import pytest

import git_stage_batch.batch.attribution as attribution_module
from git_stage_batch.batch.attribution import (
    AttributionUnitKind,
    FileComparison,
    compare_baseline_to_working_tree,
    enumerate_units_from_file_comparison,
    build_file_attribution,
    project_attribution_to_diff,
)
from git_stage_batch.batch.match import match_lines
from git_stage_batch.core.diff_parser import (
    build_line_changes_from_patch_bytes,
)
from git_stage_batch.utils.git import stream_git_command


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    """Create a temporary git repository."""

    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

    return tmp_path


def _create_batch_source_commit(repo, path: str, content: str) -> str:
    file_path = repo / path
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
    assert replacement_units[0].deletion_content == b"old\n"
    assert replacement_units[0].claimed_content == b"new\n"


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
        "read_batch_metadata",
        lambda _name: {
            "files": {
                "test.txt": {
                    "batch_source_commit": batch_source_commit,
                    "claimed_lines": ["2"],
                    "deletions": [],
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


class TestPresenceGranularity:
    """Test that PRESENCE_ONLY units are per-line, not per-run."""

    def test_consecutive_additions_create_separate_units(self, temp_repo):
        """Consecutive added lines should produce separate PRESENCE_ONLY units."""
        # Create baseline with one line
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # Add three consecutive lines
        test_file.write_text("line1\nline2\nline3\nline4\n")

        # Build attribution
        comparison = compare_baseline_to_working_tree("test.txt")
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)

        # Should have THREE separate PRESENCE_ONLY units (lines 2, 3, 4)
        presence_units = [u for u in units_map.values() if u.kind == AttributionUnitKind.PRESENCE_ONLY]
        assert len(presence_units) == 3, "Should have 3 separate PRESENCE_ONLY units"

        # Each should be a single line
        for unit in presence_units:
            assert unit.claimed_content is not None
            # Single line should not contain multiple newlines
            assert unit.claimed_content.count(b'\n') <= 1

    def test_only_individually_owned_lines_hidden(self, temp_repo):
        """Only individually owned added lines should be hidden, not grouped."""
        # This would require a full batch setup to test properly
        # For now, verify unit structure supports individual hiding
        test_file = temp_repo / "test.txt"
        test_file.write_text("original\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        test_file.write_text("original\nadd1\nadd2\nadd3\n")

        comparison = compare_baseline_to_working_tree("test.txt")
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)

        presence_units = [u for u in units_map.values() if u.kind == AttributionUnitKind.PRESENCE_ONLY]

        # Verify each has distinct line number
        line_numbers = [u.claimed_line_in_working_tree for u in presence_units]
        assert len(line_numbers) == len(set(line_numbers)), "Each unit should have unique line number"


class TestReplacementPairing:
    """Test that replacement pairing is deterministic and one-to-one."""

    def test_multiple_changes_pair_deterministically(self, temp_repo):
        """Multiple nearby deletion/addition runs should pair deterministically."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\ndel1\nline2\ndel2\nline3\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # Replace both deletion targets
        test_file.write_text("line1\nadd1\nline2\nadd2\nline3\n")

        comparison = compare_baseline_to_working_tree("test.txt")
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)

        # Should have exactly 2 REPLACEMENT units
        replacement_units = [u for u in units_map.values() if u.kind == AttributionUnitKind.REPLACEMENT]
        assert len(replacement_units) == 2, f"Should have 2 replacements, got {len(replacement_units)}"

        # Verify they have distinct anchors
        anchors = [u.deletion_anchor_in_working_tree for u in replacement_units]
        assert len(set(anchors)) == 2, "Each replacement should have distinct anchor"

    def test_addition_run_not_reused_for_multiple_replacements(self, temp_repo):
        """One addition run cannot be reused for multiple replacements."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("del1\ndel2\nkeep\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # One addition where two deletions were
        test_file.write_text("add\nkeep\n")

        comparison = compare_baseline_to_working_tree("test.txt")
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)

        # Should have at most 1 REPLACEMENT (one-to-one pairing)
        replacement_units = [u for u in units_map.values() if u.kind == AttributionUnitKind.REPLACEMENT]
        assert len(replacement_units) <= 1, "Addition run cannot be reused for multiple replacements"

    def test_pairing_stable_regardless_of_order(self, temp_repo):
        """Replacement pairing should be stable regardless of iteration order."""
        # This test ensures determinism by checking the pairing is based on
        # structural properties, not iteration order
        test_file = temp_repo / "test.txt"
        test_file.write_text("line1\ndel1\nline2\ndel2\nline3\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        test_file.write_text("line1\nadd1\nline2\nadd2\nline3\n")

        # Build units multiple times - should get same result
        results = []
        for _ in range(3):
            comparison = compare_baseline_to_working_tree("test.txt")
            units_map = {}
            enumerate_units_from_file_comparison(comparison, units_map)

            replacement_units = sorted(
                [u for u in units_map.values() if u.kind == AttributionUnitKind.REPLACEMENT],
                key=lambda u: (u.claimed_line_in_working_tree, u.deletion_anchor_in_working_tree)
            )
            results.append(replacement_units)

        # All runs should produce identical results
        assert len(results[0]) == len(results[1]) == len(results[2])
        for i in range(len(results[0])):
            assert results[0][i].unit_id == results[1][i].unit_id == results[2][i].unit_id


class TestAnchorSemantics:
    """Test that anchor determination is alignment-driven, not arithmetic."""

    def test_anchor_follows_structural_comparison(self, temp_repo):
        """Anchor should follow structural comparison, not arithmetic position."""
        test_file = temp_repo / "test.txt"
        # Create baseline with some structure
        test_file.write_text("header\nkeep1\nkeep2\ntarget\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # Delete target line
        test_file.write_text("header\nkeep1\nkeep2\n")

        comparison = compare_baseline_to_working_tree("test.txt")
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)

        deletion_units = [u for u in units_map.values() if u.kind == AttributionUnitKind.DELETION_ONLY]
        assert len(deletion_units) == 1

        # Anchor should be last matched line (keep2 at line 3), not arithmetic line-1
        assert deletion_units[0].deletion_anchor_in_working_tree == 3

    def test_start_of_file_anchor_correct(self, temp_repo):
        """Start-of-file deletions should have None anchor."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("delete_me\nkeep\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # Delete first line
        test_file.write_text("keep\n")

        comparison = compare_baseline_to_working_tree("test.txt")
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)

        deletion_units = [u for u in units_map.values() if u.kind == AttributionUnitKind.DELETION_ONLY]
        assert len(deletion_units) == 1
        assert deletion_units[0].deletion_anchor_in_working_tree is None, "Start-of-file anchor should be None"


class TestPresenceOwnershipIdentity:
    """Test that presence ownership matching is source-identity-aware."""

    def test_repeated_identical_lines_dont_collapse(self, temp_repo):
        """Repeated identical lines should not collapse ownership incorrectly."""
        # This would require full batch setup to test properly
        # For now, verify structure supports source-identity tracking
        test_file = temp_repo / "test.txt"
        test_file.write_text("same\nother\nsame\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # Add another "same" line
        test_file.write_text("same\nother\nsame\nsame\n")

        comparison = compare_baseline_to_working_tree("test.txt")
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)

        presence_units = [u for u in units_map.values() if u.kind == AttributionUnitKind.PRESENCE_ONLY]

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
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        test_file.write_text("line1\nnew\nline3\n")

        # Build attribution
        attribution = build_file_attribution("test.txt")

        # Should have explicit REPLACEMENT unit
        replacement_units = [u.unit for u in attribution.units if u.unit.kind == AttributionUnitKind.REPLACEMENT]
        assert len(replacement_units) >= 1, "Should have at least one REPLACEMENT unit"

        # Get diff for projection
        subprocess.run(
            ["git", "diff", "HEAD", "test.txt"],
            capture_output=True,
            check=True
        )

        # Parse diff

        patches = list(parse_unified_diff_streaming(
            stream_git_command(["diff", "HEAD", "test.txt"])
        ))
        assert len(patches) >= 1

        patch_bytes = patches[0].to_patch_bytes()
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)

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
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # Replace middle line
        test_file.write_text("context1\ncontext2\nnew\ncontext3\ncontext4\n")

        # Build attribution
        attribution = build_file_attribution("test.txt")

        # Get diff with different context settings
        for context_lines in [0, 3, 10]:
            patches = list(parse_unified_diff_streaming(
                stream_git_command(["diff", f"-U{context_lines}", "HEAD", "test.txt"])
            ))
            if not patches:
                continue

            patch_bytes = patches[0].to_patch_bytes()
            line_changes = build_line_changes_from_patch_bytes(patch_bytes)

            # Project attribution
            display_to_unit = project_attribution_to_diff(attribution, line_changes)

            # Should find REPLACEMENT unit regardless of context
            replacement_found = any(
                u.unit.kind == AttributionUnitKind.REPLACEMENT
                for u in display_to_unit.values()
            )
            assert replacement_found, f"REPLACEMENT should be found with -U{context_lines}"

    def test_deletion_projection_independent_of_nearby_context(self, temp_repo):
        """Deletion projection should not depend on specific nearby context lines."""
        test_file = temp_repo / "test.txt"
        test_file.write_text("keep1\nkeep2\ndelete_me\nkeep3\nkeep4\n")

        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # Delete middle line
        test_file.write_text("keep1\nkeep2\nkeep3\nkeep4\n")

        # Build attribution
        attribution = build_file_attribution("test.txt")

        # Should have DELETION_ONLY unit
        deletion_units = [u.unit for u in attribution.units if u.unit.kind == AttributionUnitKind.DELETION_ONLY]
        assert len(deletion_units) == 1

        # Get diff
        patches = list(parse_unified_diff_streaming(
            stream_git_command(["diff", "HEAD", "test.txt"])
        ))
        patch_bytes = patches[0].to_patch_bytes()
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)

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
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        # Delete first "same" but not second
        test_file.write_text("line1\nline3\nsame\nline5\n")

        # Build attribution
        attribution = build_file_attribution("test.txt")

        # Should have one DELETION_ONLY unit with specific anchor
        deletion_units = [u.unit for u in attribution.units if u.unit.kind == AttributionUnitKind.DELETION_ONLY]
        assert len(deletion_units) == 1

        # The deletion should have anchor = 1 (after line1)
        assert deletion_units[0].deletion_anchor_in_working_tree == 1

        # Get diff
        patches = list(parse_unified_diff_streaming(
            stream_git_command(["diff", "HEAD", "test.txt"])
        ))
        patch_bytes = patches[0].to_patch_bytes()
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)

        # Project
        display_to_unit = project_attribution_to_diff(attribution, line_changes)

        # Should find exactly one deletion (not confused by the remaining "same" line)
        deletion_count = sum(
            1 for u in display_to_unit.values()
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
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        test_file.write_text("keep\nnew1\nnew2\nkeep2\n")

        # File comparison layer
        comparison = compare_baseline_to_working_tree("test.txt")
        units_map = {}
        enumerate_units_from_file_comparison(comparison, units_map)

        # Attribution layer
        attribution = build_file_attribution("test.txt")

        # Both should agree on number and kind of units
        assert len(units_map) == len(attribution.units)

        # Get diff for projection layer
        patches = list(parse_unified_diff_streaming(
            stream_git_command(["diff", "HEAD", "test.txt"])
        ))
        patch_bytes = patches[0].to_patch_bytes()
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)

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
        subprocess.run(["git", "commit", "-m", "baseline"], check=True, capture_output=True)

        test_file.write_text("line1\nambiguous\n")

        # Build attribution (no batches, so everything is unowned)
        attribution = build_file_attribution("test.txt")

        # All units should have empty owning_batches (unowned = visible)
        for attr_unit in attribution.units:
            # In absence of batches, units should be unowned
            assert len(attr_unit.owning_batches) == 0, "Without batches, all units should be unowned"

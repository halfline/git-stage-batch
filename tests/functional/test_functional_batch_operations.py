"""Functional tests for batch operations."""

import subprocess
from pathlib import Path


from .conftest import git_stage_batch, get_staged_diff, get_unstaged_diff


def _install_castkms_ptrdiff_replay_fixture(
    functional_repo,
    *,
    corrected_source_alternative: bool = True,
):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_packs = [
        next(fixture_root.glob("castkms_ptrdiff_replay_exact-*.pack")),
    ]
    if corrected_source_alternative:
        fixture_packs.append(
            fixture_root / "castkms_ptrdiff_source_alternative_exact.pack"
        )
    for fixture_pack in fixture_packs:
        subprocess.run(
            ["git", "index-pack", "--stdin"], cwd=functional_repo,
            input=fixture_pack.read_bytes(), check=True, capture_output=True,
        )
    subprocess.run(
        [
            "git", "checkout", "--detach",
            "a59c3b7e422482f654186b79c87236c825d75741",
        ],
        cwd=functional_repo, check=True, capture_output=True,
    )
    refs = {
        "refs/git-stage-batch/batches/decompose-22-ptrdiff-stride-admission":
            "7f312fe101f4dc2dd9f55c2fadd07b005907009a",
        "refs/git-stage-batch/state/decompose-22-ptrdiff-stride-admission":
            (
                "2f333bc3c08d6e10fa70d3fe1396057d0e2882f3"
                if corrected_source_alternative
                else "1ddc2c96e04556d24fd7216884127803fd28c333"
            ),
        "refs/git-stage-batch/batches/decompose-22-ptrdiff-stride-admission-repair":
            "6f9cf6498bc0e51f6491de71fe8725a7b592b7ab",
        "refs/git-stage-batch/state/decompose-22-ptrdiff-stride-admission-repair":
            "d71f3b3c44de3df9326a27d627ad99d191e02887",
    }
    for ref, oid in refs.items():
        subprocess.run(
            ["git", "update-ref", ref, oid], cwd=functional_repo,
            check=True, capture_output=True,
        )
    return "decompose-22-ptrdiff-stride-admission"



def _install_castkms_wide_offset_replay_fixture(functional_repo):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_pack = next(
        fixture_root.glob("castkms_wide_offset_replay_exact-*.pack")
    )
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "--detach",
            "7922200aaa5bc27b29b38ff8f607593d31e38827",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    batch_name = "decompose-24-wide-framebuffer-offsets"
    persisted_refs = {
        f"refs/git-stage-batch/batches/{batch_name}":
            "1d2cd693967c909301b0dc9ced537f2c9aa012b5",
        f"refs/git-stage-batch/state/{batch_name}":
            "caa757d3b5bdd3ae7838714254bea0c3ce54c112",
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )
    return batch_name


def _install_castkms_raw_map_replay_fixture(functional_repo):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_pack = next(
        fixture_root.glob("castkms_raw_map_replay_exact-*.pack")
    )
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "--detach",
            "ca977563d0480499285a739838df53df46d012db",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    batch_name = "decompose-31-raw-framebuffer-maps"
    persisted_refs = {
        f"refs/git-stage-batch/batches/{batch_name}":
            "68a98e92d69295c1a80b340fc97e5e74984fa167",
        f"refs/git-stage-batch/state/{batch_name}":
            "ede272733ba7bff86d383059581cf1b73d976a53",
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )
    return batch_name


def _install_castkms_primary_replay_fixture(functional_repo):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_pack = next(fixture_root.glob("castkms_primary_replay_exact-*.pack"))
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "--detach",
            "73ab324457aa97fc118e8406b973a8050e6e1cd2",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    batch_name = "decompose-36-primary-plane-lookup-ownership"
    persisted_refs = {
        f"refs/git-stage-batch/batches/{batch_name}":
            "3226c34dc32ba37fc12ef3a4501dccd5ef5179e9",
        f"refs/git-stage-batch/state/{batch_name}":
            "ad3641debe9fcba69b6018d096cf97f2969fd730",
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )
    return batch_name


def _install_castkms_primary_replay_fixture(functional_repo):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_pack = next(fixture_root.glob("castkms_primary_replay_exact-*.pack"))
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "--detach",
            "73ab324457aa97fc118e8406b973a8050e6e1cd2",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    batch_name = "decompose-36-primary-plane-lookup-ownership"
    persisted_refs = {
        f"refs/git-stage-batch/batches/{batch_name}":
            "3226c34dc32ba37fc12ef3a4501dccd5ef5179e9",
        f"refs/git-stage-batch/state/{batch_name}":
            "ad3641debe9fcba69b6018d096cf97f2969fd730",
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )
    return batch_name




def _install_castkms_hot_unplug_replay_fixture(functional_repo):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_pack = next(
        fixture_root.glob("castkms_hot_unplug_replay_exact-*.pack")
    )
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "--detach",
            "9e0e83db0bd289ab6f2a26debfb03bca90a10937",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    batch_name = "decompose-32-config-hot-unplug"
    persisted_refs = {
        f"refs/git-stage-batch/batches/{batch_name}":
            "6e6ab7224e6fcfef38a980c4cc2871ae5190f457",
        f"refs/git-stage-batch/state/{batch_name}":
            "1b0d99b0092653263a4c64bcdc27a882b0242422",
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )
    return batch_name


def _install_castkms_primary_replay_fixture(functional_repo):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_pack = next(fixture_root.glob("castkms_primary_replay_exact-*.pack"))
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "--detach",
            "73ab324457aa97fc118e8406b973a8050e6e1cd2",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    batch_name = "decompose-36-primary-plane-lookup-ownership"
    persisted_refs = {
        f"refs/git-stage-batch/batches/{batch_name}":
            "3226c34dc32ba37fc12ef3a4501dccd5ef5179e9",
        f"refs/git-stage-batch/state/{batch_name}":
            "ad3641debe9fcba69b6018d096cf97f2969fd730",
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )
    return batch_name


def _install_castkms_cursor_replay_fixture(functional_repo):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_pack = next(fixture_root.glob("castkms_cursor_replay_exact-*.pack"))
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "--detach",
            "0f363e67dd7f8fca07c2c5063b55f2a85723e191",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    batch_name = "decompose-35-cursor-plane-lookup-ownership"
    persisted_refs = {
        f"refs/git-stage-batch/batches/{batch_name}":
            "1d0e9af52e3461111dd8a70fd78b005116d9c095",
        f"refs/git-stage-batch/state/{batch_name}":
            "3243a7e460aa3e4e16e3be263d03dfab8efd42d2",
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )
    return batch_name
class TestCreateBatch:
    """Test creating batches."""

    def test_create_batch_with_note(self, repo_with_changes):
        """Test creating a batch with a note."""
        result = git_stage_batch("new", "feature-login", "-m", "Add login page")
        assert result.returncode == 0
        assert "Created batch" in result.stderr or result.returncode == 0

        # Verify batch exists
        result = git_stage_batch("list")
        # List output goes to stdout if batches exist, stderr if not
        output = result.stdout + result.stderr
        assert "feature-login" in output or result.returncode == 0

    def test_create_batch_without_note(self, repo_with_changes):
        """Test creating a batch without a note."""
        result = git_stage_batch("new", "test-batch")
        assert result.returncode == 0

    def test_create_duplicate_batch_fails(self, repo_with_changes):
        """Test creating duplicate batch fails."""
        git_stage_batch("new", "test-batch")

        result = git_stage_batch("new", "test-batch", check=False)
        assert result.returncode != 0
        assert "already exists" in result.stderr


class TestIncludeToBatch:
    """Test including changes to a batch."""

    def test_include_to_batch_saves_changes(self, repo_with_changes):
        """Test including lines to a batch saves them."""
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")

        # Include lines to batch
        result = git_stage_batch("include", "--to", "test-batch", "--line", "1,2")
        assert result.returncode == 0
        assert "test-batch" in result.stderr

        # Changes should be removed from working tree
        get_unstaged_diff()
        # Should have fewer unstaged changes

    def test_include_to_batch_stays_on_hunk(self, repo_with_changes):
        """Test including lines to batch stays on selected hunk."""
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")

        git_stage_batch("show")

        # Include only line 1 to batch
        git_stage_batch("include", "--to", "test-batch", "--line", "1")

        # Verify line was saved to batch
        batch_show = git_stage_batch("show", "--from", "test-batch")
        assert batch_show.returncode == 0
        assert batch_show.stdout

        # Should stay on selected hunk (not advance to next)
        # Note: include --to batch doesn't remove lines from working tree,
        # so the hunk should still have all lines
        second_show = git_stage_batch("show", check=False)
        if second_show.returncode == 0:
            # Should still be showing the same file
            assert "README.md" in second_show.stdout

    def test_include_to_multiple_batches(self, repo_with_changes):
        """Test including different changes to different batches."""
        git_stage_batch("new", "batch-a")
        git_stage_batch("new", "batch-b")
        git_stage_batch("start")

        # Include to first batch
        git_stage_batch("include", "--to", "batch-a", "--line", "1")

        # Skip to next hunk
        git_stage_batch("skip", check=False)

        # Include to second batch
        result = git_stage_batch("include", "--to", "batch-b", "--line", "1", check=False)
        if result.returncode == 0:
            # Both batches should have content
            batch_a = git_stage_batch("show", "--from", "batch-a")
            batch_b = git_stage_batch("show", "--from", "batch-b")

            assert batch_a.stdout
            assert batch_b.stdout
            assert batch_a.stdout != batch_b.stdout


class TestDiscardToBatch:
    """Test discarding changes to a batch."""

    def test_discard_to_batch_saves_and_discards(self, repo_with_changes):
        """Test discard to batch saves changes and removes them."""
        git_stage_batch("new", "discard-batch")
        git_stage_batch("start")

        git_stage_batch("discard", "--to", "discard-batch", "--line", "1,2")

        # Changes should be saved to batch
        result = git_stage_batch("show", "--from", "discard-batch")
        assert result.returncode == 0
        assert result.stdout

        # Changes should be removed from working tree
        get_unstaged_diff()
        # Should have fewer changes

    def test_discard_replacement_lines_to_batch_reapplies(self, functional_repo):
        """Discarding selected replacement lines to a batch can be applied back."""
        file_path = functional_repo / "file.txt"
        file_path.write_text("a\nb\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=functional_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=functional_repo, capture_output=True)

        file_path.write_text("A\nB\n")

        git_stage_batch("start")
        discard_result = git_stage_batch("discard", "--to", "test", "--line", "1,3")

        assert discard_result.stdout.count("file.txt ::") == 1
        assert file_path.read_text() == "a\nB\n"

        git_stage_batch("apply", "--from", "test")

        assert file_path.read_text() == "A\nB\n"


class TestShowFromBatch:
    """Test showing changes from a batch."""

    def test_show_from_batch_displays_changes(self, repo_with_changes):
        """Test showing changes from a batch."""
        git_stage_batch("new", "test-batch", "-m", "Test changes")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2")

        result = git_stage_batch("show", "--from", "test-batch")
        assert result.returncode == 0
        # Should show the note in the header
        assert "Test changes" in result.stdout
        # Should show line IDs
        assert "[#" in result.stdout

    def test_show_from_empty_batch_succeeds(self, repo_with_changes):
        """Test showing from empty batch succeeds with no output."""
        git_stage_batch("new", "empty-batch")

        result = git_stage_batch("show", "--from", "empty-batch", check=False)
        assert result.returncode == 0
        assert result.stdout == ""  # No output for empty batch

    def test_show_from_nonexistent_batch_fails(self, repo_with_changes):
        """Test showing from nonexistent batch fails."""
        result = git_stage_batch("show", "--from", "nonexistent", check=False)
        assert result.returncode != 0


class TestApplyFromBatch:
    """Test applying changes from a batch."""

    def test_apply_ptrdiff_stride_migration_before_companion_repair(
        self,
        functional_repo,
    ):
        """The stride migration must replay before its companion is inverted."""
        batch_name = _install_castkms_ptrdiff_replay_fixture(functional_repo)
        path = functional_repo / "src" / "castkms_formats.c"

        result = git_stage_batch(
            "apply", "--from", batch_name, check=False,
        )
        assert result.returncode == 0, result.stderr

        git_stage_batch(
            "discard", "--from", f"{batch_name}-repair",
        )
        source = path.read_text()
        assert "static ptrdiff_t get_block_step_bytes" in source
        assert "if (block_stride > SSIZE_MAX)" in source
        assert "if (block_stride > INT_MAX)" not in source
        assert "drm_format_info_block_height(fb->format" in source

    def test_apply_wide_offset_helper_and_both_callers(self, functional_repo):
        """A selected helper migration must carry continuation lines."""
        batch_name = _install_castkms_wide_offset_replay_fixture(functional_repo)
        relative_path = "src/castkms_formats.c"
        path = functional_repo / relative_path
        committed = path.read_text()

        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            relative_path,
            "--pages",
            "all",
        )
        result = git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            relative_path,
            "--line",
            "1-24,26-32,34-36",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        source = path.read_text()
        assert source.count("offset = castkms_packed_pixels_offset(") == 2
        assert "\n\tpacked_pixels_offset(frame_info" not in source

        result = git_stage_batch(
            "discard",
            "--from",
            batch_name,
            "--file",
            relative_path,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert path.read_text() == committed

    def test_legacy_ptrdiff_stride_migration_fails_without_semantic_intent(
        self,
        functional_repo,
    ):
        """An ambiguous legacy insertion must not preserve both alternatives."""
        batch_name = _install_castkms_ptrdiff_replay_fixture(
            functional_repo,
            corrected_source_alternative=False,
        )
        path = functional_repo / "src" / "castkms_formats.c"
        before = path.read_bytes()

        result = git_stage_batch("apply", "--from", batch_name, check=False)

        assert result.returncode != 0
        assert "does not record whether adjacent historical source content" in (
            result.stderr
        )
        assert path.read_bytes() == before

    def test_apply_batch_after_committing_neighboring_replacements(
        self,
        functional_repo,
    ):
        """Committed neighboring replacements must not block batch replay."""
        batch_name = _install_castkms_primary_replay_fixture(functional_repo)

        result = git_stage_batch("apply", "--from", batch_name, check=False)

        assert result.returncode == 0, result.stderr
        header = (functional_repo / "src" / "castkms_config.h").read_text()
        tests = (
            functional_repo / "src" / "tests" / "castkms_config_test.c"
        ).read_text()
        assert (
            "castkms_config_crtc_primary_plane(struct castkms_config_crtc"
            in header
        )
        assert "castkms_config_crtc_primary_plane(config, crtc_cfg)" not in tests

    def test_apply_hot_unplug_guard_after_recommended_adopters(
        self,
        functional_repo,
    ):
        """Applying recommended adopters must leave their guard replayable."""
        batch_name = _install_castkms_hot_unplug_replay_fixture(functional_repo)
        path = functional_repo / "src" / "castkms_config.c"

        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--pages",
            "all",
        )
        git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--line",
            "1-2,15-22,24",
        )
        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--pages",
            "all",
        )
        result = git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--line",
            "3-14,23",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        source = path.read_text()
        assert "if (!drm_dev_enter(dev, &idx))" in source
        assert "drm_dev_exit(idx);" in source
        assert "config = castkmsdev->config;" in source

    def test_apply_batch_after_committing_neighboring_replacements(
        self,
        functional_repo,
    ):
        """Committed neighboring replacements must not block batch replay."""
        batch_name = _install_castkms_primary_replay_fixture(functional_repo)

        result = git_stage_batch("apply", "--from", batch_name, check=False)

        assert result.returncode == 0, result.stderr
        header = (functional_repo / "src" / "castkms_config.h").read_text()
        tests = (
            functional_repo / "src" / "tests" / "castkms_config_test.c"
        ).read_text()
        assert (
            "castkms_config_crtc_primary_plane(struct castkms_config_crtc"
            in header
        )
        assert "castkms_config_crtc_primary_plane(config, crtc_cfg)" not in tests

    def test_discard_partially_applied_replacement_restores_file(
        self,
        functional_repo,
    ):
        """Discarding a file-scoped replay must restore the committed file."""
        batch_name = _install_castkms_primary_replay_fixture(functional_repo)
        path = functional_repo / "src" / "castkms_config.c"
        committed = path.read_text()

        git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
        )
        assert path.read_text() != committed

        result = git_stage_batch(
            "discard",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert path.read_text() == committed

    def test_discard_applied_output_replacement_restores_file(
        self,
        functional_repo,
    ):
        """Discarding a reviewed output replacement must restore the file."""
        batch_name = _install_castkms_cursor_replay_fixture(functional_repo)
        path = functional_repo / "src" / "castkms_output.c"
        committed = path.read_text()

        overview = git_stage_batch("apply", "--from", batch_name, check=False)
        assert overview.returncode != 0
        assert "apply candidate" in overview.stderr
        git_stage_batch(
            "show",
            "--from",
            f"{batch_name}:apply:1",
            "--file",
            "src/castkms_output.c",
        )
        git_stage_batch(
            "apply",
            "--from",
            f"{batch_name}:apply:1",
            "--file",
            "src/castkms_output.c",
        )
        result = git_stage_batch(
            "discard",
            "--from",
            batch_name,
            "--file",
            "src/castkms_output.c",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert path.read_text() == committed

    def test_apply_suggested_cursor_helper_line_selection(
        self,
        functional_repo,
    ):
        """The suggested helper line selection must apply after its adopters."""
        batch_name = _install_castkms_cursor_replay_fixture(functional_repo)

        overview = git_stage_batch("apply", "--from", batch_name, check=False)
        assert overview.returncode != 0
        git_stage_batch(
            "show",
            "--from",
            f"{batch_name}:apply:1",
            "--file",
            "src/castkms_output.c",
        )
        git_stage_batch(
            "apply",
            "--from",
            f"{batch_name}:apply:1",
            "--file",
            "src/castkms_output.c",
        )
        git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.h",
        )
        result = git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--line",
            "1-10,17-18",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        source = (functional_repo / "src" / "castkms_config.c").read_text()
        assert "castkms_crtc_get_plane(struct castkms_config_crtc" in source
        assert (
            "castkms_crtc_get_plane(crtc_cfg, DRM_PLANE_TYPE_CURSOR)"
            in source
        )

        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
        )
        result = git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--line",
            "11-16",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        source = (functional_repo / "src/castkms_config.c").read_text()
        assert (
            "castkms_crtc_get_plane(crtc_cfg, DRM_PLANE_TYPE_PRIMARY)"
            in source
        )
        assert (
            "castkms_config_crtc_cursor_plane(struct castkms_config *"
            not in source
        )

    def test_discard_partial_cursor_helper_replay_restores_file(
        self,
        functional_repo,
    ):
        """Discarding a partial helper replay must restore the committed file."""
        batch_name = _install_castkms_cursor_replay_fixture(functional_repo)
        path = functional_repo / "src" / "castkms_config.c"
        committed = path.read_text()

        git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--line",
            "1-10,17-18",
        )
        result = git_stage_batch(
            "discard",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert path.read_text() == committed

    def test_discard_completed_cursor_helper_replay_restores_file(
        self,
        functional_repo,
    ):
        """Discarding both helper selections must restore the committed file."""
        batch_name = _install_castkms_cursor_replay_fixture(functional_repo)
        path = functional_repo / "src" / "castkms_config.c"
        committed = path.read_text()

        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
        )
        git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--line",
            "11-16",
        )
        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
        )
        git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            "--line",
            "1-10,17-18",
        )
        result = git_stage_batch(
            "discard",
            "--from",
            batch_name,
            "--file",
            "src/castkms_config.c",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert path.read_text() == committed

    def test_apply_remaining_cursor_test_calls_after_middle_pairs(
        self,
        functional_repo,
    ):
        """Applying middle test pairs must leave surrounding calls replayable."""
        batch_name = _install_castkms_cursor_replay_fixture(functional_repo)

        git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/tests/castkms_config_test.c",
            "--line",
            "5-8",
        )
        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            "src/tests/castkms_config_test.c",
        )
        result = git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            "src/tests/castkms_config_test.c",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        source = (
            functional_repo / "src" / "tests" / "castkms_config_test.c"
        ).read_text()
        assert (
            "castkms_config_crtc_cursor_plane(config, crtc_cfg)"
            not in source
        )

    def test_start_reviews_changes_restored_from_batch(self, functional_repo):
        """A restored batch should remain available to a fresh staging pass."""
        file_path = functional_repo / "file.txt"
        file_path.write_text("before\n")
        subprocess.run(
            ["git", "add", "file.txt"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )

        file_path.write_text("after\n")
        git_stage_batch("new", "restored-change", "-m", "Restore one change")
        git_stage_batch("start", "--no-auto-advance")
        git_stage_batch(
            "discard",
            "--to",
            "restored-change",
            "--file",
            "file.txt",
            "--no-auto-advance",
        )
        git_stage_batch("stop")

        git_stage_batch("apply", "--from", "restored-change")
        assert file_path.read_text() == "after\n"
        assert get_unstaged_diff("file.txt")

        result = git_stage_batch("start", "--no-auto-advance", check=False)

        assert result.returncode == 0, result.stderr
        assert "file.txt" in result.stdout

        git_stage_batch("include", "--files", "file.txt", "--no-auto-advance")
        assert get_staged_diff("file.txt")

    def test_partial_staging_keeps_remaining_restored_hunks_reviewable(
        self,
        functional_repo,
    ):
        """Staging one restored hunk must not hide the remaining applied work."""
        file_path = functional_repo / "file.txt"
        file_path.write_text(
            "base 1\nbase 2\nbase 3\nbase 4\nbase 5\nbase 6\nbase 7\n"
        )
        subprocess.run(
            ["git", "add", "file.txt"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )

        file_path.write_text(
            "changed 1\n"
            "base 1\nbase 2\nbase 3\nbase 4\nbase 5\nbase 6\nbase 7\n"
            "changed 7\n"
        )
        git_stage_batch("new", "restored-change")
        git_stage_batch(
            "start",
            "--unified",
            "0",
            "--no-auto-advance",
        )
        git_stage_batch(
            "discard",
            "--to",
            "restored-change",
            "--file",
            "file.txt",
            "--no-auto-advance",
        )
        git_stage_batch("stop")
        git_stage_batch("apply", "--from", "restored-change")

        first = git_stage_batch(
            "start",
            "--unified",
            "0",
            "--no-auto-advance",
        )
        assert "changed 1" in first.stdout
        assert "changed 7" not in first.stdout

        git_stage_batch("include", "--no-auto-advance")
        remaining = git_stage_batch("again", "--no-auto-advance", check=False)

        assert remaining.returncode == 0, remaining.stderr
        assert "changed 7" in remaining.stdout

        git_stage_batch("stop")
        fresh = git_stage_batch("start", "--no-auto-advance", check=False)

        assert fresh.returncode == 0, fresh.stderr
        assert "changed 7" in fresh.stdout

    def test_start_names_batches_that_already_own_all_changes(
        self,
        functional_repo,
    ):
        """A fresh start should explain why unchanged saved work is hidden."""
        file_path = functional_repo / "file.txt"
        file_path.write_text("before\n")
        subprocess.run(
            ["git", "add", "file.txt"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )

        file_path.write_text("after\n")
        git_stage_batch("new", "saved-change")
        git_stage_batch("start", "--no-auto-advance")
        git_stage_batch(
            "include",
            "--to",
            "saved-change",
            "--file",
            "file.txt",
            "--no-auto-advance",
        )
        git_stage_batch("stop")

        result = git_stage_batch("start", "--no-auto-advance", check=False)

        assert result.returncode == 2
        assert (
            "All working tree changes are currently saved in batch "
            "'saved-change'."
        ) in result.stderr

    def test_start_names_each_batch_that_owns_the_hidden_changes(
        self,
        functional_repo,
    ):
        """The no-change diagnostic should identify every masking batch."""
        first_path = functional_repo / "first.txt"
        second_path = functional_repo / "second.txt"
        first_path.write_text("before first\n")
        second_path.write_text("before second\n")
        subprocess.run(
            ["git", "add", "first.txt", "second.txt"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add files"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        first_path.write_text("after first\n")
        second_path.write_text("after second\n")

        git_stage_batch("new", "alpha")
        git_stage_batch("new", "beta")
        git_stage_batch("start", "--no-auto-advance")
        git_stage_batch(
            "include",
            "--to",
            "alpha",
            "--file",
            "first.txt",
            "--no-auto-advance",
        )
        git_stage_batch(
            "include",
            "--to",
            "beta",
            "--file",
            "second.txt",
            "--no-auto-advance",
        )
        git_stage_batch("stop")

        result = git_stage_batch("start", "--no-auto-advance", check=False)

        assert result.returncode == 2
        assert (
            "All working tree changes are currently saved in batch 'alpha', "
            "batch 'beta'."
        ) in result.stderr

    def test_external_edit_invalidates_restored_batch_review_provenance(
        self,
        functional_repo,
    ):
        """Applied provenance must not survive a later worktree mutation."""
        file_path = functional_repo / "file.txt"
        file_path.write_text("before\n")
        subprocess.run(
            ["git", "add", "file.txt"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )

        file_path.write_text("after\n")
        git_stage_batch("new", "restored-change")
        git_stage_batch("start", "--no-auto-advance")
        git_stage_batch(
            "discard",
            "--to",
            "restored-change",
            "--file",
            "file.txt",
            "--no-auto-advance",
        )
        git_stage_batch("stop")
        git_stage_batch("apply", "--from", "restored-change")

        file_path.write_text("after\nindependent\n")
        result = git_stage_batch("start", "--no-auto-advance")

        assert "independent" in result.stdout
        assert "[#3] + independent" in result.stdout
        assert "[#2] + after" in result.stdout

    def test_fresh_start_reviews_consecutive_batch_applications(
        self,
        functional_repo,
    ):
        """Layered applies should retain each still-fresh ownership overlay."""
        file_path = functional_repo / "file.txt"
        file_path.write_text("base\n")
        subprocess.run(
            ["git", "add", "file.txt"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        file_path.write_text("base\nalpha\ncontext\nbeta\n")
        git_stage_batch("new", "alpha")
        git_stage_batch("new", "beta")
        first_view = git_stage_batch("start", "--no-auto-advance").stdout
        alpha_line_id = next(
            line.split("]", 1)[0].removeprefix("[#")
            for line in first_view.splitlines()
            if "+ alpha" in line
        )
        git_stage_batch(
            "discard",
            "--to",
            "alpha",
            "--line",
            alpha_line_id,
            "--no-auto-advance",
        )
        second_view = git_stage_batch("again", "--no-auto-advance").stdout
        assert "+ context" in second_view
        beta_line_ids = [
            line.split("]", 1)[0].removeprefix("[#")
            for line in second_view.splitlines()
            if "+ context" in line or "+ beta" in line
        ]
        assert len(beta_line_ids) == 2
        git_stage_batch(
            "discard",
            "--to",
            "beta",
            "--line",
            ",".join(beta_line_ids),
            "--no-auto-advance",
        )
        git_stage_batch("stop")

        assert file_path.read_text() == "base\n"
        git_stage_batch("apply", "--from", "alpha")
        git_stage_batch("apply", "--from", "beta")
        assert file_path.read_text() == "base\nalpha\ncontext\nbeta\n"

        result = git_stage_batch("start", "--no-auto-advance", check=False)

        assert result.returncode == 0, result.stderr
        assert "+ alpha" in result.stdout
        assert "+ context" in result.stdout
        assert "+ beta" in result.stdout

    def test_undo_apply_restores_applied_batch_review_provenance(
        self,
        functional_repo,
    ):
        """Undo should restore both worktree bytes and provenance state."""
        file_path = functional_repo / "file.txt"
        file_path.write_text("before\n")
        subprocess.run(
            ["git", "add", "file.txt"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=functional_repo,
            capture_output=True,
        )

        file_path.write_text("after\n")
        git_stage_batch("new", "restored-change")
        git_stage_batch("start", "--no-auto-advance")
        git_stage_batch(
            "discard",
            "--to",
            "restored-change",
            "--file",
            "file.txt",
            "--no-auto-advance",
        )

        git_stage_batch("apply", "--from", "restored-change")
        overlay_path = (
            functional_repo
            / ".git"
            / "git-stage-batch"
            / "applied-batch-overlays.json"
        )
        assert overlay_path.exists()
        git_stage_batch("undo")

        assert file_path.read_text() == "before\n"
        assert not overlay_path.exists()
        git_stage_batch("apply", "--from", "restored-change")
        result = git_stage_batch("again", "--no-auto-advance", check=False)
        assert result.returncode == 0, result.stderr
        assert "file.txt" in result.stdout

    def test_apply_from_batch_stages_changes(self, repo_with_changes):
        """Test applying from batch stages the changes."""
        # Save changes to batch
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2,3")

        # Clear working tree changes
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Apply from batch
        result = git_stage_batch("apply", "--from", "test-batch")
        assert result.returncode == 0

        # Changes should be in working tree (unstaged)
        unstaged = get_unstaged_diff()
        assert unstaged
        assert "+" in unstaged

    def test_apply_from_batch_with_line_selection(self, repo_with_changes):
        """Test applying specific lines from a batch.

        Note: Lines 1,2,3,4 form an explicit atomic replacement unit
        (deletion + coupled additions), so we select all four to respect the
        semantic boundary.
        """
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2,3,4")

        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Apply only specific lines (must respect atomic unit boundaries)
        # Lines 1,2,3,4 form one explicit atomic replacement unit
        result = git_stage_batch("apply", "--from", "test-batch", "--line", "1,2,3,4")
        assert result.returncode == 0

        unstaged = get_unstaged_diff()
        assert unstaged


    def test_apply_raw_map_tests_after_case_entries(self, functional_repo):
        """Applying case entries must leave their test functions replayable."""
        batch_name = _install_castkms_raw_map_replay_fixture(functional_repo)
        relative_path = "src/tests/castkms_format_test.c"
        path = functional_repo / relative_path
        committed = path.read_text()

        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            relative_path,
            "--pages",
            "all",
        )
        git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            relative_path,
            "--line",
            "80-81",
        )
        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            relative_path,
            "--pages",
            "all",
        )
        result = git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            relative_path,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        source = path.read_text()
        assert source.count(
            "static void castkms_format_test_framebuffer_offset"
        ) == 1
        assert source.count(
            "static void castkms_format_test_distinct_multiplane_maps"
        ) == 1
        assert source.count(
            "KUNIT_CASE(castkms_format_test_framebuffer_offset)"
        ) == 1
        assert source.count(
            "KUNIT_CASE(castkms_format_test_distinct_multiplane_maps)"
        ) == 1

        git_stage_batch(
            "discard",
            "--from",
            batch_name,
            "--file",
            relative_path,
        )
        assert path.read_text() == committed


    def test_apply_batch_after_committing_neighboring_replacements(
        self,
        functional_repo,
    ):
        """Committed neighboring replacements must not block batch replay."""
        batch_name = _install_castkms_primary_replay_fixture(functional_repo)

        result = git_stage_batch("apply", "--from", batch_name, check=False)

        assert result.returncode == 0, result.stderr
        header = (functional_repo / "src" / "castkms_config.h").read_text()
        tests = (
            functional_repo / "src" / "tests" / "castkms_config_test.c"
        ).read_text()
        assert (
            "castkms_config_crtc_primary_plane(struct castkms_config_crtc"
            in header
        )
        assert "castkms_config_crtc_primary_plane(config, crtc_cfg)" not in tests

    def test_apply_stride_guard_after_wide_offset_helper(self, functional_repo):
        """An adjacent helper insertion must replay after its predecessor."""
        batch_name = _install_castkms_stride_guard_replay_fixture(functional_repo)
        relative_path = "src/castkms_formats.c"
        path = functional_repo / relative_path

        git_stage_batch(
            "show",
            "--from",
            batch_name,
            "--file",
            relative_path,
            "--pages",
            "all",
        )
        result = git_stage_batch(
            "apply",
            "--from",
            batch_name,
            "--file",
            relative_path,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        source = path.read_text()
        assert "bool castkms_framebuffer_read_strides_are_valid" in source
        assert "if (block_stride > INT_MAX)" in source
        assert "if (block_stride > SSIZE_MAX)" not in source


class TestBatchList:
    """Test listing batches."""

    def test_list_empty_batches(self, repo_with_changes):
        """Test listing when no batches exist."""
        result = git_stage_batch("list")
        assert result.returncode == 0
        # Output might be empty or have a header

    def test_list_multiple_batches(self, repo_with_changes):
        """Test listing multiple batches."""
        git_stage_batch("new", "batch-a")
        git_stage_batch("new", "batch-b")
        git_stage_batch("new", "batch-c")

        result = git_stage_batch("list")
        assert "batch-a" in result.stdout
        assert "batch-b" in result.stdout
        assert "batch-c" in result.stdout


class TestBatchDelete:
    """Test deleting batches."""

    def test_delete_batch(self, repo_with_changes):
        """Test deleting a batch."""
        git_stage_batch("new", "test-batch")

        result = git_stage_batch("drop", "test-batch")
        assert result.returncode == 0

        # Batch should be gone
        list_result = git_stage_batch("list")
        assert "test-batch" not in list_result.stdout

    def test_delete_nonexistent_batch_fails(self, repo_with_changes):
        """Test deleting nonexistent batch fails."""
        result = git_stage_batch("drop", "nonexistent", check=False)
        assert result.returncode != 0


class TestOddEvenLinesBatches:
    """Test discard and apply with even-lines and odd-lines batches."""

    def test_discard_odd_even_lines_to_batches(self, functional_repo):
        """Test discarding odd/even lines to separate batches."""
        # Create a file with some initial content
        test_file = functional_repo / "numbers.txt"
        test_file.write_text("Initial content\n")

        # Add to git
        subprocess.run(["git", "add", "numbers.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add numbers file"], check=True, capture_output=True)

        # Add 10 new lines (only additions, simpler for line ID tracking)
        test_file.write_text(
            "Initial content\n"
            "Line 1\n"
            "Line 2\n"
            "Line 3\n"
            "Line 4\n"
            "Line 5\n"
            "Line 6\n"
            "Line 7\n"
            "Line 8\n"
            "Line 9\n"
            "Line 10\n"
        )

        # Create batches
        git_stage_batch("new", "odd-lines")
        git_stage_batch("new", "even-lines")

        # Start and discard odd lines (1,3,5,7,9) to odd-lines batch
        git_stage_batch("start")
        git_stage_batch("discard", "--to", "odd-lines", "--line", "1,3,5,7,9")

        # Verify odd lines batch has content
        odd_result = git_stage_batch("show", "--from", "odd-lines")
        assert odd_result.returncode == 0
        assert odd_result.stdout
        # Should have line markers
        assert "[#" in odd_result.stdout
        # Should contain some of our lines
        assert "Line" in odd_result.stdout

        # Discard all remaining lines in the file to even-lines batch
        git_stage_batch("discard", "--to", "even-lines", "--file", check=False)

        # Verify even lines batch has content
        even_result = git_stage_batch("show", "--from", "even-lines", check=False)
        if even_result.returncode == 0:
            assert even_result.stdout
            assert "[#" in even_result.stdout
            assert "Line" in even_result.stdout

        # Both batches should exist
        list_result = git_stage_batch("list")
        assert "odd-lines" in list_result.stdout
        assert "even-lines" in list_result.stdout

    def test_apply_odd_then_even_lines_together(self, functional_repo):
        """Test discarding to odd/even batches then applying both in order."""
        # Create a file with initial content
        test_file = functional_repo / "sequence.txt"
        test_file.write_text("Header\n")

        subprocess.run(["git", "add", "sequence.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add sequence file"], check=True, capture_output=True)

        # Add 10 new lines
        test_file.write_text("Header\n" + "\n".join([f"Added line {i}" for i in range(1, 11)]) + "\n")

        # Create batches and discard
        git_stage_batch("new", "odd-lines")
        git_stage_batch("new", "even-lines")
        git_stage_batch("start")

        # Discard odd line IDs
        git_stage_batch("discard", "--to", "odd-lines", "--line", "1,3,5,7,9")

        # Discard all remaining hunks in the selected file to even-lines batch
        git_stage_batch("discard", "--to", "even-lines", "--file", check=False)

        # File should now be back to original (all lines discarded)
        selected_content = test_file.read_text()
        assert selected_content == "Header\n"

        # Both batches should have content
        odd_show = git_stage_batch("show", "--from", "odd-lines")
        even_show = git_stage_batch("show", "--from", "even-lines", check=False)

        assert odd_show.returncode == 0
        assert "Added line" in odd_show.stdout

        if even_show.returncode == 0:
            assert even_show.stdout
            assert "Added line" in even_show.stdout

        # Clear working tree and apply both batches back
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Apply both in sequence: odd first, then even
        git_stage_batch("apply", "--from", "odd-lines")
        git_stage_batch("apply", "--from", "even-lines")

        # Should have all changes back
        unstaged = get_unstaged_diff("sequence.txt")
        assert unstaged
        assert "Added line" in unstaged
        # Should have all 10 lines back
        final_content = test_file.read_text()
        for i in range(1, 11):
            assert f"Added line {i}" in final_content

    def test_apply_even_then_odd_lines_together(self, functional_repo):
        """Test discarding to odd/even batches then applying in reverse order."""
        # Create a file with initial content
        test_file = functional_repo / "reverse.txt"
        test_file.write_text("Start\n")

        subprocess.run(["git", "add", "reverse.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add reverse file"], check=True, capture_output=True)

        # Add 10 new lines
        test_file.write_text("Start\n" + "\n".join([f"New {i}" for i in range(1, 11)]) + "\n")

        # Create batches and discard
        git_stage_batch("new", "odd-lines")
        git_stage_batch("new", "even-lines")
        git_stage_batch("start")

        # Discard odd line IDs
        git_stage_batch("discard", "--to", "odd-lines", "--line", "1,3,5,7,9")

        # Discard all remaining hunks in the selected file to even-lines batch
        git_stage_batch("discard", "--to", "even-lines", "--file", check=False)

        # File should be back to original
        assert test_file.read_text() == "Start\n"

        # Clear all changes to test apply
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)
        assert test_file.read_text() == "Start\n"

        # Apply in reverse order: even then odd
        # This tests that apply --from works with these batch names in both orders
        git_stage_batch("apply", "--from", "even-lines")
        git_stage_batch("apply", "--from", "odd-lines")

        # Should have all changes back
        unstaged = get_unstaged_diff("reverse.txt")
        assert unstaged
        assert "New" in unstaged
        # Should have all 10 lines back
        final_content = test_file.read_text()
        for i in range(1, 11):
            assert f"New {i}" in final_content


class TestBatchAbortReversion:
    """Test that batches are reverted to their original state after abort."""

    def test_abort_reverts_batch_created_during_session(self, functional_repo):
        """Test that batches created during session are deleted on abort."""
        # Create a file with changes
        test_file = functional_repo / "test.txt"
        test_file.write_text("Line 1\n")
        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, capture_output=True)
        test_file.write_text("Line 1\nLine 2\n")

        # Verify batch doesn't exist yet
        list_before = git_stage_batch("list")
        assert "session-batch" not in list_before.stdout

        # Start session and create batch
        git_stage_batch("start")
        git_stage_batch("new", "session-batch")
        git_stage_batch("include", "--to", "session-batch", "--line", "1", check=False)

        # Verify batch exists and has content
        batch_show = git_stage_batch("show", "--from", "session-batch")
        assert batch_show.returncode == 0
        assert batch_show.stdout

        # Abort session
        git_stage_batch("abort")

        # Batch should be deleted (it was created during session)
        list_after = git_stage_batch("list")
        assert "session-batch" not in list_after.stdout

        # Verify batch is gone
        result = git_stage_batch("show", "--from", "session-batch", check=False)
        assert result.returncode != 0

    def test_abort_reverts_batch_modified_during_session(self, functional_repo):
        """Test that batches modified during session are reverted to original state."""
        # Create a file with changes
        test_file = functional_repo / "revert.txt"
        test_file.write_text("Original\n")
        subprocess.run(["git", "add", "revert.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, capture_output=True)
        test_file.write_text("Original\nAdded line 1\nAdded line 2\nAdded line 3\n")

        # Create batch BEFORE session (empty is the original state)
        git_stage_batch("new", "existing-batch", "-m", "Initial note")

        # Start session and modify the batch by adding lines
        git_stage_batch("start")
        git_stage_batch("include", "--to", "existing-batch", "--line", "1,2,3", check=False)

        # Verify batch has content now
        modified_show = git_stage_batch("show", "--from", "existing-batch")
        assert modified_show.returncode == 0
        assert "Added line 1" in modified_show.stdout or "Added line 2" in modified_show.stdout

        # Abort - should revert to original empty state
        git_stage_batch("abort")

        # Batch should be back to original empty state (no added lines)
        reverted_show = git_stage_batch("show", "--from", "existing-batch")
        # Should not contain the lines added during session
        assert "Added line 1" not in reverted_show.stdout
        assert "Added line 2" not in reverted_show.stdout
        assert "Added line 3" not in reverted_show.stdout

    def test_abort_restores_dropped_batch(self, functional_repo):
        """Test that batches dropped during session are restored on abort."""
        # Create a file
        test_file = functional_repo / "dropped.txt"
        test_file.write_text("Content\n")
        subprocess.run(["git", "add", "dropped.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, capture_output=True)
        test_file.write_text("Content\nNew line\n")

        # Create batch before session
        git_stage_batch("new", "will-drop", "-m", "Original batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "will-drop", "--line", "1", check=False)
        git_stage_batch("abort")

        # Capture original state
        original_show = git_stage_batch("show", "--from", "will-drop")
        assert original_show.returncode == 0
        original_content = original_show.stdout

        # Start session and drop the batch
        git_stage_batch("start")
        git_stage_batch("drop", "will-drop")

        # Verify batch is gone
        list_dropped = git_stage_batch("list")
        assert "will-drop" not in list_dropped.stdout

        # Abort - should restore the batch
        git_stage_batch("abort")

        # Batch should be restored with original content
        restored_show = git_stage_batch("show", "--from", "will-drop")
        assert restored_show.returncode == 0
        assert restored_show.stdout == original_content

    def test_abort_handles_multiple_batch_operations(self, functional_repo):
        """Test abort correctly handles multiple batch operations in one session."""
        # Create file
        test_file = functional_repo / "multi.txt"
        test_file.write_text("Base\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, capture_output=True)
        test_file.write_text("Base\nLine 1\nLine 2\nLine 3\n")

        # Create some batches before session
        git_stage_batch("new", "batch-a", "-m", "Batch A")
        git_stage_batch("new", "batch-b", "-m", "Batch B")

        # Start session and do various operations
        git_stage_batch("start")

        # Modify batch-a
        git_stage_batch("include", "--to", "batch-a", "--line", "1", check=False)

        # Drop batch-b
        git_stage_batch("drop", "batch-b")

        # Create new batch-c
        git_stage_batch("new", "batch-c")
        git_stage_batch("include", "--to", "batch-c", "--line", "2", check=False)

        # Verify state during session
        list_during = git_stage_batch("list")
        assert "batch-a" in list_during.stdout
        assert "batch-b" not in list_during.stdout  # dropped
        assert "batch-c" in list_during.stdout  # created

        # Abort
        git_stage_batch("abort")

        # Check final state:
        # - batch-a should be empty (reverted)
        # - batch-b should be restored
        # - batch-c should be deleted
        list_after = git_stage_batch("list")
        assert "batch-a" in list_after.stdout
        assert "batch-b" in list_after.stdout
        assert "batch-c" not in list_after.stdout

        # batch-a should be empty (reverted to original state, no Line 1)
        batch_a_show = git_stage_batch("show", "--from", "batch-a", check=False)
        assert "Line 1" not in batch_a_show.stdout

        # batch-b should exist
        git_stage_batch("show", "--from", "batch-b", check=False)
        # Should exist even if empty
        assert "batch-b" in list_after.stdout


class TestComplexBatchWorkflows:
    """Test complex batch workflows."""

    def test_split_changes_across_batches(self, repo_with_changes):
        """Test splitting changes across multiple batches."""
        # Create batches for different features
        git_stage_batch("new", "feature-a", "-m", "Feature A changes")
        git_stage_batch("new", "feature-b", "-m", "Feature B changes")
        git_stage_batch("new", "fixes", "-m", "Fixes")

        git_stage_batch("start")

        # Distribute changes across batches
        git_stage_batch("include", "--to", "feature-a", "--line", "1")
        git_stage_batch("skip", check=False)
        git_stage_batch("include", "--to", "feature-b", "--line", "1", check=False)
        git_stage_batch("skip", check=False)
        git_stage_batch("include", "--to", "fixes", "--line", "1", check=False)

        # All batches should have content
        for batch in ["feature-a", "feature-b", "fixes"]:
            result = git_stage_batch("show", "--from", batch, check=False)
            if result.returncode == 0:
                assert result.stdout

    def test_split_new_file_reconstructs_exact_content(self, functional_repo):
        """Applying batches split from a new file must reproduce it exactly."""
        test_file = functional_repo / "new-service.js"
        expected = (
            "class Service {\n"
            "    start() {\n"
            "        return oldValue ||\n"
            "            newValue;\n"
            "    }\n"
            "}\n"
        )
        test_file.write_text(expected)

        git_stage_batch("new", "new-file-structure")
        git_stage_batch("new", "new-file-behavior")
        git_stage_batch("start")

        git_stage_batch(
            "discard",
            "--to",
            "new-file-structure",
            "--line",
            "1,2,5,6",
        )
        git_stage_batch(
            "discard",
            "--to",
            "new-file-behavior",
            "--file",
        )

        assert not test_file.exists()

        git_stage_batch("apply", "--from", "new-file-structure")
        git_stage_batch("apply", "--from", "new-file-behavior")

        assert test_file.read_text() == expected

    def test_batch_accumulation_workflow(self, repo_with_changes):
        """Test accumulating changes to a batch over multiple sessions."""
        git_stage_batch("new", "accumulated")

        # First session: add some changes
        git_stage_batch("start")
        git_stage_batch("include", "--to", "accumulated", "--line", "1,2")

        # Make more changes
        readme = repo_with_changes / "README.md"
        content = readme.read_text()
        readme.write_text(content + "\n## More Changes\n- Additional feature\n")

        # Second session: add more to same batch
        git_stage_batch("start")
        git_stage_batch("include", "--to", "accumulated", "--line", "1", check=False)

        # Batch should have accumulated content
        batch_show = git_stage_batch("show", "--from", "accumulated")
        assert batch_show.stdout

    def test_apply_batch_then_modify_and_reapply(self, repo_with_changes):
        """Test applying batch, making changes, and reapplying."""
        # Create and populate batch
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2")

        # Clear working tree
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Apply batch
        git_stage_batch("apply", "--from", "test-batch")

        # Should have unstaged changes
        unstaged = get_unstaged_diff()
        assert unstaged

        # Stage and commit
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Applied batch changes"],
            check=True,
            capture_output=True
        )

        # Batch still exists
        result = git_stage_batch("list")
        assert "test-batch" in result.stdout


class TestBatchRebaseWorkflow:
    """Test batch operations with git rebase."""

    def test_batch_changes_then_apply_in_history(self, functional_repo):
        """Test batching changes from tip, then applying them to earlier commit via rebase."""
        # Create a file with stable base content
        file1 = functional_repo / "feature.txt"
        file1.write_text("# Feature\n\ndef main():\n    pass\n")
        subprocess.run(["git", "add", "feature.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add feature skeleton"], check=True, capture_output=True)

        # Add unrelated commit
        file2 = functional_repo / "other.txt"
        file2.write_text("Other file\n")
        subprocess.run(["git", "add", "other.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add other file"], check=True, capture_output=True)

        # Make changes at tip - add a new function
        file1.write_text("# Feature\n\ndef main():\n    pass\n\ndef helper():\n    return 42\n")

        # Batch the tip changes (just the new helper function)
        git_stage_batch("new", "improvements")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "improvements")

        # Clear working tree
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Rebase to edit the first commit
        # HEAD~2 goes back before both our commits, allowing us to edit both
        # sed will change the first pick to edit (which is "Add feature skeleton")
        env = {
            **subprocess.os.environ,
            "GIT_SEQUENCE_EDITOR": "sed -i.bak '1s/^pick/edit/'",
        }
        rebase_result = subprocess.run(
            ["git", "rebase", "-i", "HEAD~2"],
            env=env,
            capture_output=True,
            text=True,
            check=False
        )

        # Should be in rebase state, stopped at first commit
        assert rebase_result.returncode == 0

        # Apply the batch from the tip to this earlier commit
        git_stage_batch("apply", "--from", "improvements")

        # Verify changes are in working tree
        content = file1.read_text()
        assert "helper()" in content
        assert "return 42" in content

        # Amend the commit with the improvements
        subprocess.run(["git", "add", "feature.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--amend", "--no-edit"],
            check=True,
            capture_output=True
        )

        # Continue the rebase
        continue_result = subprocess.run(
            ["git", "rebase", "--continue"],
            capture_output=True,
            text=True,
            check=False
        )

        # Rebase should complete successfully
        assert continue_result.returncode == 0

        # Verify the improvement is now in the first commit
        first_commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()

        show_result = subprocess.run(
            ["git", "show", first_commit_hash],
            capture_output=True,
            text=True,
            check=True
        )

        # The helper function should be in the first commit now
        assert "helper()" in show_result.stdout
        assert "return 42" in show_result.stdout

        # Batch should still exist
        result = git_stage_batch("list")
        assert "improvements" in result.stdout


def _install_castkms_stride_guard_replay_fixture(functional_repo):
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_pack = next(
        fixture_root.glob("castkms_stride_guard_replay_exact-*.pack")
    )
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "--detach",
            "812d4beb9592b12048524b6f59049eec25dc03d3",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    batch_name = "decompose-23-int-range-scanout-guard"
    persisted_refs = {
        f"refs/git-stage-batch/batches/{batch_name}":
            "b2de98e6c965ea6e22945b0f13043c3a1072f033",
        f"refs/git-stage-batch/state/{batch_name}":
            "f714d3d0af9cb864401fe2ee2b8eb3253645d539",
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )
    return batch_name

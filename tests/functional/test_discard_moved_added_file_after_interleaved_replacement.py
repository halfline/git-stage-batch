"""Regression coverage for reversing a moved added-file claim after layering."""

import json
import subprocess

from .conftest import git_stage_batch


def _save_replace_and_sift(path, batch, predecessor):
    saved = git_stage_batch(
        "include",
        "--to",
        batch,
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert saved.returncode == 0, saved.stderr
    replaced = git_stage_batch(
        "discard",
        "--file",
        path.name,
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor,
        check=False,
    )
    assert replaced.returncode == 0, replaced.stderr
    sifted = git_stage_batch(
        "sift", "--from", batch, "--to", batch, check=False
    )
    assert sifted.returncode == 0, sifted.stderr


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _batch_file_metadata(functional_repo, batch, path):
    state = subprocess.run(
        ["git", "show", f"refs/git-stage-batch/state/{batch}:batch.json"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(state)["files"][path.name]


def test_discard_moved_added_file_claim_preserves_interleaved_predecessor(
    functional_repo,
):
    """Whole-file inverse must not delete shared added-file predecessor text."""
    path = functional_repo / "protocol.h"
    predecessor = (
        "header\n"
        "Permit cursor inclusion and metadata in a capture stream. Pixel capture\n"
        "without this right must exclude the cursor.\n"
        "stable a\nstable b\nstable c\nfooter\n"
    )
    feature_target = predecessor.replace("footer\n", "read bitmap\nfooter\n")
    layered_target = (
        "header\n"
        "Permit cursor inclusion, metadata, and bitmap retrieval in a capture\n"
        "stream. Pixel capture without this right must exclude the cursor.\n"
        "stable a\nstable b\nstable c\n"
        "read bitmap\nfooter\n"
    )
    wording_target = layered_target.replace("read bitmap\n", "")
    outer_lines = [f"outer feature {index}\n" for index in range(1, 7)]
    final_target = layered_target.replace(
        "header\n", "header\n" + "".join(outer_lines)
    )
    other = functional_repo / "reader.c"
    other.write_text("base reader\n")
    subprocess.run(
        ["git", "add", other.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add reader baseline"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(final_target)
    other.write_text("base reader\nread bitmap helper\n")

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    bitmap_id = _display_id_for_text(view, "read bitmap")
    bitmap_saved = git_stage_batch(
        "discard",
        "--to",
        "bitmap-read",
        "--line",
        str(bitmap_id),
        "--no-auto-advance",
        check=False,
    )
    assert bitmap_saved.returncode == 0, bitmap_saved.stderr
    outer_predecessor = final_target.replace("read bitmap\n", "")
    for index, outer_line in enumerate(outer_lines, start=1):
        outer_predecessor = outer_predecessor.replace(outer_line, "")
        _save_replace_and_sift(path, f"outer-{index}", outer_predecessor)
    assert path.read_text() == wording_target
    other_saved = git_stage_batch(
        "include",
        "--to",
        "bitmap-read",
        "--file",
        other.name,
        "--no-auto-advance",
        check=False,
    )
    assert other_saved.returncode == 0, other_saved.stderr
    other_discarded = git_stage_batch(
        "discard",
        "--file",
        other.name,
        "--no-auto-advance",
        check=False,
    )
    assert other_discarded.returncode == 0, other_discarded.stderr
    moved = git_stage_batch(
        "reset",
        "--from",
        "bitmap-read",
        "--to",
        "bitmap-read-hold",
        "--files",
        "**",
        check=False,
    )
    assert moved.returncode == 0, moved.stderr

    _save_replace_and_sift(path, "bitmap-read", predecessor)
    wording_metadata = _batch_file_metadata(
        functional_repo, "bitmap-read", path
    )
    assert "change_type" not in wording_metadata
    assert wording_metadata["presence_claims"] != [
        {"source_lines": [f"1-{len(layered_target.splitlines())}"]}
    ]
    rejected_merge = git_stage_batch(
        "reset",
        "--from",
        "bitmap-read",
        "--to",
        "bitmap-read-hold",
        "--file",
        path.name,
        check=False,
    )
    assert rejected_merge.returncode != 0
    assert "different batch source" in rejected_merge.stderr
    old_apply = git_stage_batch(
        "apply",
        "--from",
        "bitmap-read-hold",
        "--file",
        path.name,
        check=False,
    )
    assert old_apply.returncode == 0, old_apply.stderr
    assert path.read_text() == feature_target
    wording_apply = git_stage_batch(
        "apply",
        "--from",
        "bitmap-read",
        "--file",
        path.name,
        check=False,
    )
    assert wording_apply.returncode == 0, wording_apply.stderr
    assert path.read_text() == layered_target

    wording_inverse = git_stage_batch(
        "discard",
        "--from",
        "bitmap-read",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert wording_inverse.returncode == 0, wording_inverse.stderr
    old_inverse = git_stage_batch(
        "discard",
        "--from",
        "bitmap-read-hold",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert old_inverse.returncode == 0, old_inverse.stderr
    assert path.read_text() == predecessor
    assert path.read_text() != feature_target

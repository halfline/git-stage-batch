"""Coverage for transformed appends after earlier same-file discards."""

from pathlib import Path
import subprocess

from .conftest import git_stage_batch


FIXTURE_ROOT = Path(__file__).parent / "fixtures"
GUEST_SMOKE_PATH = "scripts/vm/guest-smoke-test.sh"
GUEST_SMOKE_EXACT_LINE_IDS = "4-82,153-176,180-275"
GUEST_SMOKE_SHARED_LINE = (
    '"$runtime_dir/unplug-gate" "$runtime_dir/mode-gate"'
)
GUEST_SMOKE_REPLACEMENT = (
    '\t\t\t"$runtime_dir/mode-gate"\n'
    '\t\t\t"$runtime_dir/unplug-gate"\n'
)


def _commit_file(repo, content):
    file_path = repo / "file.txt"
    file_path.write_text(content)
    subprocess.run(
        ["git", "add", "file.txt"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return file_path


def _show_file(repo, ref, path="file.txt"):
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout


def _display_id_for_text(view, text):
    matches = [
        line for line in view.splitlines()
        if text in line and "[#" in line
    ]
    assert len(matches) == 1, matches
    return matches[0].split("[#", 1)[1].split("]", 1)[0]


def _prepare_guest_smoke_fixture(functional_repo):
    relative_path = GUEST_SMOKE_PATH
    file_path = functional_repo / relative_path
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        (FIXTURE_ROOT / "discard_transformed_append_baseline.sh").read_text()
    )
    subprocess.run(
        ["git", "add", relative_path],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add guest smoke test"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    file_path.write_text(
        (FIXTURE_ROOT / "discard_transformed_append_session.sh").read_text()
    )
    return relative_path, file_path


def _discard_guest_smoke_leading_batches(relative_path):
    git_stage_batch("start", "--no-auto-advance")
    first_view = git_stage_batch(
        "show", "--file", relative_path, "--page", "all"
    ).stdout
    assert "[#99]" in first_view
    assert "[#105]" in first_view
    git_stage_batch(
        "discard",
        "--to",
        "batch-a",
        "--line",
        "99-105",
        "--no-auto-advance",
    )

    second_view = git_stage_batch(
        "show", "--file", relative_path, "--page", "all"
    ).stdout
    assert "[#97]" in second_view
    assert "[#98]" in second_view
    assert "test ! -e ./castkms.ko" in second_view
    assert "test ! -e ./src/tests/castkms-kunit-tests.ko" in second_view
    git_stage_batch(
        "discard",
        "--to",
        "batch-b",
        "--line",
        "97-98",
        "--no-auto-advance",
    )


def _discard_guest_smoke_shared_line(relative_path):
    shared_view = git_stage_batch(
        "show", "--file", relative_path, "--page", "all"
    ).stdout
    shared_id = _display_id_for_text(
        shared_view,
        GUEST_SMOKE_SHARED_LINE,
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        shared_id,
        "--as-stdin",
        "--no-auto-advance",
        input_text=GUEST_SMOKE_REPLACEMENT,
        check=False,
    )
    assert result.returncode == 0, f"shared_id={shared_id}\n{result.stderr}"


def _batch_file_content(functional_repo, relative_path):
    return _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/batch-c",
        relative_path,
    )


def test_guest_smoke_transformed_append_before_exact_multirange(functional_repo):
    """A shared-line split should precede the remaining exact capture."""
    relative_path, file_path = _prepare_guest_smoke_fixture(functional_repo)

    _discard_guest_smoke_leading_batches(relative_path)
    _discard_guest_smoke_shared_line(relative_path)

    remaining_view = git_stage_batch(
        "show", "--file", relative_path, "--page", "all"
    ).stdout
    assert "[#4]" in remaining_view
    assert "mode_gate_open=0" in remaining_view
    assert "[#82]" in remaining_view
    assert "[#153]" in remaining_view
    assert "enable_writeback=0" in remaining_view
    assert "[#176]" in remaining_view
    assert "[#180]" in remaining_view
    assert "[#275]" in remaining_view
    exact_result = git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        GUEST_SMOKE_EXACT_LINE_IDS,
        "--no-auto-advance",
        check=False,
    )
    assert exact_result.returncode == 0, (
        f"remaining_ids={GUEST_SMOKE_EXACT_LINE_IDS}\n"
        f"{exact_result.stderr}"
    )

    live_content = file_path.read_text()
    batch_content = _batch_file_content(functional_repo, relative_path)
    assert '\t\t\t"$runtime_dir/unplug-gate"\n' in live_content
    assert '\t\t\t"$runtime_dir/mode-gate"\n' not in live_content
    assert "append_crc_record()" not in live_content
    assert "run_writeback()" not in live_content
    assert "mode_gate_open=0" not in live_content
    assert "enable_writeback=1" not in live_content
    assert "enable_writeback=0" in live_content
    assert '\t\t\t"$runtime_dir/mode-gate"\n' in batch_content
    assert '\t\t\t"$runtime_dir/unplug-gate"\n' not in batch_content
    assert "append_crc_record()" in batch_content
    assert "run_writeback()" in batch_content
    assert "mode_gate_open=0" in batch_content
    assert "enable_writeback=1" in batch_content


def test_guest_smoke_transformed_append_after_exact_multirange(functional_repo):
    """A transformed shared line should append after exact multirange capture."""
    relative_path, file_path = _prepare_guest_smoke_fixture(functional_repo)
    _discard_guest_smoke_leading_batches(relative_path)

    git_stage_batch("show", "--file", relative_path, "--page", "all")
    git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        GUEST_SMOKE_EXACT_LINE_IDS,
        "--no-auto-advance",
    )
    _discard_guest_smoke_shared_line(relative_path)
    live_content = file_path.read_text()
    batch_content = _batch_file_content(functional_repo, relative_path)
    assert '\t\t\t"$runtime_dir/unplug-gate"\n' in live_content
    assert '\t\t\t"$runtime_dir/mode-gate"\n' not in live_content
    assert (
        '\t\t\t"$runtime_dir/unplug-gate" "$runtime_dir/mode-gate"\n'
        not in live_content
    )
    assert '\t\t\t"$runtime_dir/mode-gate"\n' in batch_content
    assert '\t\t\t"$runtime_dir/unplug-gate"\n' not in batch_content


def test_transformed_append_after_prior_same_file_batches(functional_repo):
    """A transformed append should survive earlier same-file source advances."""
    file_path = _commit_file(
        functional_repo,
        "header\n"
        "scan-old-one\n"
        "scan-old-two\n"
        "clean-anchor\n"
        "runtime-anchor\n"
        "footer\n",
    )
    file_path.write_text(
        "header\n"
        "scan-new-one\n"
        "scan-new-two\n"
        "clean-one\n"
        "clean-two\n"
        "clean-anchor\n"
        "runtime-anchor\n"
        "mode-before-one\n"
        "mode-before-two\n"
        "unplug-one\n"
        "shared-unplug mode-gate\n"
        "unplug-two\n"
        "mode-after-one\n"
        "mode-after-two\n"
        "footer\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    git_stage_batch(
        "discard",
        "--to",
        "batch-a",
        "--line",
        "1-4",
        "--no-auto-advance",
    )
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    git_stage_batch(
        "discard",
        "--to",
        "batch-b",
        "--line",
        "1-2",
        "--no-auto-advance",
    )
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        "1-2,6-7",
        "--no-auto-advance",
    )
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    result = git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        "2",
        "--as-stdin",
        "--no-auto-advance",
        input_text="shared-unplug\n",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert file_path.read_text() == (
        "header\n"
        "scan-old-one\n"
        "scan-old-two\n"
        "clean-anchor\n"
        "runtime-anchor\n"
        "unplug-one\n"
        "unplug-two\n"
        "footer\n"
    )
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/batch-c",
    ) == (
        "header\n"
        "scan-old-one\n"
        "scan-old-two\n"
        "clean-anchor\n"
        "runtime-anchor\n"
        "mode-before-one\n"
        "mode-before-two\n"
        "shared-unplug\n"
        "mode-after-one\n"
        "mode-after-two\n"
        "footer\n"
    )

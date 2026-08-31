"""Coverage for transformed appends after earlier same-file discards."""

import json
from pathlib import Path
import subprocess
import tarfile

import pytest

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


def _display_id_range(view, first_text, last_text):
    first = int(_display_id_for_text(view, first_text))
    last = int(_display_id_for_text(view, last_text))
    assert first <= last
    return f"{first}-{last}"


def test_single_added_line_transform_preserves_neighboring_markdown(functional_repo):
    """Replacing one added sentence must retain neighboring list additions.

    This is the minimal form of the CastKMS deconstruction failure: the line
    selected below is one addition inside a larger old-to-new replacement
    hunk, past the point where the old side runs out of matched lines.
    """
    path = functional_repo / "guide.md"
    path.write_text(
        "# Guide\n\n"
        "The old guide explains the original workflow.\n"
        "It includes several details that the new guide replaces.\n"
        "The original workflow has one lane.\n\n"
        "## Commands\n"
    )
    subprocess.run(
        ["git", "add", "guide.md"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add guide"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    final_text = (
        "# Guide\n\n"
        "The new guide explains the expanded workflow.\n"
        "It records why each lane runs.\n\n"
        "CI runs three lanes on every push:\n\n"
        "- Userspace lane.\n"
        "- Fast lane.\n"
        "- Product lane.\n\n"
        "## Commands\n"
    )
    path.write_text(final_text)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", "guide.md", "--page", "all"
    ).stdout
    sentence_id = _display_id_for_text(
        view, "CI runs three lanes on every push:"
    )
    git_stage_batch(
        "discard",
        "--to",
        "lane-count",
        "--line",
        sentence_id,
        "--as-stdin",
        "--no-auto-advance",
        input_text="CI runs on every push:\n",
    )

    assert path.read_text() == final_text.replace(
        "CI runs three lanes on every push:\n",
        "CI runs on every push:\n",
    )


def test_single_added_line_transform_preserves_evolved_markdown(functional_repo):
    """Replacing one added sentence must not restore its large deleted peer."""
    baseline_text = """`provision` downloads the base image, creates a 30 GiB sparse overlay, boots
the guest, installs the pinned kernel and build tools, and reboots the guest
into that kernel. Re-running it is safe and idempotent.

`test` mirrors the current working tree into the guest and then:

1. builds `castkms.ko` and the four-suite KUnit module with `W=1`;
2. verifies both modules' names, vermagic, dependencies, legacy strings, and
   exported symbols;
3. loads stock `vkms` and `castkms` together without default devices;
4. verifies independent `vkms` and `castkms` configfs roots;
5. creates a device through configfs and verifies topology removal safely
   disables and unplugs it before detaching configuration, including explicit
   ioctl and debugfs failures through file descriptors kept open across
   removal;
6. creates a default `castkms` DRM card with a color pipeline and writeback
   connector;
7. performs a bounded preferred-mode, vsynced page-flip test;
8. keeps CRC capture open across two writeback jobs, verifies both fences and
   output buffers, and requires fresh CRC records after writeback cleanup;
9. records `modetest`, `drm_info`, CRC, writeback, and lifecycle output;
10. unloads every module it loaded and verifies cleanup.

The pinned Fedora kernel publishes the KUnit ABI in its development package
but does not ship the corresponding `kunit.ko`, so the VM currently provides
compile and linkage coverage for the KUnit suites rather than executing them.
The standalone build target is also available directly with `make kunit`.

Results are copied to:

```text
~/.cache/castkms-vm/results/default/
```

Useful commands:

```sh
./scripts/vm/castkms-vm status
./scripts/vm/castkms-vm shell
./scripts/vm/castkms-vm logs
./scripts/vm/castkms-vm sync
./scripts/vm/castkms-vm stop
```

"""
    final_text = """`provision` downloads the base image, creates a 30 GiB sparse overlay, boots
the guest, installs the pinned kernel and build tools, and reboots into that
kernel. Re-running it is safe and idempotent.

`kunit-test` mirrors the current working tree into the guest, builds all four
audio/CEC inclusion combinations and the kernel-options-disabled fallback with
`W=1`, runs the nine KUnit suites, then loads the normal device with CEC
enabled and two outputs, and runs the focused live grant-fd lifecycle gate. The
live gate checks cross-connector denial and verifies that revoking one grant
does not prevent the other output's grant from managing its attachment. It
rejects kernel warnings and requires a clean module unload.

The broader `test` command runs that fast gate first, reuses its build
artifacts, then builds the userspace protocol and PipeWire tests and runs the
product scenarios. Setting `CASTKMS_VM_FAST_GATE=skip` skips the fast gate and
does one warning-enabled production build instead. GitHub CI uses that mode
because its separate fast lane has already run the matrix and KUnit.

CI runs three lanes on every pull request and push to `main`:

- **Userspace / protocol and entrypoints**: `make check` on the host,
  including the EDID suite and every available CLI entrypoint.
- **Fast / KUnit and grant security**: `castkms-vm kunit-test`.
- **Product / full capture stack**:
  `CASTKMS_VM_FAST_GATE=skip ./scripts/vm/castkms-vm test`.

A separate desktop instance checks that Mutter discovers an attached virtual
monitor, so GNOME packages never land on the default guest:

```sh
./scripts/vm/castkms-vm desktop-provision
./scripts/vm/castkms-vm desktop-test
```

## Commands

```sh
./scripts/vm/castkms-vm status
./scripts/vm/castkms-vm shell
./scripts/vm/castkms-vm logs
./scripts/vm/castkms-vm sync
./scripts/vm/castkms-vm stop
```

"""
    path = _commit_file(functional_repo, baseline_text)
    path.write_text(final_text)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", "file.txt", "--page", "all"
    ).stdout
    sentence_id = _display_id_for_text(
        view,
        "CI runs three lanes on every pull request and push to `main`:",
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "lane-count",
        "--line",
        sentence_id,
        "--as-stdin",
        "--no-auto-advance",
        input_text="CI runs on every pull request and push to `main`:\n",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    transformed_sentence = "CI runs on every pull request and push to `main`:\n"
    retained = final_text.replace(
        "CI runs three lanes on every pull request and push to `main`:\n",
        transformed_sentence,
    )
    assert path.read_text() == retained
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/lane-count",
    ) == baseline_text.replace(
        "Useful commands:\n",
        transformed_sentence + "Useful commands:\n",
    )

    git_stage_batch("stop")
    replay = git_stage_batch("apply", "--from", "lane-count", check=False)
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == retained


@pytest.mark.parametrize(
    ("first_text", "last_text", "replacement_text", "retained"),
    [
        (
            "new-b",
            "new-b",
            "saved-b-one\nsaved-b-two\n",
            "head\nnew-a\nsaved-b-one\nsaved-b-two\nnew-c\nnew-d\ntail\n",
        ),
        (
            "new-b",
            "new-c",
            "saved-middle\n",
            "head\nnew-a\nsaved-middle\nnew-d\ntail\n",
        ),
    ],
    ids=("growing-rewrite", "shrinking-rewrite"),
)
def test_inner_added_span_transform_preserves_scope_across_cardinality(
    functional_repo,
    first_text,
    last_text,
    replacement_text,
    retained,
):
    """An explicit added subspan must not inherit its whole replacement run."""
    path = _commit_file(
        functional_repo,
        "head\nold-a\nold-b\nold-c\ntail\n",
    )
    path.write_text("head\nnew-a\nnew-b\nnew-c\nnew-d\ntail\n")

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", "file.txt", "--page", "all"
    ).stdout
    selected = _display_id_range(view, first_text, last_text)
    result = git_stage_batch(
        "discard",
        "--to",
        "inner-rewrite",
        "--line",
        selected,
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement_text,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == retained

    git_stage_batch("stop")
    replay = git_stage_batch("apply", "--from", "inner-rewrite", check=False)
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == retained


def test_complete_added_side_transform_retains_replacement_scope(functional_repo):
    """Selecting every added peer must still restore the deleted side."""
    baseline = "head\nold-a\nold-b\ntail\n"
    path = _commit_file(functional_repo, baseline)
    path.write_text("head\nnew-a\nnew-b\ntail\n")

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", "file.txt", "--page", "all"
    ).stdout
    selected = _display_id_range(view, "new-a", "new-b")
    result = git_stage_batch(
        "discard",
        "--to",
        "complete-rewrite",
        "--line",
        selected,
        "--as-stdin",
        "--no-auto-advance",
        input_text="saved-a\nsaved-b\n",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == baseline
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/complete-rewrite",
    ) == "head\nsaved-a\nsaved-b\ntail\n"

    git_stage_batch("stop")
    replay = git_stage_batch("apply", "--from", "complete-rewrite", check=False)
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == "head\nsaved-a\nsaved-b\ntail\n"


def test_single_added_line_transform_can_reuse_deleted_baseline_wording(
    functional_repo,
):
    """A retained trailing addition may match a deleted line byte-for-byte."""
    path = _commit_file(
        functional_repo,
        "head\nold one\nold two\ntail\n",
    )
    path.write_text("head\nnew one\nnew two\nextra\ntail\n")

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", "file.txt", "--page", "all"
    ).stdout
    extra_id = _display_id_for_text(view, "extra")
    result = git_stage_batch(
        "discard",
        "--to",
        "baseline-wording",
        "--line",
        extra_id,
        "--as-stdin",
        "--no-auto-advance",
        input_text="old one\n",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    retained = "head\nnew one\nnew two\nold one\ntail\n"
    assert path.read_text() == retained
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/baseline-wording",
    ) == "head\nold one\nold two\nold one\ntail\n"

    git_stage_batch("stop")
    replay = git_stage_batch(
        "apply",
        "--from",
        "baseline-wording",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == retained


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


def test_transformed_selector_append_does_not_relocate_repeated_lines(functional_repo):
    """A selector split should not claim matching braces from its validator."""
    baseline = (
        "header\n"
        "plane-old\n"
        "middle\n"
        "int select_writer(int format)\n"
        "{\n"
        "\tswitch (format) {\n"
        "\tcase 1:\n"
        "\t\treturn old_writer;\n"
        "\tdefault:\n"
        "\t\tBUG();\n"
        "\t}\n"
        "}\n"
        "footer\n"
    )
    final = (
        "header\n"
        "plane-new\n"
        "int select_plane(int format)\n"
        "{\n"
        "\tfor (int i = 0; i < plane_count; i++)\n"
        "\t\tif (planes[i].format == format)\n"
        "\t\t\treturn planes[i].reader;\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT(select_plane);\n"
        "middle\n"
        "int select_writer(int format)\n"
        "{\n"
        "\tfor (int i = 0; i < writer_count; i++)\n"
        "\t\tif (writers[i].format == format)\n"
        "\t\t\treturn writers[i].writer;\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT(select_writer);\n"
        "bool registries_valid(void)\n"
        "{\n"
        "\tfor (int i = 0; i < plane_count; i++) {\n"
        "\t\tif (!planes[i].reader)\n"
        "\t\t\treturn false;\n"
        "\t}\n"
        "\n"
        "\tfor (int i = 0; i < writer_count; i++) {\n"
        "\t\tif (!writers[i].writer)\n"
        "\t\t\treturn false;\n"
        "\t}\n"
        "\n"
        "\treturn true;\n"
        "}\n"
        "EXPORT(registries_valid);\n"
        "footer\n"
    )
    live_selector = (
        "\tswitch (format) {\n"
        "\tcase 1:\n"
        "\t\treturn old_writer;\n"
        "\tdefault:\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "}\n"
        "EXPORT(select_writer);\n"
    )
    batch_selector = (
        "\tfor (int i = 0; i < writer_count; i++)\n"
        "\t\tif (writers[i].format == format)\n"
        "\t\t\treturn writers[i].writer;\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT(select_writer);\n"
    )
    expected_batch = (
        "header\n"
        "plane-new\n"
        "int select_plane(int format)\n"
        "{\n"
        "\tfor (int i = 0; i < plane_count; i++)\n"
        "\t\tif (planes[i].format == format)\n"
        "\t\t\treturn planes[i].reader;\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT(select_plane);\n"
        "middle\n"
        "int select_writer(int format)\n"
        "{\n"
        + batch_selector
        + "footer\n"
    )
    file_path = _commit_file(functional_repo, baseline)
    file_path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    context_ids = _display_id_range(view, "plane-old", "EXPORT(select_plane);")
    git_stage_batch(
        "include",
        "--to",
        "selector",
        "--line",
        context_ids,
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    selector_ids = _display_id_range(
        view,
        "\tswitch (format) {",
        "EXPORT(select_writer);",
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "selector",
        "--line",
        selector_ids,
        "--as-stdin",
        "--no-auto-advance",
        input_text=batch_selector + live_selector,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_live = final.replace(batch_selector, live_selector)
    actual = (
        file_path.read_text(),
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/selector",
        ),
    )
    assert actual == (expected_live, expected_batch)
    live_content, batch_content = actual
    assert "BUG();" not in live_content
    assert live_content.count("EXPORT(select_writer);") == 1
    assert "\tswitch (format) {" not in batch_content
    assert "old_writer" not in batch_content
    assert "registries_valid" not in batch_content


def test_transformed_selector_stops_before_adjacent_validator(functional_repo):
    """A batch/live split should not claim matching validator structure."""
    old_doc = """ * castkms_get_pixel_write_function() - Retrieve the correct write_pixel function for a specific format.
 * The returned pointer is NULL for unsupported pixel formats. The caller must ensure that the
 * pointer is valid before using it in a castkms_writeback_job.
 *
 * @format: DRM_FORMAT_* value for which to obtain a conversion function (see [drm_fourcc.h])
 */
"""
    new_doc = """ * castkms_get_pixel_write_function() - Retrieve a format's write callback
 * @format: DRM_FORMAT_* value for which to obtain a conversion function
 *
 * Returns NULL when @format is unsupported.
 */
"""
    old_switch = """pixel_write_t castkms_get_pixel_write_function(u32 format)
{
	switch (format) {
	case DRM_FORMAT_ARGB8888:
		return &argb_u16_to_ARGB8888;
	case DRM_FORMAT_XRGB8888:
		return &argb_u16_to_XRGB8888;
	case DRM_FORMAT_ABGR8888:
		return &argb_u16_to_ABGR8888;
	case DRM_FORMAT_ARGB16161616:
		return &argb_u16_to_ARGB16161616;
	case DRM_FORMAT_XRGB16161616:
		return &argb_u16_to_XRGB16161616;
	case DRM_FORMAT_RGB565:
		return &argb_u16_to_RGB565;
	default:
		BUG();
	}
}
"""
    live_switch = old_switch.replace("\t\tBUG();\n", "\t\treturn NULL;\n")
    batch_selector = """pixel_write_t castkms_get_pixel_write_function(u32 format)
{
	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_writeback_formats); i++)
		if (castkms_writeback_formats[i].format == format)
			return castkms_writeback_formats[i].write_pixel;

	return NULL;
}
"""
    selector_export = "EXPORT_SYMBOL_IF_KUNIT(castkms_get_pixel_write_function);\n"
    descriptors = """struct castkms_writeback_format {
	u32 format;
	pixel_write_t write_pixel;
};

static const struct castkms_writeback_format castkms_writeback_formats[] = {
	{ DRM_FORMAT_ARGB8888, argb_u16_to_ARGB8888 },
};

"""
    validator = """#if IS_ENABLED(CONFIG_KUNIT)
VISIBLE_IF_KUNIT bool castkms_format_registries_are_valid(void)
{
	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_plane_formats); i++) {
		pixel_read_line_t read_line;
		u32 format = castkms_plane_formats[i].format;

		read_line = castkms_get_pixel_read_line_function(format);
		if (read_line != castkms_plane_formats[i].read_line)
			return false;
	}

	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_writeback_formats); i++) {
		pixel_write_t write_pixel;
		u32 format = castkms_writeback_formats[i].format;

		write_pixel = castkms_get_pixel_write_function(format);
		if (write_pixel != castkms_writeback_formats[i].write_pixel)
			return false;
	}

	return true;
}
EXPORT_SYMBOL_IF_KUNIT(castkms_format_registries_are_valid);
#endif
"""
    prior = "".join(f"prior-change-{number:03}\n" for number in range(1, 370))
    baseline = "prefix\n/**\n" + old_doc + old_switch + "suffix\n"
    final = (
        "prefix\n" + prior + descriptors + "/**\n" + new_doc
        + batch_selector + selector_export + validator + "suffix\n"
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    prior_first = int(_display_id_for_text(view, "prior-change-001"))
    descriptor_last = max(
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "};" in line and "[#" in line
    )
    git_stage_batch(
        "include", "--to", "descriptor-context", "--line",
        f"{prior_first}-{descriptor_last}", "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = int(_display_id_for_text(view, "Retrieve the correct write_pixel"))
    last = int(_display_id_for_text(view, "\treturn NULL;")) + 1
    payload = (
        FIXTURE_ROOT / "discard_transformed_selector_cross_context.txt"
    ).read_text()
    result = git_stage_batch(
        "discard", "--to", "selector", "--line", f"{first}-{last}",
        "--as-stdin", "--no-auto-advance", input_text=payload, check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_live = (
        "prefix\n" + prior + descriptors + "/**\n" + old_doc
        + live_switch + selector_export + validator + "suffix\n"
    )
    expected_batch = "prefix\n/**\n" + new_doc + batch_selector + "suffix\n"
    assert path.read_text() == expected_live
    assert _show_file(functional_repo, "refs/git-stage-batch/batches/selector") == (
        expected_batch
    )


def test_transformed_plane_selector_preserves_intermediate_callback(
    functional_repo,
):
    """A selector split should retain a callback changed by an inner batch."""
    old_switch = """pixel_read_line_t select_reader(u32 format)
{
	switch (format) {
	case FORMAT_P010:
	case FORMAT_P012:
	case FORMAT_P016:
		return &legacy_p0xx_reader;
	case FORMAT_YUV420:
	case FORMAT_YUV422:
		return &planar_reader;
	default:
		BUG();
	}
}
"""
    live_switch = old_switch.replace(
        "\t\treturn &legacy_p0xx_reader;\n",
        "\t\treturn &p0xx_reader;\n",
    ).replace("\t\tBUG();\n", "\t\treturn NULL;\n")
    batch_selector = """pixel_read_line_t select_reader(u32 format)
{
	for (unsigned int i = 0; i < ARRAY_SIZE(plane_formats); i++)
		if (plane_formats[i].format == format)
			return plane_formats[i].read_line;

	return NULL;
}
"""
    descriptor_table = """struct plane_format {
	u32 format;
	pixel_read_line_t read_line;
};

static const struct plane_format plane_formats[] = {
	{ FORMAT_P010, p0xx_reader },
	{ FORMAT_YUV420, planar_reader },
};

"""
    validator = """bool registries_are_valid(void)
{
	for (unsigned int i = 0; i < ARRAY_SIZE(plane_formats); i++) {
		if (select_reader(plane_formats[i].format) !=
		    plane_formats[i].read_line)
			return false;
	}

	return true;
}
"""
    prior = "".join(f"prior-change-{number:03}\n" for number in range(1, 260))
    baseline = "prefix\n" + old_switch + "suffix\n"
    final = (
        "prefix\n" + prior + descriptor_table + batch_selector
        + "EXPORT(select_reader);\n" + validator + "suffix\n"
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    prior_ids = _display_id_range(view, "prior-change-001", "prior-change-259")
    git_stage_batch(
        "discard", "--to", "prior", "--line", prior_ids,
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = _display_id_for_text(view, "\tswitch (format) {")
    last = _display_id_for_text(view, "\treturn NULL;")
    batch_body = batch_selector.split("{\n", 1)[1]
    live_body = live_switch.split("{\n", 1)[1]
    result = git_stage_batch(
        "discard", "--to", "selector", "--line", f"{first}-{last}",
        "--as-stdin", "--no-auto-advance",
        input_text=batch_body + live_body,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_live = (
        "prefix\n" + descriptor_table + live_switch
        + "EXPORT(select_reader);\n" + validator + "suffix\n"
    )
    expected_batch = "prefix\n" + batch_selector + "suffix\n"
    assert path.read_text() == expected_live
    assert _show_file(
        functional_repo, "refs/git-stage-batch/batches/selector"
    ) == expected_batch
    assert "return &p0xx_reader;" in path.read_text()


def test_active_castkms_selector_preserves_intermediate_p0xx_callback(
    functional_repo,
):
    """A resumed multi-batch selector split must retain its P0XX callback."""
    fixture_packs = (
        next(FIXTURE_ROOT.glob("p0xx_selector_exact_v2-*.pack")),
        next(FIXTURE_ROOT.glob("p0xx_selector_exact_supplement-*.pack")),
        next(FIXTURE_ROOT.glob("p0xx_selector_exact_objects-*.pack")),
    )
    for fixture_pack in fixture_packs:
        subprocess.run(
            ["git", "index-pack", "--stdin"],
            cwd=functional_repo,
            input=fixture_pack.read_bytes(),
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [
            "git", "checkout", "--detach",
            "2447067692411cddc11a2ada3bc36ac676c0c3fe",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    refs_path = FIXTURE_ROOT / "p0xx_selector_exact_refs.txt"
    for line in refs_path.read_text().splitlines():
        ref, object_id = line.split()
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )

    git_dir = Path(
        subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    state_dir = git_dir / "git-stage-batch"
    state_dir.mkdir()
    session_archive = FIXTURE_ROOT / "p0xx_selector_exact_session.tar.gz"
    with tarfile.open(session_archive) as archive:
        archive.extractall(state_dir, filter="data")

    active_path = state_dir / "active-session.json"
    active = json.loads(active_path.read_text())
    active["worktree_git_dir"] = str(git_dir)
    active["marker_path"] = str(state_dir / "session/abort/head.txt")
    active_path.write_text(json.dumps(active, indent=2) + "\n")

    path = functional_repo / "src" / "castkms_formats.c"
    snapshot = state_dir / "session/selected/working-tree.snapshot"
    path.write_bytes(snapshot.read_bytes())
    live_before = path.read_text()
    batch_body = """\tfor (unsigned int i = 0; i < ARRAY_SIZE(castkms_plane_formats); i++)
\t\tif (castkms_plane_formats[i].format == format)
\t\t\treturn castkms_plane_formats[i].read_line;

\treturn NULL;
}
"""
    live_body = """\tswitch (format) {
\tcase DRM_FORMAT_ARGB8888:
\t\treturn &ARGB8888_read_line;
\tcase DRM_FORMAT_ABGR8888:
\t\treturn &ABGR8888_read_line;
\tcase DRM_FORMAT_BGRA8888:
\t\treturn &BGRA8888_read_line;
\tcase DRM_FORMAT_RGBA8888:
\t\treturn &RGBA8888_read_line;
\tcase DRM_FORMAT_XRGB8888:
\t\treturn &XRGB8888_read_line;
\tcase DRM_FORMAT_XBGR8888:
\t\treturn &XBGR8888_read_line;
\tcase DRM_FORMAT_RGB888:
\t\treturn &RGB888_read_line;
\tcase DRM_FORMAT_BGR888:
\t\treturn &BGR888_read_line;
\tcase DRM_FORMAT_ARGB16161616:
\t\treturn &ARGB16161616_read_line;
\tcase DRM_FORMAT_ABGR16161616:
\t\treturn &ABGR16161616_read_line;
\tcase DRM_FORMAT_XRGB16161616:
\t\treturn &XRGB16161616_read_line;
\tcase DRM_FORMAT_XBGR16161616:
\t\treturn &XBGR16161616_read_line;
\tcase DRM_FORMAT_RGB565:
\t\treturn &RGB565_read_line;
\tcase DRM_FORMAT_BGR565:
\t\treturn &BGR565_read_line;
\tcase DRM_FORMAT_NV12:
\tcase DRM_FORMAT_NV16:
\tcase DRM_FORMAT_NV24:
\tcase DRM_FORMAT_NV21:
\tcase DRM_FORMAT_NV61:
\tcase DRM_FORMAT_NV42:
\t\treturn &YUV888_semiplanar_read_line;
\tcase DRM_FORMAT_P010:
\tcase DRM_FORMAT_P012:
\tcase DRM_FORMAT_P016:
\t\treturn &P0XX_read_line;
\tcase DRM_FORMAT_YUV420:
\tcase DRM_FORMAT_YUV422:
\tcase DRM_FORMAT_YUV444:
\tcase DRM_FORMAT_YVU420:
\tcase DRM_FORMAT_YVU422:
\tcase DRM_FORMAT_YVU444:
\t\treturn &planar_yuv_read_line;
\tcase DRM_FORMAT_R1:
\t\treturn &R1_read_line;
\tcase DRM_FORMAT_R2:
\t\treturn &R2_read_line;
\tcase DRM_FORMAT_R4:
\t\treturn &R4_read_line;
\tcase DRM_FORMAT_R8:
\t\treturn &R8_read_line;
\tdefault:
\t\treturn NULL;
\t}
}
"""
    result = git_stage_batch(
        "discard", "--to", "decompose-14-plane-descriptor-provider",
        "--line", "280-350", "--as-stdin", "--no-auto-advance",
        input_text=batch_body + live_body,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_live = live_before.replace(batch_body, live_body)
    assert path.read_text() == expected_live
    assert "\t\treturn &P0XX_read_line;\n" in path.read_text()
    batch_after = _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/decompose-14-plane-descriptor-provider",
        "src/castkms_formats.c",
    )
    assert (
        "\tfor (unsigned int i = 0; "
        "i < ARRAY_SIZE(castkms_plane_formats); i++)\n"
    ) in batch_after
    assert "\treturn NULL;\n\t}\n}\n" not in batch_after
    assert "\t\treturn &P0XX_read_line;\n" not in batch_after


@pytest.mark.parametrize(
    ("line_ids", "replacement_text", "scoped_predecessor_blob"),
    [
        (
            "20",
            "\t\tu64 frame = "
            "drm_crtc_vblank_count_and_time(crtc, &frame_time);\n"
            "\t\tu64 frame = drm_crtc_vblank_count(crtc);\n",
            "8ba3d23ff689288e30090c2791ceedddb1d684c8",
        ),
        (
            "19-20",
            "\t\tktime_t frame_time;\n"
            "\t\tu64 frame = "
            "drm_crtc_vblank_count_and_time(crtc, &frame_time);\n"
            "\t\tu64 frame = drm_crtc_vblank_count(crtc);\n",
            "3181292108577b8e33e59532b9e6fe1462882107",
        ),
    ],
    ids=("single-addition", "contiguous-addition-span"),
)
def test_active_castkms_frame_transform_preserves_inner_addition_scope(
    functional_repo,
    line_ids,
    replacement_text,
    scoped_predecessor_blob,
):
    """A resumed concern-09 transform must not absorb unrelated additions."""
    for fixture_pack in sorted(FIXTURE_ROOT.glob("concern09_exact_*.pack")):
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
            "94b9e9d09cb77ab1cb39e954e3a826feb37cf4ac",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    refs_path = FIXTURE_ROOT / "concern09_exact_refs.txt"
    for line in refs_path.read_text().splitlines():
        ref, object_id = line.split()
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )

    git_dir = Path(
        subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    state_dir = git_dir / "git-stage-batch"
    state_dir.mkdir()
    with tarfile.open(FIXTURE_ROOT / "concern09_exact_session.tar.gz") as archive:
        archive.extractall(state_dir, filter="data")

    active_path = state_dir / "active-session.json"
    active = json.loads(active_path.read_text())
    active["worktree_git_dir"] = str(git_dir)
    active["marker_path"] = str(state_dir / "session/abort/head.txt")
    active_path.write_text(json.dumps(active, indent=2) + "\n")

    file_path = "src/castkms_crtc.c"
    path = functional_repo / file_path
    snapshot = state_dir / "session/selected/working-tree.snapshot"
    path.write_bytes(snapshot.read_bytes())

    def worktree_blob() -> str:
        return subprocess.run(
            ["git", "hash-object", file_path],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    final_blob = "fb851ea25295748ebf420f2538495d4dd8f384e2"
    assert worktree_blob() == final_blob
    view = git_stage_batch("show", "--file", file_path, "--page", "all").stdout
    assert (
        "[#20] + \t\tu64 frame = "
        "drm_crtc_vblank_count_and_time(crtc, &frame_time);"
    ) in view

    target_refs = (
        "refs/git-stage-batch/batches/decompose-09-implicit-frame-delivery",
        "refs/git-stage-batch/state/decompose-09-implicit-frame-delivery",
    )
    refs_before = subprocess.run(
        ["git", "rev-parse", *target_refs],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    sparse = git_stage_batch(
        "discard",
        "--to",
        "decompose-09-implicit-frame-delivery",
        "--line",
        "2,19-20",
        "--as-stdin",
        "--no-auto-advance",
        input_text=(
            "\t\tktime_t frame_time;\n"
            "\t\tu64 frame = "
            "drm_crtc_vblank_count_and_time(crtc, &frame_time);\n"
            "\t\tu64 frame = drm_crtc_vblank_count(crtc);\n"
        ),
        check=False,
    )
    assert sparse.returncode != 0
    assert "must be one contiguous line range" in sparse.stderr
    assert worktree_blob() == final_blob
    assert subprocess.run(
        ["git", "rev-parse", *target_refs],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout == refs_before

    result = git_stage_batch(
        "discard",
        "--to",
        "decompose-09-implicit-frame-delivery",
        "--line",
        line_ids,
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement_text,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert worktree_blob() == scoped_predecessor_blob
    git_stage_batch("stop")
    replay = git_stage_batch(
        "apply",
        "--from",
        "decompose-09-implicit-frame-delivery",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert worktree_blob() == final_blob


def test_new_batch_records_exact_addition_prefix_ownership(functional_repo):
    """A repeated suffix must not fragment a new batch's saved prefix."""
    prefix = (
        "#if FIRST\n"
        "first body\n"
        "shared line\n"
        "#endif /* FIRST end */\n"
    )
    suffix = (
        "#if SECOND\n"
        "second body\n"
        "shared line\n"
        "#endif /* SECOND end */\n"
    )
    path = _commit_file(functional_repo, "head\ntail\n")
    path.write_text("head\n" + prefix + "tail\n")

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    selected = _display_id_range(view, "#if FIRST", "FIRST end")
    result = git_stage_batch(
        "discard", "--to", "exact-prefix", "--line", selected,
        "--as-stdin", "--no-auto-advance", input_text=prefix + suffix,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == "head\n" + suffix + "tail\n"
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/exact-prefix",
    ) == "head\n" + prefix + "tail\n"


@pytest.mark.parametrize(
    ("batch_commit", "state_commit"),
    [
        (
            "01ef53c6d19c92d75904f93553b37a4854ea4fd5",
            "eb2cdbd0257fadc29745cc765d4f3cbb59d8f73c",
        ),
        (
            "574a682ae73673b4535271bd3e8cd1bc5b1dcd50",
            "efefc4889f52f848a14ec1af03a4863517c21c54",
        ),
    ],
    ids=("persisted-before-fix", "created-by-tentative-fix"),
)
def test_legacy_selector_batch_accepts_adjacent_validator_split(
    functional_repo, batch_commit, state_commit
):
    """A resumed selector batch should accept its adjacent validator split."""
    fixture_pack = FIXTURE_ROOT / "legacy_validator_append_exact.pack"
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git", "checkout", "--detach",
            "2447067692411cddc11a2ada3bc36ac676c0c3fe",
        ],
        cwd=functional_repo, check=True, capture_output=True,
    )

    persisted_refs = {
        "refs/git-stage-batch/batches/decompose-16-p0xx-padding":
            "40587f84c6b2c3619ff5c7969260f7561f34d8f1",
        "refs/git-stage-batch/state/decompose-16-p0xx-padding":
            "c02f15bc3200e36cbcfc88141a7cfb11e301c547",
        "refs/git-stage-batch/batches/"
        "decompose-10-writeback-advertisement-adopter":
            "14791e1b468a8c5e3e8781090993f2f6575ae5b8",
        "refs/git-stage-batch/state/"
        "decompose-10-writeback-advertisement-adopter":
            "8c6581150cad8d58894dd5b11c427648a32fe988",
        "refs/git-stage-batch/batches/"
        "decompose-11-writeback-descriptor-provider":
            batch_commit,
        "refs/git-stage-batch/state/"
        "decompose-11-writeback-descriptor-provider":
            state_commit,
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo, check=True, capture_output=True,
        )

    path = functional_repo / "src" / "castkms_formats.c"
    live_before = subprocess.run(
        ["git", "cat-file", "blob", "f20fbadc862ef1e22ee335aee2c561380e448b70"],
        cwd=functional_repo, check=True, capture_output=True,
    ).stdout.decode()
    path.write_text(live_before)
    batch_ref = (
        "refs/git-stage-batch/batches/"
        "decompose-11-writeback-descriptor-provider"
    )
    state_ref = (
        "refs/git-stage-batch/state/"
        "decompose-11-writeback-descriptor-provider"
    )
    batch_before = _show_file(
        functional_repo, batch_ref, "src/castkms_formats.c"
    )
    payload = (
        FIXTURE_ROOT / "legacy_validator_append_payload.c"
    ).read_text()
    full_body, plane_body = payload.split("#endif\n#if", 1)
    full_validator = full_body + "#endif\n"
    plane_validator = "#if" + plane_body
    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", "src/castkms_formats.c", "--page", "all"
    ).stdout
    assert "[#396] + #if IS_ENABLED(CONFIG_KUNIT)" in view
    assert "[#432] + #endif" in view

    result = git_stage_batch(
        "discard", "--to", "decompose-11-writeback-descriptor-provider",
        "--line", "396-432", "--as-stdin", "--no-auto-advance",
        input_text=payload, check=False,
    )
    if result.returncode:
        live_blob = subprocess.run(
            ["git", "hash-object", "src/castkms_formats.c"],
            cwd=functional_repo, check=True, capture_output=True, text=True,
        ).stdout.strip()
        current_refs = {
            ref: subprocess.run(
                ["git", "rev-parse", ref], cwd=functional_repo,
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            for ref in (batch_ref, state_ref)
        }
        assert live_blob == "f20fbadc862ef1e22ee335aee2c561380e448b70"
        assert current_refs == {
            batch_ref: batch_commit,
            state_ref: state_commit,
        }
    assert result.returncode == 0, result.stderr

    expected_live = live_before.replace(full_validator, plane_validator)
    expected_batch = batch_before + full_validator
    actual_live = path.read_text()
    actual_batch = _show_file(
        functional_repo,
        batch_ref,
        "src/castkms_formats.c",
    )
    assert (actual_live, actual_batch) == (expected_live, expected_batch)


def test_active_legacy_session_accepts_adjacent_validator_split(functional_repo):
    """A resumed active session should accept an adjacent validator split."""
    fixture_pack = FIXTURE_ROOT / "legacy_validator_append_exact.pack"
    subprocess.run(
        ["git", "index-pack", "--stdin"],
        cwd=functional_repo,
        input=fixture_pack.read_bytes(),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git", "checkout", "--detach",
            "2447067692411cddc11a2ada3bc36ac676c0c3fe",
        ],
        cwd=functional_repo, check=True, capture_output=True,
    )

    refs = FIXTURE_ROOT / "legacy_validator_append_session_refs.txt"
    for line in refs.read_text().splitlines():
        ref, object_id = line.split()
        if subprocess.run(
            ["git", "cat-file", "-e", object_id],
            cwd=functional_repo, capture_output=True,
        ).returncode:
            continue
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo, check=True, capture_output=True,
        )

    git_dir = Path(
        subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=functional_repo, check=True, capture_output=True, text=True,
        ).stdout.strip()
    )
    state_dir = git_dir / "git-stage-batch"
    state_dir.mkdir()
    session_archive = (
        FIXTURE_ROOT / "legacy_validator_append_session.tar.gz"
    )
    with tarfile.open(session_archive) as archive:
        archive.extractall(state_dir, filter="data")

    active_path = state_dir / "active-session.json"
    active = json.loads(active_path.read_text())
    active["worktree_git_dir"] = str(git_dir)
    active["marker_path"] = str(state_dir / "session/abort/head.txt")
    active_path.write_text(json.dumps(active, indent=2) + "\n")

    path = functional_repo / "src" / "castkms_formats.c"
    snapshot = state_dir / "session/selected/working-tree.snapshot"
    path.write_bytes(snapshot.read_bytes())
    batch_ref = (
        "refs/git-stage-batch/batches/"
        "decompose-11-writeback-descriptor-provider"
    )
    state_ref = (
        "refs/git-stage-batch/state/"
        "decompose-11-writeback-descriptor-provider"
    )
    assert subprocess.run(
        ["git", "rev-parse", batch_ref], cwd=functional_repo,
        check=True, capture_output=True, text=True,
    ).stdout.strip() == "574a682ae73673b4535271bd3e8cd1bc5b1dcd50"
    assert subprocess.run(
        ["git", "rev-parse", state_ref], cwd=functional_repo,
        check=True, capture_output=True, text=True,
    ).stdout.strip() == "efefc4889f52f848a14ec1af03a4863517c21c54"

    live_before = path.read_text()
    batch_before = _show_file(
        functional_repo, batch_ref, "src/castkms_formats.c"
    )
    payload = (
        FIXTURE_ROOT / "legacy_validator_append_payload.c"
    ).read_text()
    full_body, plane_body = payload.split("#endif\n#if", 1)
    full_validator = full_body + "#endif\n"
    plane_validator = "#if" + plane_body

    view = git_stage_batch(
        "show", "--file", "src/castkms_formats.c", "--page", "all"
    ).stdout
    assert "[#396] + #if IS_ENABLED(CONFIG_KUNIT)" in view
    assert "[#432] + #endif" in view
    result = git_stage_batch(
        "discard", "--to", "decompose-11-writeback-descriptor-provider",
        "--line", "396-432", "--as-stdin", "--no-auto-advance",
        input_text=payload, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text() == live_before.replace(
        full_validator, plane_validator
    )
    assert _show_file(
        functional_repo, batch_ref, "src/castkms_formats.c"
    ) == batch_before + full_validator


def test_transformed_append_splits_line_inside_later_added_function(
    functional_repo,
):
    """A transformed append may evolve a line in a later-added function."""
    baseline = """prefix
static int block_step(void)
{
	return BLOCK_WIDTH;
}
suffix
"""
    final = """prefix
static bool strides_are_valid(unsigned long stride)
{
	if (stride > SSIZE_MAX)
		return false;

	return true;
}

static ptrdiff_t block_step(void)
{
	return BLOCK_HEIGHT;
}
suffix
"""
    after_step_discard = final.replace("BLOCK_HEIGHT", "BLOCK_WIDTH")
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", "file.txt", "--page", "all"
    ).stdout
    step_ids = ",".join(
        (
            _display_id_for_text(view, "\treturn BLOCK_WIDTH;"),
            _display_id_for_text(view, "\treturn BLOCK_HEIGHT;"),
        )
    )
    git_stage_batch(
        "discard", "--to", "pointer-width", "--line", step_ids,
        "--no-auto-advance",
    )

    assert path.read_text() == after_step_discard
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/pointer-width",
    ) == baseline.replace("BLOCK_WIDTH", "BLOCK_HEIGHT")

    view = git_stage_batch(
        "show", "--file", "file.txt", "--page", "all"
    ).stdout
    limit_id = _display_id_for_text(view, "\tif (stride > SSIZE_MAX)")
    batch_ref = "refs/git-stage-batch/batches/pointer-width"
    state_ref = "refs/git-stage-batch/state/pointer-width"
    live_before = path.read_text()
    batch_before = _show_file(functional_repo, batch_ref)
    refs_before = subprocess.run(
        ["git", "rev-parse", batch_ref, state_ref],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result = git_stage_batch(
        "discard", "--to", "pointer-width", "--line", limit_id,
        "--as-stdin", "--no-auto-advance",
        input_text=(
            "\tif (stride > SSIZE_MAX)\n"
            "\tif (stride > INT_MAX)\n"
        ),
        check=False,
    )

    if result.returncode:
        assert path.read_text() == live_before
        assert _show_file(functional_repo, batch_ref) == batch_before
        assert subprocess.run(
            ["git", "rev-parse", batch_ref, state_ref],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout == refs_before
    assert result.returncode == 0, result.stderr
    assert path.read_text() == after_step_discard.replace(
        "\tif (stride > SSIZE_MAX)\n",
        "\tif (stride > INT_MAX)\n",
    )
    assert _show_file(functional_repo, batch_ref) == baseline.replace(
        "\treturn BLOCK_WIDTH;\n",
        "\tif (stride > SSIZE_MAX)\n\treturn BLOCK_HEIGHT;\n",
    )
    state = json.loads(_show_file(functional_repo, state_ref, "batch.json"))
    file_metadata = state["files"]["file.txt"]
    source_lines = _show_file(
        functional_repo,
        file_metadata["batch_source_commit"],
    ).splitlines()
    limit_source_line = source_lines.index("\tif (stride > SSIZE_MAX)") + 1
    assert any(
        str(limit_source_line) in claim.get("baseline_references", {})
        for claim in file_metadata["presence_claims"]
    )
    assert sum(
        deletion.get("source_alternative") is True
        for deletion in file_metadata["deletions"]
    ) == 1

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Commit the live stride guard"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    result = git_stage_batch("apply", "--from", "pointer-width", check=False)
    if result.returncode:
        assert "has 1 apply candidate" in result.stderr
        git_stage_batch(
            "show",
            "--from",
            "pointer-width:apply:1",
            "--file",
            "file.txt",
        )
        result = git_stage_batch(
            "apply",
            "--from",
            "pointer-width:apply:1",
            "--file",
            "file.txt",
            check=False,
        )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == final
def test_expanded_provider_list_stays_before_heading_after_same_batch_peels(
    functional_repo,
):
    """A provider-list snapshot must remain contiguous with earlier batch peels."""
    baseline = "head\n\n## Graphical testing\n\ngraphical details\n"
    source = (
        "head\n\n"
        "```sh\nSCENARIO=capture run-test\n```\n\n"
        "The available scenarios are `configfs` and `capture`. The default runs\n"
        "them in that order.\n\n"
        "**capture** checks frame delivery.\n\n"
        "## Graphical testing\n\n"
        "graphical details\n"
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(source)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    capture_first = int(_display_id_for_text(view, "**capture**"))
    capture_last = capture_first
    capture_result = git_stage_batch(
        "discard",
        "--to",
        "capture-provider",
        "--line",
        f"{capture_first}-{capture_last + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert capture_result.returncode == 0, capture_result.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    command_id = int(_display_id_for_text(view, "SCENARIO=capture run-test"))
    before_selector = path.read_text()
    selector_result = git_stage_batch(
        "discard",
        "--to",
        "capture-provider",
        "--line",
        f"{command_id - 1}-{command_id + 2}",
        "--no-auto-advance",
        check=False,
    )
    assert selector_result.returncode == 0, selector_result.stderr

    configfs_selector = before_selector.replace(
        "SCENARIO=capture run-test",
        "SCENARIO=configfs run-test",
    )
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    restore_selector = git_stage_batch(
        "discard",
        "--file",
        "file.txt",
        "--as-stdin",
        "--no-auto-advance",
        input_text=configfs_selector,
        check=False,
    )
    assert restore_selector.returncode == 0, restore_selector.stderr

    one_provider = configfs_selector.replace(
        "The available scenarios are `configfs` and `capture`. The default runs\n"
        "them in that order.\n",
        "The available scenario is `configfs`. The default runs it.\n",
    )
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    narrow_list = git_stage_batch(
        "discard",
        "--file",
        "file.txt",
        "--as-stdin",
        "--no-auto-advance",
        input_text=one_provider,
        check=False,
    )
    assert narrow_list.returncode == 0, narrow_list.stderr

    expected_partial = baseline.replace(
        "## Graphical testing\n",
        (
            "```sh\nSCENARIO=capture run-test\n```\n\n"
            "**capture** checks frame delivery.\n\n"
            "## Graphical testing\n"
        ),
    )
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/capture-provider",
        )
        == expected_partial
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    list_id = int(_display_id_for_text(view, "The available scenario is"))
    replacement = (
        "The available scenarios are `configfs` and `capture`. The default runs\n"
        "them in that order.\n\n"
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "capture-provider",
        "--line",
        f"{list_id}-{list_id + 1}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=replacement,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_owned = (
        "```sh\nSCENARIO=capture run-test\n```\n\n"
        + replacement
        + "**capture** checks frame delivery.\n\n"
    )
    expected = baseline.replace(
        "## Graphical testing\n",
        expected_owned + "## Graphical testing\n",
    )
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/capture-provider",
        )
        == expected
    )


def test_repeeled_transformed_tail_stays_before_retained_suffix(
    functional_repo,
):
    """A transformed predecessor tail must remain ahead of its scaffold suffix."""
    prefix = "# Guide\n\n"
    final_region = "shared line\nfinal tail\n\n"
    predecessor_region = "shared line\npredecessor tail\n\n"
    suffix = "## Build\n\nbuild details\n"
    final = prefix + final_region + suffix
    predecessor = prefix + predecessor_region + suffix
    path = functional_repo / "guide.md"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch(
        "discard",
        "--to",
        "predecessor-scaffold",
        "--file",
        "guide.md",
        "--no-auto-advance",
    )
    git_stage_batch(
        "apply",
        "--from",
        "predecessor-scaffold",
        "--file",
        "guide.md",
    )

    batch_view = git_stage_batch(
        "show",
        "--from",
        "predecessor-scaffold",
        "--file",
        "guide.md",
        "--page",
        "all",
    ).stdout
    first = int(_display_id_for_text(batch_view, "shared line"))
    last = int(_display_id_for_text(batch_view, "final tail")) + 1
    git_stage_batch(
        "reset",
        "--from",
        "predecessor-scaffold",
        "--line",
        f"{first}-{last}",
    )
    git_stage_batch(
        "discard",
        "--from",
        "predecessor-scaffold",
        "--file",
        "guide.md",
    )
    assert path.read_text() == final_region

    live_view = git_stage_batch("show", "--file", "guide.md", "--page", "all").stdout
    first = int(_display_id_for_text(live_view, "shared line"))
    last = int(_display_id_for_text(live_view, "final tail")) + 1
    transform = git_stage_batch(
        "discard",
        "--to",
        "grant-adopter",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=final_region + predecessor_region,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor_region
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/grant-adopter",
            "guide.md",
        )
        == final_region
    )

    live_view = git_stage_batch("show", "--file", "guide.md", "--page", "all").stdout
    first = int(_display_id_for_text(live_view, "shared line"))
    last = int(_display_id_for_text(live_view, "predecessor tail")) + 1
    repeel = git_stage_batch(
        "discard",
        "--to",
        "predecessor-scaffold",
        "--line",
        f"{first}-{last}",
        "--no-auto-advance",
        check=False,
    )
    assert repeel.returncode == 0, repeel.stderr
    actual = _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/predecessor-scaffold",
        "guide.md",
    )
    assert actual == predecessor


def test_transformed_batch_replays_after_predecessor_scaffold_is_dropped(
    functional_repo,
):
    """Dropping a temporary scaffold must not invalidate its transformed peer."""
    prefix = "# Guide\n\n"
    final_region = "shared line\nfinal tail\n\n"
    predecessor_region = "shared line\npredecessor tail\n\n"
    suffix = "## Build\n\nbuild details\n"
    final = prefix + final_region + suffix
    predecessor = prefix + predecessor_region + suffix
    path = functional_repo / "guide.md"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch(
        "discard",
        "--to",
        "predecessor-scaffold",
        "--file",
        "guide.md",
        "--no-auto-advance",
    )
    git_stage_batch(
        "apply",
        "--from",
        "predecessor-scaffold",
        "--file",
        "guide.md",
    )

    batch_view = git_stage_batch(
        "show",
        "--from",
        "predecessor-scaffold",
        "--file",
        "guide.md",
        "--page",
        "all",
    ).stdout
    first = int(_display_id_for_text(batch_view, "shared line"))
    last = int(_display_id_for_text(batch_view, "final tail")) + 1
    git_stage_batch(
        "reset",
        "--from",
        "predecessor-scaffold",
        "--line",
        f"{first}-{last}",
    )
    git_stage_batch(
        "discard",
        "--from",
        "predecessor-scaffold",
        "--file",
        "guide.md",
    )
    assert path.read_text() == final_region

    live_view = git_stage_batch("show", "--file", "guide.md", "--page", "all").stdout
    first = int(_display_id_for_text(live_view, "shared line"))
    last = int(_display_id_for_text(live_view, "final tail")) + 1
    transform = git_stage_batch(
        "discard",
        "--to",
        "grant-adopter",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=final_region + predecessor_region,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor_region
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/grant-adopter",
            "guide.md",
        )
        == final_region
    )

    live_view = git_stage_batch("show", "--file", "guide.md", "--page", "all").stdout
    first = int(_display_id_for_text(live_view, "shared line"))
    last = int(_display_id_for_text(live_view, "predecessor tail")) + 1
    repeel = git_stage_batch(
        "discard",
        "--to",
        "predecessor-scaffold",
        "--line",
        f"{first}-{last}",
        "--no-auto-advance",
        check=False,
    )
    assert repeel.returncode == 0, repeel.stderr
    if path.exists():
        cleanup = git_stage_batch(
            "discard",
            "--file",
            "guide.md",
            "--no-auto-advance",
            check=False,
        )
        assert cleanup.returncode == 0, cleanup.stderr
    assert not path.exists()
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/predecessor-scaffold",
            "guide.md",
        )
        == predecessor
    )

    git_stage_batch(
        "apply",
        "--from",
        "predecessor-scaffold",
        "--file",
        "guide.md",
    )
    assert path.read_text() == predecessor
    git_stage_batch(
        "reset",
        "--from",
        "predecessor-scaffold",
        "--file",
        "guide.md",
    )
    git_stage_batch("drop", "predecessor-scaffold")
    assert path.read_text() == predecessor

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "guide.md"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "grant-adopter",
        "--file",
        "guide.md",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_transformed_matrix_row_stays_between_adjacent_siblings(functional_repo):
    """An F7-to-P5 matrix transform must retain its predecessor row."""
    baseline = "# build rules\n\nall:\n\tbuild module\n"
    first_row = (
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CONFIG_SND=n\n"
        "\ttest ! -e src/castkms_audio.o\n"
        '\tcase "$$(modinfo -F depends ./castkms.ko)" in *snd*) false;; esac\n'
        '\tcase "$$(modinfo -F softdep ./castkms.ko)" in *snd*) false;; esac\n'
    )
    second_final = (
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=n CASTKMS_BUILD_CEC=y\n"
        "\ttest ! -e src/castkms_audio.o\n"
        "\ttest -e src/castkms_cec_core.o\n"
        "\ttest -e src/castkms_cec_uapi.o\n"
        '\tcase "$$(modinfo -F depends ./castkms.ko)" in *snd*) false;; esac\n'
        '\tcase "$$(modinfo -F softdep ./castkms.ko)" in *snd*) false;; esac\n'
    )
    second_predecessor = (
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=n\n"
        "\ttest ! -e src/castkms_audio.o\n"
        '\tcase "$$(modinfo -F depends ./castkms.ko)" in *snd*) false;; esac\n'
        '\tcase "$$(modinfo -F softdep ./castkms.ko)" in *snd*) false;; esac\n'
    )
    third_row = (
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=y CASTKMS_BUILD_CEC=y\n"
        "\ttest -e src/castkms_audio.o\n"
        "\ttest -e src/castkms_cec_core.o\n"
        "\ttest -e src/castkms_cec_uapi.o\n"
        "\tcheck undefined symbols\n"
    )
    final = (
        "# build rules\n"
        "CASTKMS_BUILD_AUDIO ?= y\n"
        "\n"
        "CASTKMS_KBUILD_OPTIONS := \\\n"
        "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO)\n\n"
        "all:\n"
        "\tbuild module\n\n"
        "build-matrix:\n"
        + first_row
        + second_final
        + third_row
        + "\ncheck:\n\tverify\n"
    )
    predecessor = final.replace(second_final, second_predecessor)

    path = functional_repo / "Makefile"
    path.write_text(baseline)
    subprocess.run(
        ["git", "add", "Makefile"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add build rules"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    build_id = int(
        _display_id_for_text(
            view,
            "\t$(MAKE) all CASTKMS_BUILD_AUDIO=n CASTKMS_BUILD_CEC=y",
        )
    )
    transform = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        f"{build_id - 1}-{build_id + 5}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=second_predecessor,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/cec-build-selection",
            "Makefile",
        )
        == final
    )

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "Makefile"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add CEC build predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "cec-build-selection",
        "--file",
        "Makefile",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final

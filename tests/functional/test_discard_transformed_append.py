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
GUEST_SMOKE_SHARED_LINE = '"$runtime_dir/unplug-gate" "$runtime_dir/mode-gate"'
GUEST_SMOKE_REPLACEMENT = (
    '\t\t\t"$runtime_dir/mode-gate"\n\t\t\t"$runtime_dir/unplug-gate"\n'
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
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return matches[0].split("[#", 1)[1].split("]", 1)[0]


def _display_id_range(view, first_text, last_text):
    first = int(_display_id_for_text(view, first_text))
    last = int(_display_id_for_text(view, last_text))
    assert first <= last
    return f"{first}-{last}"


def _assert_whole_file_replacement_geometry(
    functional_repo,
    *,
    batch_name,
    file_path,
    predecessor,
    final,
):
    state = json.loads(
        _show_file(
            functional_repo,
            f"refs/git-stage-batch/state/{batch_name}",
            "batch.json",
        )
    )
    file_metadata = state["files"][file_path]
    assert (
        _show_file(
            functional_repo,
            file_metadata["batch_source_commit"],
            file_path,
        )
        == predecessor + final
    )
    assert len(file_metadata["deletions"]) == 1
    assert file_metadata["deletions"][0]["source_alternative"] is True
    predecessor_line_count = len(predecessor.splitlines())
    assert file_metadata["replacement_units"] == [
        {
            "presence_lines": [f"1-{predecessor_line_count}"],
            "deletion_indices": [0],
        }
    ]


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
    view = git_stage_batch("show", "--file", "guide.md", "--page", "all").stdout
    sentence_id = _display_id_for_text(view, "CI runs three lanes on every push:")
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
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
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
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
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
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
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
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/complete-rewrite",
        )
        == "head\nsaved-a\nsaved-b\ntail\n"
    )

    git_stage_batch("stop")
    replay = git_stage_batch("apply", "--from", "complete-rewrite", check=False)
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == "head\nsaved-a\nsaved-b\ntail\n"


def test_transformed_preamble_stays_before_following_markdown_heading(
    functional_repo,
):
    """A shortened added preamble must stay contiguous before its heading."""
    path = _commit_file(
        functional_repo,
        "head\nlegacy workflow\n\n## Graphical testing\nlegacy graphical details\n",
    )
    path.write_text(
        "head\n"
        "new workflow\n\n"
        "## Product scenarios\n\n"
        "`test` starts with module identification and teardown. Unless\n"
        "`FAST_GATE=skip` is set, it first runs the build matrix plus KUnit\n"
        "and grant gate. The product work is selectable:\n\n"
        "```sh\n"
        "run-product-test\n"
        "```\n\n"
        "## Graphical testing\n"
        "new graphical details\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    selected = _display_id_range(
        view,
        "`test` starts with module identification and teardown. Unless",
        "and grant gate. The product work is selectable:",
    )
    replacement = (
        "`test` starts with module identification and teardown. Unless\n"
        "`FAST_GATE=skip` is set, it first runs the build matrix plus KUnit\n"
        "and grant gate.\n"
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "guest-kunit-runner",
        "--line",
        selected,
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    batch_content = _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/guest-kunit-runner",
    )
    assert replacement + "## Graphical testing\n" in batch_content


def test_transformed_preamble_stays_before_heading_after_prior_doc_batches(
    functional_repo,
):
    """A transformed preamble must retain its boundary after earlier peels."""
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

## Graphical testing

graphical details
"""
    source_text = """`provision` downloads the base image, creates a 30 GiB sparse overlay, boots
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

CI runs on every pull request and push to `main`:

- **Userspace / protocol and entrypoints**: `make check` on the host,
  including the EDID suite and every available CLI entrypoint.
- **Fast / KUnit and grant security**.

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

The default SSH forward is `127.0.0.1:22222`. Override it when running more
than one instance:

```sh
CASTKMS_VM_INSTANCE=second \
CASTKMS_VM_SSH_PORT=22223 \
./scripts/vm/castkms-vm provision
```

`reset` stops the guest and moves its instance directory into the state
directory's `archive/` folder before creating a fresh overlay. The downloaded
base image and SSH key are retained.

## Product scenarios

`test` starts with module identification and teardown. Unless
`CASTKMS_VM_FAST_GATE=skip` is set, it first runs the build matrix plus KUnit
and grant gate. The product work is selectable:

```sh
CASTKMS_VM_SCENARIO=capture ./scripts/vm/castkms-vm test
```

## Graphical testing

graphical details
"""
    path = _commit_file(functional_repo, baseline_text)
    path.write_text(source_text)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    prior_ci = _display_id_range(
        view,
        "CI runs on every pull request and push to `main`:",
        "- **Fast / KUnit and grant security**.",
    )
    git_stage_batch(
        "discard",
        "--to",
        "prior-ci-docs",
        "--line",
        prior_ci,
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    preamble = _display_id_range(
        view,
        "`test` starts with module identification and teardown. Unless",
        "and grant gate. The product work is selectable:",
    )
    replacement = (
        "`test` starts with module identification and teardown. Unless\n"
        "`CASTKMS_VM_FAST_GATE=skip` is set, it first runs the build matrix plus KUnit\n"
        "and grant gate.\n"
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "guest-kunit-runner",
        "--line",
        preamble,
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/guest-kunit-runner",
    ) == baseline_text.replace(
        "## Graphical testing\n",
        replacement + "## Graphical testing\n",
    )


def test_added_transition_stays_after_closed_markdown_fence(functional_repo):
    """A later peel must remain after the completed old results block."""
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
    source_text = """`provision` downloads the base image, creates a 30 GiB sparse overlay, boots
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

CI runs on every pull request and push to `main`:

- **Userspace / protocol and entrypoints**: `make check` on the host,
  including the EDID suite and every available CLI entrypoint.
- **Fast / KUnit and grant security**.

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
    path.write_text(source_text)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    prior_block = _display_id_range(
        view,
        "CI runs on every pull request and push to `main`:",
        "- **Fast / KUnit and grant security**.",
    )
    git_stage_batch(
        "discard",
        "--to",
        "prior-docs-batch",
        "--line",
        prior_block,
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    transition = _display_id_range(
        view,
        "The broader `test` command runs that fast gate first, reuses its build",
        "artifacts, then builds the userspace protocol and PipeWire tests and runs the",
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "guest-kunit-runner",
        "--line",
        transition,
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    batch_content = _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/guest-kunit-runner",
    )
    transition_text = (
        "The broader `test` command runs that fast gate first, reuses its build\n"
        "artifacts, then builds the userspace protocol and PipeWire tests and runs the\n"
    )
    assert batch_content == baseline_text.replace(
        "Useful commands:\n",
        transition_text + "\nUseful commands:\n",
    )


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
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
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
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/baseline-wording",
        )
        == "head\nold one\nold two\nold one\ntail\n"
    )

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
        f"remaining_ids={GUEST_SMOKE_EXACT_LINE_IDS}\n{exact_result.stderr}"
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
        "header\nscan-old-one\nscan-old-two\nclean-anchor\nruntime-anchor\nfooter\n",
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
        "{\n" + batch_selector + "footer\n"
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
        "prefix\n"
        + prior
        + descriptors
        + "/**\n"
        + new_doc
        + batch_selector
        + selector_export
        + validator
        + "suffix\n"
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
        "include",
        "--to",
        "descriptor-context",
        "--line",
        f"{prior_first}-{descriptor_last}",
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = int(_display_id_for_text(view, "Retrieve the correct write_pixel"))
    last = int(_display_id_for_text(view, "\treturn NULL;")) + 1
    payload = (
        FIXTURE_ROOT / "discard_transformed_selector_cross_context.txt"
    ).read_text()
    result = git_stage_batch(
        "discard",
        "--to",
        "selector",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=payload,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_live = (
        "prefix\n"
        + prior
        + descriptors
        + "/**\n"
        + old_doc
        + live_switch
        + selector_export
        + validator
        + "suffix\n"
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
        "prefix\n"
        + prior
        + descriptor_table
        + batch_selector
        + "EXPORT(select_reader);\n"
        + validator
        + "suffix\n"
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    prior_ids = _display_id_range(view, "prior-change-001", "prior-change-259")
    git_stage_batch(
        "discard",
        "--to",
        "prior",
        "--line",
        prior_ids,
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = _display_id_for_text(view, "\tswitch (format) {")
    last = _display_id_for_text(view, "\treturn NULL;")
    batch_body = batch_selector.split("{\n", 1)[1]
    live_body = live_switch.split("{\n", 1)[1]
    result = git_stage_batch(
        "discard",
        "--to",
        "selector",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=batch_body + live_body,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_live = (
        "prefix\n"
        + descriptor_table
        + live_switch
        + "EXPORT(select_reader);\n"
        + validator
        + "suffix\n"
    )
    expected_batch = "prefix\n" + batch_selector + "suffix\n"
    assert path.read_text() == expected_live
    assert (
        _show_file(functional_repo, "refs/git-stage-batch/batches/selector")
        == expected_batch
    )
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
            "git",
            "checkout",
            "--detach",
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
        "discard",
        "--to",
        "decompose-14-plane-descriptor-provider",
        "--line",
        "280-350",
        "--as-stdin",
        "--no-auto-advance",
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
        "\tfor (unsigned int i = 0; i < ARRAY_SIZE(castkms_plane_formats); i++)\n"
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
        "[#20] + \t\tu64 frame = drm_crtc_vblank_count_and_time(crtc, &frame_time);"
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
    assert (
        subprocess.run(
            ["git", "rev-parse", *target_refs],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == refs_before
    )

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
    prefix = "#if FIRST\nfirst body\nshared line\n#endif /* FIRST end */\n"
    suffix = "#if SECOND\nsecond body\nshared line\n#endif /* SECOND end */\n"
    path = _commit_file(functional_repo, "head\ntail\n")
    path.write_text("head\n" + prefix + "tail\n")

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    selected = _display_id_range(view, "#if FIRST", "FIRST end")
    result = git_stage_batch(
        "discard",
        "--to",
        "exact-prefix",
        "--line",
        selected,
        "--as-stdin",
        "--no-auto-advance",
        input_text=prefix + suffix,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == "head\n" + suffix + "tail\n"
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/exact-prefix",
        )
        == "head\n" + prefix + "tail\n"
    )


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
            "git",
            "checkout",
            "--detach",
            "2447067692411cddc11a2ada3bc36ac676c0c3fe",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    persisted_refs = {
        "refs/git-stage-batch/batches/decompose-16-p0xx-padding": "40587f84c6b2c3619ff5c7969260f7561f34d8f1",
        "refs/git-stage-batch/state/decompose-16-p0xx-padding": "c02f15bc3200e36cbcfc88141a7cfb11e301c547",
        "refs/git-stage-batch/batches/"
        "decompose-10-writeback-advertisement-adopter": "14791e1b468a8c5e3e8781090993f2f6575ae5b8",
        "refs/git-stage-batch/state/"
        "decompose-10-writeback-advertisement-adopter": "8c6581150cad8d58894dd5b11c427648a32fe988",
        "refs/git-stage-batch/batches/"
        "decompose-11-writeback-descriptor-provider": batch_commit,
        "refs/git-stage-batch/state/"
        "decompose-11-writeback-descriptor-provider": state_commit,
    }
    for ref, object_id in persisted_refs.items():
        subprocess.run(
            ["git", "update-ref", ref, object_id],
            cwd=functional_repo,
            check=True,
            capture_output=True,
        )

    path = functional_repo / "src" / "castkms_formats.c"
    live_before = subprocess.run(
        ["git", "cat-file", "blob", "f20fbadc862ef1e22ee335aee2c561380e448b70"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    ).stdout.decode()
    path.write_text(live_before)
    batch_ref = (
        "refs/git-stage-batch/batches/decompose-11-writeback-descriptor-provider"
    )
    state_ref = "refs/git-stage-batch/state/decompose-11-writeback-descriptor-provider"
    batch_before = _show_file(functional_repo, batch_ref, "src/castkms_formats.c")
    payload = (FIXTURE_ROOT / "legacy_validator_append_payload.c").read_text()
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
        "discard",
        "--to",
        "decompose-11-writeback-descriptor-provider",
        "--line",
        "396-432",
        "--as-stdin",
        "--no-auto-advance",
        input_text=payload,
        check=False,
    )
    if result.returncode:
        live_blob = subprocess.run(
            ["git", "hash-object", "src/castkms_formats.c"],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        current_refs = {
            ref: subprocess.run(
                ["git", "rev-parse", ref],
                cwd=functional_repo,
                check=True,
                capture_output=True,
                text=True,
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
            "git",
            "checkout",
            "--detach",
            "2447067692411cddc11a2ada3bc36ac676c0c3fe",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    refs = FIXTURE_ROOT / "legacy_validator_append_session_refs.txt"
    for line in refs.read_text().splitlines():
        ref, object_id = line.split()
        if subprocess.run(
            ["git", "cat-file", "-e", object_id],
            cwd=functional_repo,
            capture_output=True,
        ).returncode:
            continue
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
    session_archive = FIXTURE_ROOT / "legacy_validator_append_session.tar.gz"
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
        "refs/git-stage-batch/batches/decompose-11-writeback-descriptor-provider"
    )
    state_ref = "refs/git-stage-batch/state/decompose-11-writeback-descriptor-provider"
    assert (
        subprocess.run(
            ["git", "rev-parse", batch_ref],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "574a682ae73673b4535271bd3e8cd1bc5b1dcd50"
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", state_ref],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "efefc4889f52f848a14ec1af03a4863517c21c54"
    )

    live_before = path.read_text()
    batch_before = _show_file(functional_repo, batch_ref, "src/castkms_formats.c")
    payload = (FIXTURE_ROOT / "legacy_validator_append_payload.c").read_text()
    full_body, plane_body = payload.split("#endif\n#if", 1)
    full_validator = full_body + "#endif\n"
    plane_validator = "#if" + plane_body

    view = git_stage_batch(
        "show", "--file", "src/castkms_formats.c", "--page", "all"
    ).stdout
    assert "[#396] + #if IS_ENABLED(CONFIG_KUNIT)" in view
    assert "[#432] + #endif" in view
    result = git_stage_batch(
        "discard",
        "--to",
        "decompose-11-writeback-descriptor-provider",
        "--line",
        "396-432",
        "--as-stdin",
        "--no-auto-advance",
        input_text=payload,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text() == live_before.replace(full_validator, plane_validator)
    assert (
        _show_file(functional_repo, batch_ref, "src/castkms_formats.c")
        == batch_before + full_validator
    )


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
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    step_ids = ",".join(
        (
            _display_id_for_text(view, "\treturn BLOCK_WIDTH;"),
            _display_id_for_text(view, "\treturn BLOCK_HEIGHT;"),
        )
    )
    git_stage_batch(
        "discard",
        "--to",
        "pointer-width",
        "--line",
        step_ids,
        "--no-auto-advance",
    )

    assert path.read_text() == after_step_discard
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/pointer-width",
    ) == baseline.replace("BLOCK_WIDTH", "BLOCK_HEIGHT")

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
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
        "discard",
        "--to",
        "pointer-width",
        "--line",
        limit_id,
        "--as-stdin",
        "--no-auto-advance",
        input_text=("\tif (stride > SSIZE_MAX)\n\tif (stride > INT_MAX)\n"),
        check=False,
    )

    if result.returncode:
        assert path.read_text() == live_before
        assert _show_file(functional_repo, batch_ref) == batch_before
        assert (
            subprocess.run(
                ["git", "rev-parse", batch_ref, state_ref],
                cwd=functional_repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            == refs_before
        )
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
    assert (
        sum(
            deletion.get("source_alternative") is True
            for deletion in file_metadata["deletions"]
        )
        == 1
    )

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


def test_transformed_provider_snapshot_replaces_predecessor_before_heading(
    functional_repo,
):
    """A peeled provider snapshot must replace its committed predecessor."""
    baseline = "head\n\n## Graphical testing\n\ngraphical details\n"
    final_block = (
        "The available scenarios are `configfs`, `capture`, `cursor`, and\n"
        "`pipewire-audio`. The default `all` runs them in that order.\n\n"
    )
    predecessor_block = (
        "The available scenarios are `configfs`, `capture`, and `cursor`. The default\n"
        "`all` runs them in that order.\n\n"
    )
    final = baseline.replace(
        "## Graphical testing\n",
        final_block + "## Graphical testing\n",
    )
    predecessor = baseline.replace(
        "## Graphical testing\n",
        predecessor_block + "## Graphical testing\n",
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = int(_display_id_for_text(view, "The available scenarios"))
    last = int(_display_id_for_text(view, "`pipewire-audio`"))
    result = git_stage_batch(
        "discard",
        "--to",
        "pipewire-provider",
        "--line",
        f"{first}-{last + 1}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=final_block + predecessor_block,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/pipewire-provider",
        )
        == final
    )

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add predecessor provider snapshot"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "pipewire-provider",
        "--file",
        "file.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_rebuilt_batch_forgets_identical_removed_function_tail(functional_repo):
    """A rebuilt batch must not inherit an identical peeled sibling tail."""
    baseline = (
        "#!/usr/bin/env bash\n\n"
        "provision()\n"
        "{\n"
        "\t: provision\n"
        "}\n\n"
        "sync_source()\n"
        "{\n"
        "\t: sync\n"
        "}\n\n"
        'case "${1:-}" in\n'
        "\tsync) sync_source ;;\n"
        "esac\n"
    )
    provision_desktop = (
        "provision_desktop()\n"
        "{\n"
        "\tprovision\n"
        "\tensure_desktop_attach\n"
        "\tprintf 'ready %s\\n' \\\n"
        '\t\t"$display"\n'
        "}\n\n"
    )
    attach_helper = "ensure_desktop_attach()\n{\n\t: attach\n}\n\n"
    start_desktop = (
        "start_desktop()\n"
        "{\n"
        "\tstart\n"
        "\tensure_desktop_attach\n"
        "\tprintf 'ready %s\\n' \\\n"
        '\t\t"$display"\n'
        "}\n\n"
    )
    attach_dispatch = "\tdesktop-attach)\n\t\tensure_desktop_attach\n\t\t;;\n"
    source = baseline.replace(
        "sync_source()\n",
        provision_desktop + attach_helper + start_desktop + "sync_source()\n",
    ).replace(
        "\tsync) sync_source ;;\n",
        attach_dispatch + "\tsync) sync_source ;;\n",
    )
    final = source.replace(start_desktop, "")
    predecessor = (
        final.replace(attach_helper, "")
        .replace(attach_dispatch, "")
        .replace("\tensure_desktop_attach\n", "")
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(source)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    start_first = int(_display_id_for_text(view, "start_desktop()"))
    start_peel = git_stage_batch(
        "discard",
        "--to",
        "desktop-start-route",
        "--line",
        f"{start_first}-{start_first + 7}",
        "--no-auto-advance",
        check=False,
    )
    assert start_peel.returncode == 0, start_peel.stderr
    assert path.read_text() == final

    def peel_attach_route():
        view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
        helper_first = int(_display_id_for_text(view, "ensure_desktop_attach()"))
        dispatch_first = int(_display_id_for_text(view, "\tdesktop-attach)"))
        dispatch_call = int(_display_id_for_text(view, "\t\tensure_desktop_attach"))
        peel = git_stage_batch(
            "discard",
            "--to",
            "desktop-attach-route",
            "--line",
            (f"{helper_first}-{helper_first + 4},{dispatch_first}-{dispatch_call + 1}"),
            "--no-auto-advance",
            check=False,
        )
        assert peel.returncode == 0, peel.stderr

    peel_attach_route()
    assert path.read_text() == final.replace(attach_helper, "").replace(
        attach_dispatch, ""
    )
    restore = git_stage_batch(
        "apply",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
        check=False,
    )
    assert restore.returncode == 0, restore.stderr
    assert path.read_text() == final
    git_stage_batch(
        "reset",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
    )
    assert path.read_text() == final

    peel_attach_route()
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    function_first = int(_display_id_for_text(view, "provision_desktop()"))
    adoption_id = int(_display_id_for_text(view, "\tensure_desktop_attach"))
    display_id = int(_display_id_for_text(view, '\t\t"$display"'))
    function_last = display_id + 2
    context = git_stage_batch(
        "include",
        "--to",
        "desktop-attach-route",
        "--line",
        f"{function_first}-{adoption_id - 1},{adoption_id + 1}-{function_last}",
        "--no-auto-advance",
        check=False,
    )
    assert context.returncode == 0, context.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    adoption_id = _display_id_for_text(view, "\tensure_desktop_attach")
    adoption = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        adoption_id,
        "--no-auto-advance",
        check=False,
    )
    assert adoption.returncode == 0, adoption.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/desktop-attach-route",
        )
        == final
    )

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add desktop provision predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_transformed_product_section_stays_before_following_fenced_section(
    functional_repo,
):
    """A peeled added section must remain before its stable next heading."""
    baseline = (
        "head\n\n"
        "## Graphical testing\n\n"
        "graphical details\n\n"
        "```sh\n"
        "stop\n"
        "start\n"
        "```\n\n"
        "tail\n"
    )
    final_section = (
        "## Product scenarios\n\n"
        "The product work is selectable:\n\n"
        "```sh\n"
        "SCENARIO=capture run-test\n"
        "```\n\n"
        "The available scenarios are `configfs` and `capture`.\n\n"
        "**configfs** checks topology lifetime.\n\n"
        "**capture** checks frame delivery.\n\n"
    )
    predecessor_section = (
        "## Product scenarios\n\n"
        "The product work is selectable:\n\n"
        "```sh\n"
        "SCENARIO=configfs run-test\n"
        "```\n\n"
        "The available scenario is `configfs`.\n\n"
        "**configfs** checks topology lifetime.\n\n"
    )
    final = baseline.replace(
        "## Graphical testing\n",
        final_section + "## Graphical testing\n",
    )
    predecessor = baseline.replace(
        "## Graphical testing\n",
        predecessor_section + "## Graphical testing\n",
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = int(_display_id_for_text(view, "## Product scenarios"))
    last = int(_display_id_for_text(view, "**capture** checks frame delivery."))
    result = git_stage_batch(
        "discard",
        "--to",
        "capture-provider",
        "--line",
        f"{first}-{last + 1}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=final_section + predecessor_section,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/capture-provider",
        )
        == final
    )


def test_transformed_guest_manifest_region_preserves_both_versions(
    functional_repo,
):
    """A guest manifest refactor must peel to its complete predecessor."""
    baseline_region = (
        "readonly TOOLCHAIN_STAMP_VERSION=v1\n"
        "\n"
        "vm_write_ready_marker\n"
        "\n"
        "if (( INSTALL_PACKAGES )); then\n"
        "\tvm_install_packages \\\n"
        "\t\tbase-a \\\n"
        "\t\tbase-b\n"
        "fi\n"
        "\n"
        "for dependency in base-dependency; do\n"
        '\tvm_require_command "$dependency"\n'
        "done\n"
    )
    final_region = (
        "readonly TOOLCHAIN_STAMP_VERSION=v7\n"
        "readonly GUEST_PACKAGE_MANIFEST=guest-packages.txt\n"
        "\n"
        "vm_write_ready_marker\n"
        "\n"
        "if (( INSTALL_PACKAGES )); then\n"
        "\tpackages=(\n"
        "\t\textra\n"
        "\t\tbase-a\n"
        "\t\tbase-b\n"
        "\t)\n"
        '\tvm_install_packages "${packages[@]}"\n'
        "fi\n"
        "\n"
        'vm_write_package_manifest "$GUEST_PACKAGE_MANIFEST"\n'
        "\n"
        "for dependency in extended-dependency; do\n"
        '\tvm_require_command "$dependency"\n'
        "done\n"
    )
    predecessor_region = (
        "readonly TOOLCHAIN_STAMP_VERSION=v6\n"
        "\n"
        "vm_write_ready_marker\n"
        "\n"
        "if (( INSTALL_PACKAGES )); then\n"
        "\tvm_install_packages \\\n"
        "\t\textra \\\n"
        "\t\tbase-a \\\n"
        "\t\tbase-b\n"
        "fi\n"
        "\n"
        "for dependency in extended-dependency; do\n"
        '\tvm_require_command "$dependency"\n'
        "done\n"
    )
    prefix = "#!/bin/bash\nset -eu\n\n"
    suffix = "\nvm_install_kernel\n"
    baseline = prefix + baseline_region + suffix
    final = prefix + final_region + suffix
    predecessor = prefix + predecessor_region + suffix
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    selected = _display_id_range(
        view,
        "readonly TOOLCHAIN_STAMP_VERSION=v1",
        "for dependency in extended-dependency; do",
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "guest-toolchain-manifest",
        "--line",
        selected,
        "--as-stdin",
        "--no-auto-advance",
        input_text=final_region + predecessor_region,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    actual = (
        path.read_text(),
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/guest-toolchain-manifest",
        ),
    )
    assert actual == (predecessor, final)


def _peel_transformed_guest_manifest_core(
    functional_repo,
):
    """The exact guest-bootstrap core must peel to toolchain v6."""
    prefix = """#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only

set -euo pipefail

target_release=${1:?missing target kernel release}
rpm_base_url=${2:?missing kernel RPM base URL}
rpm_dir=/var/tmp/castkms-kernel-$target_release
"""
    baseline_region = """toolchain_stamp=/var/lib/castkms-vm/toolchain-v1
kernel_ready=1

if test ! -e "$toolchain_stamp"; then
	sudo dnf -y -q --setopt=install_weak_deps=False install \
		bc \
		bison \
		curl \
		drm_info \
		drm-utils \
		elfutils-libelf-devel \
		flex \
		gcc \
		kmod \
		make \
		openssl-devel \
		perl-interpreter \
		rsync
	sudo mkdir -p "$(dirname -- "$toolchain_stamp")"
	sudo touch "$toolchain_stamp"
fi

for package in kernel kernel-core kernel-modules-core kernel-modules kernel-devel; do
	if ! rpm -q "$package-$target_release" >/dev/null 2>&1; then
		kernel_ready=0
	fi
done

if test "$kernel_ready" -eq 0; then
	mkdir -p "$rpm_dir"
	for package in kernel kernel-core kernel-modules-core kernel-modules kernel-devel; do
		rpm_name=$package-$target_release.rpm
		if test ! -f "$rpm_dir/$rpm_name"; then
			curl --fail --location --show-error --continue-at - \
				--output "$rpm_dir/$rpm_name" "$rpm_base_url/$rpm_name"
		fi
	done

	rpmkeys --checksig "$rpm_dir"/*.rpm
	sudo dnf -y -q install "$rpm_dir"/*.rpm
fi
"""
    final_region = """toolchain_stamp=/var/lib/castkms-vm/toolchain-v7
toolchain_manifest=/var/lib/castkms-vm/toolchain-packages.txt
kernel_ready=1
toolchain_packages=(
	alsa-lib-devel
	alsa-utils
	bc
	bison
	curl
	drm_info
	drm-utils
	elfutils-libelf-devel
	flex
	gcc
	kmod
	libdrm-devel
	make
	openssl-devel
	perl-interpreter
	pipewire
	pipewire-devel
	pipewire-utils
	pkgconf
	rsync
)

if test ! -e "$toolchain_stamp"; then
	sudo dnf -y -q --setopt=install_weak_deps=False install \
		"${toolchain_packages[@]}"
	sudo mkdir -p "$(dirname -- "$toolchain_stamp")"
	sudo touch "$toolchain_stamp"
fi

rpm -q --whatprovides \
	--qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' \
	"${toolchain_packages[@]}" | LC_ALL=C sort -u | \
	sudo tee "$toolchain_manifest" >/dev/null

for package in kernel kernel-core kernel-modules-core kernel-modules kernel-modules-internal kernel-devel; do
	if ! rpm -q "$package-$target_release" >/dev/null 2>&1; then
		kernel_ready=0
	fi
done

if test "$kernel_ready" -eq 0; then
	mkdir -p "$rpm_dir"
	for package in kernel kernel-core kernel-modules-core kernel-modules kernel-modules-internal kernel-devel; do
		rpm_name=$package-$target_release.rpm
		if test ! -f "$rpm_dir/$rpm_name"; then
			curl --fail --location --show-error --continue-at - \
				--output "$rpm_dir/$rpm_name" "$rpm_base_url/$rpm_name"
		fi
	done

	rpmkeys --checksig "$rpm_dir"/*.rpm
	sudo dnf -y -q install "$rpm_dir"/*.rpm
fi
"""
    predecessor_region = """toolchain_stamp=/var/lib/castkms-vm/toolchain-v6
kernel_ready=1

if test ! -e "$toolchain_stamp"; then
	sudo dnf -y -q --setopt=install_weak_deps=False install \
		alsa-lib-devel \
		alsa-utils \
		bc \
		bison \
		curl \
		drm_info \
		drm-utils \
		elfutils-libelf-devel \
		flex \
		gcc \
		kmod \
		libdrm-devel \
		make \
		openssl-devel \
		perl-interpreter \
		pipewire \
		pipewire-devel \
		pipewire-utils \
		pkgconf \
		rsync
	sudo mkdir -p "$(dirname -- "$toolchain_stamp")"
	sudo touch "$toolchain_stamp"
fi

for package in kernel kernel-core kernel-modules-core kernel-modules kernel-modules-internal kernel-devel; do
	if ! rpm -q "$package-$target_release" >/dev/null 2>&1; then
		kernel_ready=0
	fi
done

if test "$kernel_ready" -eq 0; then
	mkdir -p "$rpm_dir"
	for package in kernel kernel-core kernel-modules-core kernel-modules kernel-modules-internal kernel-devel; do
		rpm_name=$package-$target_release.rpm
		if test ! -f "$rpm_dir/$rpm_name"; then
			curl --fail --location --show-error --continue-at - \
				--output "$rpm_dir/$rpm_name" "$rpm_base_url/$rpm_name"
		fi
	done

	rpmkeys --checksig "$rpm_dir"/*.rpm
	sudo dnf -y -q install "$rpm_dir"/*.rpm
fi
"""
    suffix = """
test -f "/boot/vmlinuz-$target_release"
sudo grubby --set-default "/boot/vmlinuz-$target_release"

printf 'installed_kernel=%s\n' "$target_release"
printf 'running_kernel=%s\n' "$(uname -r)"
printf 'default_kernel=%s\n' "$(sudo grubby --default-kernel)"
"""
    manifest_output = "printf 'toolchain_manifest=%s\\n' \"$toolchain_manifest\"\n"
    baseline = prefix + baseline_region + suffix
    final = prefix + final_region + suffix + manifest_output
    predecessor = prefix + predecessor_region + suffix
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = int(_display_id_for_text(view, "toolchain-v1"))
    repeated = [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "kernel-modules-internal kernel-devel" in line and "[#" in line
    ]
    assert len(repeated) == 2
    selected = f"{first}-{max(repeated)}"
    result = git_stage_batch(
        "discard",
        "--to",
        "guest-toolchain-manifest",
        "--line",
        selected,
        "--as-stdin",
        "--no-auto-advance",
        input_text=final_region + predecessor_region,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    intermediate_live = predecessor + manifest_output
    intermediate_batch = prefix + final_region + suffix
    actual = (
        path.read_text(),
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/guest-toolchain-manifest",
        ),
    )
    assert actual == (intermediate_live, intermediate_batch)

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    output_id = _display_id_for_text(view, "toolchain_manifest=%s")
    output_result = git_stage_batch(
        "discard",
        "--to",
        "guest-toolchain-manifest",
        "--line",
        output_id,
        "--no-auto-advance",
        check=False,
    )
    assert output_result.returncode == 0, output_result.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/guest-toolchain-manifest",
        )
        == final
    )
    return path, predecessor, final


def test_transformed_guest_manifest_core_preserves_real_predecessor(
    functional_repo,
):
    """The exact guest-bootstrap core must peel to toolchain v6."""
    _peel_transformed_guest_manifest_core(functional_repo)


def test_transformed_guest_manifest_batch_replays_over_predecessor(
    functional_repo,
):
    """The saved toolchain-v7 batch must replay over committed toolchain v6."""
    path, predecessor, final = _peel_transformed_guest_manifest_core(functional_repo)
    assert path.read_text() == predecessor

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add toolchain v6 predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "guest-toolchain-manifest",
        "--file",
        "file.txt",
        check=False,
    )

    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_repeeled_provider_neutral_loop_stays_before_checker_tail(
    functional_repo,
):
    """A predecessor re-peeled into its scaffold batch must replay in place."""
    prefix = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "smoke_validate_registry\n"
        "smoke_validate_scenario all\n\n"
    )
    final_region = (
        "declare -A registered_modules=()\n"
        'for scenario in "${smoke_scenario_names[@]}"; do\n'
        '\tsmoke_validate_scenario "$scenario"\n'
        '\tif ! test -r "$module_dir/$scenario.sh"; then\n'
        "\t\tprintf 'missing smoke scenario module: %s.sh\\n' \"$scenario\" >&2\n"
        "\t\texit 1\n"
        "\tfi\n"
        "\tregistered_modules[$scenario]=1\n"
        "done\n\n"
        'for module in "$module_dir"/*.sh; do\n'
        '\tmodule_name=$(basename -- "$module" .sh)\n'
        '\tcase "$module_name" in\n'
        "\tcommon|modules) continue ;;\n"
        "\tesac\n"
        '\tif test -z "${registered_modules[$module_name]+present}"; then\n'
        "\t\tprintf 'unregistered smoke scenario module: %s\\n' \"$module\" >&2\n"
        "\t\texit 1\n"
        "\tfi\n"
        "done\n\n"
    )
    predecessor_region = (
        'for scenario in "${smoke_scenario_names[@]}"; do\n'
        '\tsmoke_validate_scenario "$scenario"\n'
        '\tif ! test -r "$module_dir/$scenario.sh"; then\n'
        "\t\tprintf 'missing smoke scenario module: %s.sh\\n' \"$scenario\" >&2\n"
        "\t\texit 1\n"
        "\tfi\n"
        "done\n\n"
    )
    suffix = (
        "if smoke_validate_scenario invalid-scenario >/dev/null 2>&1; then\n"
        "\texit 1\n"
        "fi\n\n"
        "printf 'smoke-modules=pass (%s scenarios)\\n' \\\n"
        '\t"${#smoke_scenario_names[@]}"\n'
    )
    final = prefix + final_region + suffix
    predecessor = prefix + predecessor_region + suffix
    path = functional_repo / "checker.sh"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    save = git_stage_batch(
        "discard",
        "--to",
        "smoke-module-scaffold",
        "--file",
        "checker.sh",
        "--no-auto-advance",
        check=False,
    )
    assert save.returncode == 0, save.stderr
    assert not path.exists()

    git_stage_batch(
        "apply",
        "--from",
        "smoke-module-scaffold",
        "--file",
        "checker.sh",
    )
    batch_view = git_stage_batch(
        "show",
        "--from",
        "smoke-module-scaffold",
        "--file",
        "checker.sh",
        "--page",
        "all",
    ).stdout
    first = int(_display_id_for_text(batch_view, "declare -A registered_modules"))
    done_ids = [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in batch_view.splitlines()
        if "[#" in line and "+ done" in line
    ]
    assert len(done_ids) == 2
    reset = git_stage_batch(
        "reset",
        "--from",
        "smoke-module-scaffold",
        "--line",
        f"{first}-{max(done_ids) + 1}",
        check=False,
    )
    assert reset.returncode == 0, reset.stderr

    git_stage_batch(
        "show",
        "--from",
        "smoke-module-scaffold",
        "--file",
        "checker.sh",
        "--page",
        "all",
    )
    reverse = git_stage_batch(
        "discard",
        "--from",
        "smoke-module-scaffold",
        "--file",
        "checker.sh",
        check=False,
    )
    assert reverse.returncode == 0, reverse.stderr
    assert path.read_text() == final_region

    live_view = git_stage_batch("show", "--file", "checker.sh", "--page", "all").stdout
    first = int(_display_id_for_text(live_view, "declare -A registered_modules"))
    done_ids = [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in live_view.splitlines()
        if "[#" in line and "+ done" in line
    ]
    transform = git_stage_batch(
        "discard",
        "--to",
        "smoke-pipewire-provider",
        "--line",
        f"{first}-{max(done_ids) + 1}",
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
            "refs/git-stage-batch/batches/smoke-pipewire-provider",
            "checker.sh",
        )
        == final_region
    )

    live_view = git_stage_batch("show", "--file", "checker.sh", "--page", "all").stdout
    first = int(_display_id_for_text(live_view, "for scenario in"))
    done_id = int(_display_id_for_text(live_view, "done"))
    repeel = git_stage_batch(
        "discard",
        "--to",
        "smoke-module-scaffold",
        "--line",
        f"{first}-{done_id + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert repeel.returncode == 0, repeel.stderr
    if path.exists():
        cleanup = git_stage_batch(
            "discard",
            "--file",
            "checker.sh",
            "--no-auto-advance",
            check=False,
        )
        assert cleanup.returncode == 0, cleanup.stderr
    assert not path.exists()
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/smoke-module-scaffold",
            "checker.sh",
        )
        == predecessor
    )

    replay = git_stage_batch(
        "apply",
        "--from",
        "smoke-module-scaffold",
        "--file",
        "checker.sh",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == predecessor
    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "checker.sh"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add provider-neutral checker"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    provider_replay = git_stage_batch(
        "apply",
        "--from",
        "smoke-pipewire-provider",
        "--file",
        "checker.sh",
        check=False,
    )
    assert provider_replay.returncode == 0, provider_replay.stderr
    assert path.read_text() == final


def test_discard_from_restores_adjacent_replacement_predecessor(
    functional_repo,
):
    """Reversing an applied batch must restore its replaced argument line."""
    baseline = (
        "#!/usr/bin/env bash\n"
        "repo_dir=${1:-$HOME/project}\n"
        "expected_release=${2:?missing release}\n"
        "fast_gate=${3:-run}\n"
        "result_dir=$repo_dir/results\n"
    )
    final = baseline.replace(
        "fast_gate=${3:-run}\n",
        "scenario=${3:-all}\nfast_gate=${4:-run}\n",
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    scenario_id = _display_id_for_text(view, "scenario=${3:-all}")
    scenario_peel = git_stage_batch(
        "discard",
        "--to",
        "smoke-module-scaffold",
        "--line",
        scenario_id,
        "--no-auto-advance",
        check=False,
    )
    assert scenario_peel.returncode == 0, scenario_peel.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    old_id = int(_display_id_for_text(view, "fast_gate=${3:-run}"))
    new_id = int(_display_id_for_text(view, "fast_gate=${4:-run}"))
    replacement_peel = git_stage_batch(
        "discard",
        "--to",
        "smoke-module-scaffold",
        "--line",
        f"{min(old_id, new_id)}-{max(old_id, new_id)}",
        "--as-stdin",
        "--no-auto-advance",
        input_text="fast_gate=${4:-run}\nfast_gate=${3:-run}\n",
        check=False,
    )
    assert replacement_peel.returncode == 0, replacement_peel.stderr
    assert path.read_text() == baseline

    replay = git_stage_batch(
        "apply",
        "--from",
        "smoke-module-scaffold",
        "--file",
        "file.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final

    reverse = git_stage_batch(
        "discard",
        "--from",
        "smoke-module-scaffold",
        "--file",
        "file.txt",
        check=False,
    )
    assert reverse.returncode == 0, reverse.stderr
    assert path.read_text() == baseline


def test_desktop_attach_doc_regions_replay_over_exact_predecessor(
    functional_repo,
):
    """Two peeled regions in one later section must replay together."""
    old_quick_start = (
        "Provision the guest and run the old smoke test:\n\n"
        "```old-sh\n"
        "./scripts/vm/castkms-vm provision\n"
        "./scripts/vm/castkms-vm test\n"
        "```\n\n"
        "The old smoke command builds and loads the module.\n\n"
        "Results are copied to:\n\n"
        "```text\n"
        "old/results/default/\n"
        "```\n\n"
    )
    new_quick_start = (
        "```new-sh\n"
        "./scripts/vm/castkms-vm provision\n"
        "./scripts/vm/castkms-vm kunit-test\n"
        "```\n\n"
        "The KUnit fast path runs the focused live gate.\n\n"
        "The broader test reuses those artifacts, then runs the product gate.\n"
    )
    baseline = (
        "# VM testing\n\n" + old_quick_start + "## Graphical testing\n\n"
        "Graphical commands remain stable.\n\n"
        "The base image is intentionally minimal. GNOME installation and\n"
        "interactive testing are separate from the kernel smoke test.\n"
    )
    attach_paragraph = (
        "`desktop-start` and `desktop-attach` perform the same sync, build, "
        "and attach\n"
        "step on an already-provisioned desktop guest.\n\n"
    )
    prior_rows = (
        "./scripts/vm/castkms-vm desktop-status\n"
        "./scripts/vm/castkms-vm desktop-start\n"
    )
    attach_row = "./scripts/vm/castkms-vm desktop-attach\n"
    later_rows = (
        "./scripts/vm/castkms-vm desktop-shell\n./scripts/vm/castkms-vm desktop-stop\n"
    )
    final = (
        "# VM testing\n\n"
        + new_quick_start
        + old_quick_start
        + "## Graphical testing\n\n"
        "Graphical commands remain stable.\n\n"
        "## Mutter visibility\n\n"
        "The desktop instance uses the shared harness.\n\n"
        + attach_paragraph
        + "`desktop-test` checks discovery before attachment.\n\n"
        "Results are copied to the desktop result directory.\n\n"
        "```sh\n" + prior_rows + attach_row + later_rows + "```\n"
    )
    after_prior_replacement = final.replace(
        new_quick_start + old_quick_start,
        old_quick_start,
    )
    target_final = after_prior_replacement.replace(prior_rows, "").replace(
        later_rows, ""
    )
    predecessor = target_final.replace(attach_paragraph, "").replace(attach_row, "")
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    prior_replacement = git_stage_batch(
        "discard",
        "--file",
        "file.txt",
        "--as-stdin",
        "--no-auto-advance",
        input_text=after_prior_replacement,
        check=False,
    )
    assert prior_replacement.returncode == 0, prior_replacement.stderr
    assert path.read_text() == after_prior_replacement

    for batch, row in (
        ("desktop-stop-route", "./scripts/vm/castkms-vm desktop-stop"),
        ("desktop-shell-route", "./scripts/vm/castkms-vm desktop-shell"),
        ("desktop-status-route", "./scripts/vm/castkms-vm desktop-status"),
        ("desktop-start-route", "./scripts/vm/castkms-vm desktop-start"),
    ):
        view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
        prior_id = _display_id_for_text(view, row)
        prior_peel = git_stage_batch(
            "discard",
            "--to",
            batch,
            "--line",
            prior_id,
            "--no-auto-advance",
            check=False,
        )
        assert prior_peel.returncode == 0, prior_peel.stderr
    assert path.read_text() == target_final

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    row_id = _display_id_for_text(view, attach_row.rstrip("\n"))
    row_peel = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        row_id,
        "--no-auto-advance",
        check=False,
    )
    assert row_peel.returncode == 0, row_peel.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = int(_display_id_for_text(view, "`desktop-start` and `desktop-attach`"))
    last = int(_display_id_for_text(view, "step on an already-provisioned"))
    paragraph_peel = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        f"{first}-{last + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert paragraph_peel.returncode == 0, paragraph_peel.stderr
    assert path.read_text() == predecessor

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add desktop attach predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target_final


def test_earlier_adjacent_added_source_block_replays_before_retained_sibling(
    functional_repo,
):
    """Replay must preserve the order of adjacent blocks in one baseline gap."""
    baseline = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "repo_dir=${1:-$HOME/project}\n"
        "printf '%s\\n' \"$repo_dir\"\n"
    )
    final = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        'script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)\n'
        "# shellcheck source=kernel-diagnostics.sh\n"
        '. "$script_dir/kernel-diagnostics.sh"\n'
        "# shellcheck source=module-dependencies.sh\n"
        '. "$script_dir/module-dependencies.sh"\n'
        "\n"
        "repo_dir=${1:-$HOME/project}\n"
        "printf '%s\\n' \"$repo_dir\"\n"
    )
    predecessor = final.replace(
        "# shellcheck source=kernel-diagnostics.sh\n"
        '. "$script_dir/kernel-diagnostics.sh"\n',
        "",
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    selected = _display_id_range(
        view,
        "# shellcheck source=kernel-diagnostics.sh",
        '. "$script_dir/kernel-diagnostics.sh"',
    )
    peel = git_stage_batch(
        "discard",
        "--to",
        "kernel-diagnostics",
        "--line",
        selected,
        "--no-auto-advance",
        check=False,
    )
    assert peel.returncode == 0, peel.stderr
    assert path.read_text() == predecessor

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add dependency-source predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "kernel-diagnostics",
        "--file",
        "file.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_existing_batch_keeps_later_function_adoption_inside_function(
    functional_repo,
):
    """A later adopter must remain inside its separately owned function."""
    baseline = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "provision()\n"
        "{\n"
        "\tprintf 'provision\\n'\n"
        "}\n\n"
        "sync_source()\n"
        "{\n"
        "\tprintf 'sync\\n'\n"
        "}\n"
    )
    provision_desktop = (
        "provision_desktop()\n"
        "{\n"
        "\tprovision\n"
        "\twait_for_ssh\n"
        "\tensure_desktop_attach\n"
        "\tprintf 'ready\\n'\n"
        "}\n\n"
    )
    attach_helper = "ensure_desktop_attach()\n{\n\tprintf 'attach\\n'\n}\n\n"
    final = baseline.replace(
        "sync_source()\n", provision_desktop + attach_helper + "sync_source()\n"
    )
    predecessor = final.replace(attach_helper, "").replace(
        "\tensure_desktop_attach\n", ""
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    helper_first = int(_display_id_for_text(view, "ensure_desktop_attach()"))
    helper_body = int(_display_id_for_text(view, "\tprintf 'attach\\n'"))
    helper_last = helper_body + 2
    helper_peel = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        f"{helper_first}-{helper_last}",
        "--no-auto-advance",
        check=False,
    )
    assert helper_peel.returncode == 0, helper_peel.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    function_first = int(_display_id_for_text(view, "provision_desktop()"))
    adoption_id = int(_display_id_for_text(view, "\tensure_desktop_attach"))
    ready_id = int(_display_id_for_text(view, "\tprintf 'ready\\n'"))
    function_last = ready_id + 2
    context_ids = (
        f"{function_first}-{adoption_id - 1},{adoption_id + 1}-{function_last}"
    )
    context = git_stage_batch(
        "include",
        "--to",
        "desktop-attach-route",
        "--line",
        context_ids,
        "--no-auto-advance",
        check=False,
    )
    assert context.returncode == 0, context.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    adoption_id = _display_id_for_text(view, "\tensure_desktop_attach")
    adoption = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        adoption_id,
        "--no-auto-advance",
        check=False,
    )
    assert adoption.returncode == 0, adoption.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/desktop-attach-route",
        )
        == final
    )

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add desktop provision predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


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


def test_rebuilt_batch_forgets_removed_adjacent_function_tail(functional_repo):
    """Resetting and rebuilding a batch must forget a later peeled sibling."""
    baseline = (
        "#!/usr/bin/env bash\n\n"
        "provision()\n"
        "{\n"
        "\t: provision\n"
        "}\n\n"
        "sync_source()\n"
        "{\n"
        "\t: sync\n"
        "}\n"
    )
    provision_desktop = (
        "provision_desktop()\n"
        "{\n"
        "\tprovision\n"
        "\tensure_desktop_attach\n"
        "\tprintf 'ready %s\\n' \\\n"
        '\t\t"$display"\n'
        "}\n\n"
    )
    attach_helper = "ensure_desktop_attach()\n{\n\t: attach\n}\n\n"
    start_desktop = (
        "start_desktop()\n"
        "{\n"
        "\tstart\n"
        "\tensure_desktop_attach\n"
        "\tprintf 'started %s\\n' \\\n"
        '\t\t"$start_display"\n'
        "}\n\n"
    )
    source = baseline.replace(
        "sync_source()\n",
        provision_desktop + attach_helper + start_desktop + "sync_source()\n",
    )
    final = source.replace(start_desktop, "")
    predecessor = final.replace(attach_helper, "").replace(
        "\tensure_desktop_attach\n", ""
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(source)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    helper_first = int(_display_id_for_text(view, "ensure_desktop_attach()"))
    helper_peel = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        f"{helper_first}-{helper_first + 4}",
        "--no-auto-advance",
        check=False,
    )
    assert helper_peel.returncode == 0, helper_peel.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    start_first = int(_display_id_for_text(view, "start_desktop()"))
    start_peel = git_stage_batch(
        "discard",
        "--to",
        "desktop-start-route",
        "--line",
        f"{start_first}-{start_first + 7}",
        "--no-auto-advance",
        check=False,
    )
    assert start_peel.returncode == 0, start_peel.stderr
    assert path.read_text() == final.replace(attach_helper, "")

    restore = git_stage_batch(
        "apply",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
        check=False,
    )
    assert restore.returncode == 0, restore.stderr
    assert path.read_text() == final
    git_stage_batch(
        "reset",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
    )
    assert path.read_text() == final

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    helper_first = int(_display_id_for_text(view, "ensure_desktop_attach()"))
    helper_peel = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        f"{helper_first}-{helper_first + 4}",
        "--no-auto-advance",
        check=False,
    )
    assert helper_peel.returncode == 0, helper_peel.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    function_first = int(_display_id_for_text(view, "provision_desktop()"))
    adoption_id = int(_display_id_for_text(view, "\tensure_desktop_attach"))
    display_id = int(_display_id_for_text(view, '\t\t"$display"'))
    function_last = display_id + 2
    context_ids = (
        f"{function_first}-{adoption_id - 1},{adoption_id + 1}-{function_last}"
    )
    context = git_stage_batch(
        "include",
        "--to",
        "desktop-attach-route",
        "--line",
        context_ids,
        "--no-auto-advance",
        check=False,
    )
    assert context.returncode == 0, context.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    adoption_id = _display_id_for_text(view, "\tensure_desktop_attach")
    adoption = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        adoption_id,
        "--no-auto-advance",
        check=False,
    )
    assert adoption.returncode == 0, adoption.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/desktop-attach-route",
        )
        == final
    )

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add desktop provision predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_rebuilt_disjoint_batch_forgets_removed_adjacent_function_tail(
    functional_repo,
):
    """A rebuilt disjoint batch must forget a later peeled sibling."""
    baseline = (
        "#!/usr/bin/env bash\n\n"
        "provision()\n"
        "{\n"
        "\t: provision\n"
        "}\n\n"
        "sync_source()\n"
        "{\n"
        "\t: sync\n"
        "}\n\n"
        "command=${1:-}\n"
        'case "$command" in\n'
        "\tsync) sync_source ;;\n"
        "esac\n"
    )
    provision_desktop = (
        "provision_desktop()\n"
        "{\n"
        "\tprovision\n"
        "\tensure_desktop_attach\n"
        "\tprintf 'ready %s\\n' \\\n"
        '\t\t"$display"\n'
        "}\n\n"
    )
    attach_helper = "ensure_desktop_attach()\n{\n\t: attach\n}\n\n"
    start_desktop = (
        "start_desktop()\n"
        "{\n"
        "\tstart\n"
        "\tensure_desktop_attach\n"
        "\tprintf 'started %s\\n' \\\n"
        '\t\t"$start_display"\n'
        "}\n\n"
    )
    attach_dispatch = "\tdesktop-attach)\n\t\tensure_desktop_attach\n\t\t;;\n"
    source = baseline.replace(
        "sync_source()\n",
        provision_desktop + attach_helper + start_desktop + "sync_source()\n",
    ).replace(
        "\tsync) sync_source ;;\n",
        attach_dispatch + "\tsync) sync_source ;;\n",
    )
    final = source.replace(start_desktop, "")
    predecessor = (
        final.replace(attach_helper, "")
        .replace(attach_dispatch, "")
        .replace("\tensure_desktop_attach\n", "")
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(source)

    def peel_attach_route():
        view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
        helper_first = int(_display_id_for_text(view, "ensure_desktop_attach()"))
        dispatch_first = int(_display_id_for_text(view, "\tdesktop-attach)"))
        dispatch_call = int(_display_id_for_text(view, "\t\tensure_desktop_attach"))
        peel = git_stage_batch(
            "discard",
            "--to",
            "desktop-attach-route",
            "--line",
            (f"{helper_first}-{helper_first + 4},{dispatch_first}-{dispatch_call + 1}"),
            "--no-auto-advance",
            check=False,
        )
        assert peel.returncode == 0, peel.stderr

    git_stage_batch("start", "--no-auto-advance")
    peel_attach_route()

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    start_first = int(_display_id_for_text(view, "start_desktop()"))
    start_peel = git_stage_batch(
        "discard",
        "--to",
        "desktop-start-route",
        "--line",
        f"{start_first}-{start_first + 7}",
        "--no-auto-advance",
        check=False,
    )
    assert start_peel.returncode == 0, start_peel.stderr
    assert path.read_text() == final.replace(attach_helper, "").replace(
        attach_dispatch, ""
    )

    restore = git_stage_batch(
        "apply",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
        check=False,
    )
    assert restore.returncode == 0, restore.stderr
    assert path.read_text() == final
    git_stage_batch(
        "reset",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
    )
    assert path.read_text() == final

    peel_attach_route()
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    function_first = int(_display_id_for_text(view, "provision_desktop()"))
    adoption_id = int(_display_id_for_text(view, "\tensure_desktop_attach"))
    display_id = int(_display_id_for_text(view, '\t\t"$display"'))
    function_last = display_id + 2
    context_ids = (
        f"{function_first}-{adoption_id - 1},{adoption_id + 1}-{function_last}"
    )
    context = git_stage_batch(
        "include",
        "--to",
        "desktop-attach-route",
        "--line",
        context_ids,
        "--no-auto-advance",
        check=False,
    )
    assert context.returncode == 0, context.stderr

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    adoption_id = _display_id_for_text(view, "\tensure_desktop_attach")
    adoption = git_stage_batch(
        "discard",
        "--to",
        "desktop-attach-route",
        "--line",
        adoption_id,
        "--no-auto-advance",
        check=False,
    )
    assert adoption.returncode == 0, adoption.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/desktop-attach-route",
        )
        == final
    )

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add desktop provision predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "desktop-attach-route",
        "--file",
        "file.txt",
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


def test_options_transform_after_adjacent_variable_delete(functional_repo):
    """Deleting the CEC variable must not invalidate the following F2-to-P1 edit."""
    baseline = (
        "# SPDX-License-Identifier: GPL-2.0-only\n\n"
        "KDIR ?= /lib/modules/$(shell uname -r)/build\n\n"
        ".PHONY: all clean install kunit\n\n"
        "all:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) modules\n\n"
        "clean:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\tCONFIG_DRM_CASTKMS_KUNIT_TEST=m clean\n\n"
        "install:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) modules_install\n\n"
        "kunit:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\tCONFIG_DRM_CASTKMS_KUNIT_TEST=m modules\n"
    )
    matrix = (
        "build-matrix:\n"
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CONFIG_SND=n\n"
        "\ttest ! -e src/castkms_audio.o\n"
        '\tcase "$$(modinfo -F depends ./castkms.ko)" in *snd*) false;; esac\n'
        '\tcase "$$(modinfo -F softdep ./castkms.ko)" in *snd*) false;; esac\n'
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=n CASTKMS_BUILD_CEC=y\n"
        "\ttest ! -e src/castkms_audio.o\n"
        "\ttest -e src/castkms_cec_core.o\n"
        "\ttest -e src/castkms_cec_uapi.o\n"
        '\tcase "$$(modinfo -F depends ./castkms.ko)" in *snd*) false;; esac\n'
        '\tcase "$$(modinfo -F softdep ./castkms.ko)" in *snd*) false;; esac\n'
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=y CASTKMS_BUILD_CEC=y\n"
        "\ttest -e src/castkms_audio.o\n"
        "\ttest -e src/castkms_cec_core.o\n"
        "\ttest -e src/castkms_cec_uapi.o\n"
        "\tcheck undefined symbols\n"
    )
    suffix = (
        "\ncheck: check-shell\n"
        "\t$(MAKE) -C tools check\n\n"
        "check-shell:\n"
        "\tbash -n scripts/*.sh\n\n"
        "tools:\n"
        "\t$(MAKE) -C tools\n"
    )
    final = (
        "# SPDX-License-Identifier: GPL-2.0-only\n\n"
        "KDIR ?= /lib/modules/$(shell uname -r)/build\n"
        "CASTKMS_BUILD_AUDIO ?= y\n"
        "CASTKMS_BUILD_CEC ?= y\n\n"
        "CASTKMS_KBUILD_OPTIONS := \\\n"
        "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO) \\\n"
        "\tCASTKMS_BUILD_CEC=$(CASTKMS_BUILD_CEC)\n\n"
        ".PHONY: all build-matrix check check-shell clean install kunit "
        "module-clean tools\n\n"
        "all:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) $(CASTKMS_KBUILD_OPTIONS) modules\n\n"
        "module-clean:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\t$(CASTKMS_KBUILD_OPTIONS) CONFIG_DRM_CASTKMS_KUNIT_TEST=m clean\n\n"
        "clean: module-clean\n"
        "\t$(MAKE) -C tools clean\n\n"
        "install:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\t$(CASTKMS_KBUILD_OPTIONS) modules_install\n\n"
        "kunit:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\t$(CASTKMS_KBUILD_OPTIONS) \\\n"
        "\t\tCONFIG_DRM_CASTKMS_KUNIT_TEST=m modules\n\n" + matrix + suffix
    )
    after_variable = final.replace("CASTKMS_BUILD_CEC ?= y\n", "")
    saved_after_variable = baseline.replace(
        "KDIR ?= /lib/modules/$(shell uname -r)/build\n",
        "KDIR ?= /lib/modules/$(shell uname -r)/build\nCASTKMS_BUILD_CEC ?= y\n",
    )
    predecessor = after_variable.replace(
        "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO) \\\n"
        "\tCASTKMS_BUILD_CEC=$(CASTKMS_BUILD_CEC)\n",
        "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO)\n",
    )

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
    variable_id = _display_id_for_text(view, "CASTKMS_BUILD_CEC ?= y")
    delete = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        variable_id,
        "--no-auto-advance",
        check=False,
    )
    assert delete.returncode == 0, delete.stderr
    assert path.read_text() == after_variable
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/cec-build-selection",
            "Makefile",
        )
        == saved_after_variable
    )

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    audio_id = int(
        _display_id_for_text(
            view,
            "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO) \\",
        )
    )
    cec_id = int(_display_id_for_text(view, "\tCASTKMS_BUILD_CEC=$(CASTKMS_BUILD_CEC)"))
    assert cec_id == audio_id + 1
    transform = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        f"{audio_id}-{cec_id}",
        "--as-stdin",
        "--no-auto-advance",
        input_text="\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO)\n",
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "Makefile"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add audio-only build predecessor"],
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


def test_full_cec_makefile_transform_replays_after_adjacent_claims(
    functional_repo,
):
    """All four adjacent CEC peels must save and replay the exact final file."""
    baseline = (
        "# SPDX-License-Identifier: GPL-2.0-only\n\n"
        "KDIR ?= /lib/modules/$(shell uname -r)/build\n\n"
        ".PHONY: all clean install kunit\n\n"
        "all:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) modules\n\n"
        "clean:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\tCONFIG_DRM_CASTKMS_KUNIT_TEST=m clean\n\n"
        "install:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) modules_install\n\n"
        "kunit:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\tCONFIG_DRM_CASTKMS_KUNIT_TEST=m modules\n"
    )
    common_prefix = (
        "# SPDX-License-Identifier: GPL-2.0-only\n\n"
        "KDIR ?= /lib/modules/$(shell uname -r)/build\n"
        "CASTKMS_BUILD_AUDIO ?= y\n"
    )
    final_options = (
        "CASTKMS_BUILD_CEC ?= y\n\n"
        "CASTKMS_KBUILD_OPTIONS := \\\n"
        "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO) \\\n"
        "\tCASTKMS_BUILD_CEC=$(CASTKMS_BUILD_CEC)\n\n"
    )
    predecessor_options = (
        "\nCASTKMS_KBUILD_OPTIONS := \\\n"
        "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO)\n\n"
    )
    rules = (
        ".PHONY: all build-matrix check check-architecture check-ioctls \\\n"
        "\tcheck-shell clean install kunit module-clean tools\n\n"
        "all:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) $(CASTKMS_KBUILD_OPTIONS) modules\n\n"
        "module-clean:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\t$(CASTKMS_KBUILD_OPTIONS) CONFIG_DRM_CASTKMS_KUNIT_TEST=m clean\n\n"
        "clean: module-clean\n"
        "\t$(MAKE) -C tools clean\n\n"
        "install:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\t$(CASTKMS_KBUILD_OPTIONS) modules_install\n\n"
        "kunit:\n"
        "\t$(MAKE) -C $(KDIR) M=$(CURDIR) \\\n"
        "\t\t$(CASTKMS_KBUILD_OPTIONS) \\\n"
        "\t\tCONFIG_DRM_CASTKMS_KUNIT_TEST=m modules\n\n"
        "build-matrix:\n"
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CONFIG_SND=n\n"
        "\ttest ! -e src/castkms_audio.o\n"
        '\tcase "$$(modinfo -F depends ./castkms.ko)" in *snd*) false;; esac\n'
        '\tcase "$$(modinfo -F softdep ./castkms.ko)" in *snd*) false;; esac\n'
    )
    disabled_final = (
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=n CASTKMS_BUILD_CEC=y\n"
        "\ttest ! -e src/castkms_audio.o\n"
        "\ttest -e src/castkms_cec_core.o\n"
        "\ttest -e src/castkms_cec_uapi.o\n"
        '\tcase "$$(modinfo -F depends ./castkms.ko)" in *snd*) false;; esac\n'
        '\tcase "$$(modinfo -F softdep ./castkms.ko)" in *snd*) false;; esac\n'
    )
    disabled_predecessor = (
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=n\n"
        "\ttest ! -e src/castkms_audio.o\n"
        '\tcase "$$(modinfo -F depends ./castkms.ko)" in *snd*) false;; esac\n'
        '\tcase "$$(modinfo -F softdep ./castkms.ko)" in *snd*) false;; esac\n'
    )
    enabled_final = (
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=y CASTKMS_BUILD_CEC=y\n"
        "\ttest -e src/castkms_audio.o\n"
        "\ttest -e src/castkms_cec_core.o\n"
        "\ttest -e src/castkms_cec_uapi.o\n"
        "\t! grep -Eq 'castkms_grant|drm_file|drm_pending_event|drm_castkms|castkms_drm' \\\n"
        "\t\tsrc/castkms_cec_core.c src/castkms_cec_core.h\n"
        '\tcase "$$(nm -u src/castkms_cec_core.o)" in \\\n'
        "\t\t*castkms_grant*|*drm_event*|*drm_send_event*|*get_file_active*|*fput*) false;; \\\n"
        "\tesac\n"
        "\tnm -u src/castkms_cec_uapi.o | grep -q castkms_grant_begin\n"
        "\tnm -u src/castkms_cec_uapi.o | grep -q drm_event_reserve_init\n"
    )
    enabled_predecessor = (
        "\t$(MAKE) module-clean\n"
        "\t$(MAKE) all CASTKMS_BUILD_AUDIO=y\n"
        "\ttest -e src/castkms_audio.o\n"
    )
    suffix = (
        "\ncheck: check-architecture check-ioctls check-shell\n"
        "\t$(MAKE) -C tools check\n\n"
        "check-architecture:\n"
        "\t./scripts/check-architecture.sh\n\n"
        "check-ioctls:\n"
        "\t./scripts/check-private-ioctls.sh\n\n"
        "check-shell:\n"
        "\tbash -n scripts/*.sh scripts/vm/*.sh tools/pw-castkms/*.sh\n"
        "\t# Guest helpers use dynamic source paths, controlled sysfs names, and\n"
        "\t# caller-owned result redirections around sudo commands.\n"
        "\tshellcheck --external-sources --source-path=scripts \\\n"
        "\t\t--source-path=scripts/vm \\\n"
        "\t\t--exclude=SC1090,SC2012,SC2024 \\\n"
        "\t\tscripts/*.sh scripts/vm/*.sh tools/pw-castkms/*.sh\n\n"
        "tools:\n"
        "\t$(MAKE) -C tools\n"
    )
    final = (
        common_prefix + final_options + rules + disabled_final + enabled_final + suffix
    )
    after_variable = final.replace("CASTKMS_BUILD_CEC ?= y\n", "")
    after_options = after_variable.replace(
        "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO) \\\n"
        "\tCASTKMS_BUILD_CEC=$(CASTKMS_BUILD_CEC)\n",
        "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO)\n",
    )
    after_disabled = after_options.replace(disabled_final, disabled_predecessor)
    predecessor = (
        common_prefix
        + predecessor_options
        + rules
        + disabled_predecessor
        + enabled_predecessor
        + suffix
    )
    assert after_disabled.replace(enabled_final, enabled_predecessor) == predecessor

    def hash_text(content):
        return subprocess.run(
            ["git", "hash-object", "--stdin"],
            cwd=functional_repo,
            check=True,
            capture_output=True,
            input=content,
            text=True,
        ).stdout.strip()

    assert hash_text(baseline) == "f254477031026d95fba7df64657d8adfec772a32"
    assert hash_text(final) == "b8605d7417891ed3c92c1b33bbcc3532cecea36b"
    assert hash_text(predecessor) == "657ef7b94d906996e931b5c34ffe9107a3a4211a"

    path = functional_repo / "Makefile"
    path.write_text(baseline)
    subprocess.run(
        ["git", "add", "Makefile"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add original build rules"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    variable_id = _display_id_for_text(view, "CASTKMS_BUILD_CEC ?= y")
    delete = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        variable_id,
        "--no-auto-advance",
        check=False,
    )
    assert delete.returncode == 0, delete.stderr
    assert path.read_text() == after_variable

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    audio_id = int(
        _display_id_for_text(
            view,
            "\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO) \\",
        )
    )
    cec_id = int(_display_id_for_text(view, "\tCASTKMS_BUILD_CEC=$(CASTKMS_BUILD_CEC)"))
    assert cec_id == audio_id + 1
    options = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        f"{audio_id}-{cec_id}",
        "--as-stdin",
        "--no-auto-advance",
        input_text="\tCASTKMS_BUILD_AUDIO=$(CASTKMS_BUILD_AUDIO)\n",
        check=False,
    )
    assert options.returncode == 0, options.stderr
    assert path.read_text() == after_options

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    disabled_build_id = int(
        _display_id_for_text(
            view,
            "\t$(MAKE) all CASTKMS_BUILD_AUDIO=n CASTKMS_BUILD_CEC=y",
        )
    )
    disabled = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        f"{disabled_build_id - 1}-{disabled_build_id + 5}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=disabled_predecessor,
        check=False,
    )
    assert disabled.returncode == 0, disabled.stderr
    assert path.read_text() == after_disabled

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    enabled_build_id = int(
        _display_id_for_text(
            view,
            "\t$(MAKE) all CASTKMS_BUILD_AUDIO=y CASTKMS_BUILD_CEC=y",
        )
    )
    enabled_last_id = int(
        _display_id_for_text(
            view,
            "\tnm -u src/castkms_cec_uapi.o | grep -q drm_event_reserve_init",
        )
    )
    assert enabled_last_id == enabled_build_id + 10
    enabled = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        f"{enabled_build_id - 1}-{enabled_last_id}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=enabled_predecessor,
        check=False,
    )
    assert enabled.returncode == 0, enabled.stderr
    assert path.read_text() == predecessor

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "Makefile"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add audio-only build predecessor"],
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


def test_reset_multifile_batch_readds_transformed_file_as_replacement(
    functional_repo,
):
    """Resetting one file must not turn its re-added F-to-P edit into presence."""
    baseline = "# Build\n\nOld VM smoke-test details.\n"
    predecessor_block = (
        "HDMI audio compiles in when its dependencies are available.\n"
        "A package can omit it.\n\n"
        "make CASTKMS_BUILD_AUDIO=n\n\n"
        "The matrix covers audio configurations.\n\n"
    )
    final_block = (
        "HDMI audio and HDMI-CEC compile in when their dependencies are available.\n"
        "A package can omit both.\n\n"
        "make CASTKMS_BUILD_AUDIO=n CASTKMS_BUILD_CEC=n\n\n"
        "The matrix covers all audio and CEC combinations.\n"
        "The CEC helper must be present.\n\n"
    )
    predecessor = "# Build\n\n" + predecessor_block + "Userspace tools follow.\n"
    final = "# Build\n\n" + final_block + "Userspace tools follow.\n"
    path = functional_repo / "README.md"
    other = functional_repo / "other.txt"
    path.write_text(baseline)
    other.write_text("old\n")
    subprocess.run(
        ["git", "add", "README.md", "other.txt"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add baseline files"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(predecessor)
    other.write_text("new\n")

    git_stage_batch("start", "--no-auto-advance")
    other_view = git_stage_batch("show", "--file", "other.txt", "--page", "all").stdout
    other_id = _display_id_for_text(other_view, "new")
    git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        other_id,
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "README.md", "--page", "all").stdout
    first = int(_display_id_for_text(view, "HDMI audio compiles in when"))
    last = int(_display_id_for_text(view, "The matrix covers audio configurations."))
    git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        f"{first}-{last + 1}",
        "--no-auto-advance",
    )
    assert path.read_text() == "# Build\n\nUserspace tools follow.\n"
    git_stage_batch(
        "apply",
        "--from",
        "cec-build-selection",
        "--file",
        "README.md",
    )
    assert path.read_text() == predecessor
    git_stage_batch(
        "reset",
        "--from",
        "cec-build-selection",
        "--file",
        "README.md",
    )
    assert path.read_text() == predecessor

    view = git_stage_batch("show", "--file", "README.md", "--page", "all").stdout
    first = int(_display_id_for_text(view, "HDMI audio compiles in when"))
    last = int(_display_id_for_text(view, "The matrix covers audio configurations."))
    inverse = git_stage_batch(
        "discard",
        "--to",
        "readme-inverse",
        "--line",
        f"{first}-{last + 1}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=final_block,
        check=False,
    )
    assert inverse.returncode == 0, inverse.stderr
    assert path.read_text() == final
    git_stage_batch("drop", "readme-inverse")
    assert path.read_text() == final

    view = git_stage_batch("show", "--file", "README.md", "--page", "all").stdout
    first = int(_display_id_for_text(view, "HDMI audio and HDMI-CEC compile in"))
    last = int(_display_id_for_text(view, "The CEC helper must be present."))
    replacement = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        f"{first}-{last + 1}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_block,
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == predecessor
    state = json.loads(
        _show_file(
            functional_repo,
            "refs/git-stage-batch/state/cec-build-selection",
            "batch.json",
        )
    )
    file_metadata = state["files"]["README.md"]
    assert len(file_metadata["deletions"]) == 1
    assert file_metadata["deletions"][0]["source_alternative"] is True
    assert file_metadata["replacement_units"][0]["deletion_indices"] == [0]

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add predecessor documentation"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "cec-build-selection",
        "--file",
        "README.md",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_whole_function_transform_replays_after_four_disjoint_deletions(
    functional_repo,
):
    """Four earlier deletions must not invalidate a later F21-to-P6 function."""
    refresh_function = (
        "void refresh_connector(void *connector)\n"
        "{\n"
        "\tbool valid;\n"
        "\tvalid = connector != NULL;\n"
        "\tif (valid)\n"
        "\t\tset_valid(connector);\n"
        "\telse\n"
        "\t\tset_invalid(connector);\n"
        "}\n\n"
    )
    connector_function = (
        "int connector_init(void *connector)\n"
        "{\n"
        "\tvoid *output;\n"
        "\toutput = allocate_output();\n"
        "\tif (!output)\n"
        "\t\treturn -1;\n"
        "\tregister_output(connector, output);\n"
        "\treturn 0;\n"
        "}\n\n"
    )
    final_init = (
        "int core_init(struct device *dev)\n"
        "{\n"
        "\tstruct connector_list_iter iter;\n"
        "\tstruct connector *connector;\n"
        "\tint ret = 0;\n"
        "\n"
        "\tconnector_list_iter_begin(dev, &iter);\n"
        "\tfor_each_connector_iter(connector, &iter) {\n"
        "\t\tstruct private_connector *private_connector;\n"
        "\n"
        "\t\tif (connector->type == CONNECTOR_WRITEBACK)\n"
        "\t\t\tcontinue;\n"
        "\t\tprivate_connector = to_private_connector(connector);\n"
        "\t\tret = connector_init(private_connector);\n"
        "\t\tif (ret)\n"
        "\t\t\tbreak;\n"
        "\t}\n"
        "\tconnector_list_iter_end(&iter);\n"
        "\treturn ret;\n"
        "}\n\n"
    )
    predecessor_init = (
        "int core_init(struct device *dev)\n{\n\t(void)dev;\n\treturn 0;\n}\n\n"
    )
    deleted_functions = refresh_function + connector_function
    final = (
        "prefix\n"
        "\trefresh_connector(output);\n"
        "keep one\n\n"
        + deleted_functions
        + final_init
        + "static void retained_function(void)\n"
        + "{\n"
        "\tkeep_work();\n"
        "\telse_if_changed();\n"
        "\trefresh_connector(output);\n"
        "\tkeep_more_work();\n"
        "\trefresh_connector(connector);\n"
        "}\n"
        "suffix\n"
    )
    after_deletions = (
        final.replace("\trefresh_connector(output);\n", "", 1)
        .replace(deleted_functions, "")
        .replace("\telse_if_changed();\n\trefresh_connector(output);\n", "")
        .replace("\trefresh_connector(connector);\n", "")
    )
    predecessor = after_deletions.replace(final_init, predecessor_init)
    path = functional_repo / "core.c"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    early = int(_display_id_for_text(view, "keep one")) - 1
    refresh_first = int(_display_id_for_text(view, "void refresh_connector"))
    init_first = int(_display_id_for_text(view, "int core_init"))
    changed = int(_display_id_for_text(view, "\telse_if_changed();"))
    tail = int(_display_id_for_text(view, "\trefresh_connector(connector);"))
    deletions = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        (f"{early},{refresh_first}-{init_first - 1},{changed}-{changed + 1},{tail}"),
        "--no-auto-advance",
        check=False,
    )
    assert deletions.returncode == 0, deletions.stderr
    assert path.read_text() == after_deletions

    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    init_first = int(_display_id_for_text(view, "int core_init"))
    next_first = int(_display_id_for_text(view, "static void retained_function"))
    transform = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        f"{init_first}-{next_first - 1}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_init,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/monitor-events",
            "core.c",
        )
        == final
    )
    state = json.loads(
        _show_file(
            functional_repo,
            "refs/git-stage-batch/state/monitor-events",
            "batch.json",
        )
    )
    source_commit = state["files"]["core.c"]["batch_source_commit"]
    expected_source = final + predecessor
    assert _show_file(functional_repo, source_commit, "core.c") == expected_source

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "core.c"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add monitor-event predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-events",
        "--file",
        "core.c",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_transformed_function_replays_with_duplicate_deletion_anchor_pair(
    functional_repo,
):
    """A retained duplicate pair must not confuse a deleted line's anchor."""
    final_init = (
        "int core_init(struct device *dev)\n"
        "{\n"
        "\tstruct connector_list_iter iter;\n"
        "\tstruct connector *connector;\n"
        "\tint ret = 0;\n"
        "\n"
        "\tconnector_list_iter_begin(dev, &iter);\n"
        "\tfor_each_connector_iter(connector, &iter) {\n"
        "\t\tstruct private_connector *private_connector;\n"
        "\n"
        "\t\tif (connector->type == CONNECTOR_WRITEBACK)\n"
        "\t\t\tcontinue;\n"
        "\t\tprivate_connector = to_private_connector(connector);\n"
        "\t\tret = connector_init(private_connector);\n"
        "\t\tif (ret)\n"
        "\t\t\tbreak;\n"
        "\t}\n"
        "\tconnector_list_iter_end(&iter);\n"
        "\treturn ret;\n"
        "}\n\n"
    )
    predecessor_init = (
        "int core_init(struct device *dev)\n{\n\t(void)dev;\n\treturn 0;\n}\n\n"
    )
    repeated_anchor = "\t\tspin_unlock_irqrestore(&output->lock, flags);\n"
    deleted_line = "\t\trefresh_connector(connector);\n"
    final = (
        "prefix\n\n"
        + final_init
        + repeated_anchor
        + "\t\treturn;\n"
        + "static void suspend_connector(void)\n"
        "{\n" + repeated_anchor + deleted_line + "\t\treturn;\n"
        "}\n"
        "suffix\n"
    )
    after_deletion = final.replace(deleted_line, "")
    predecessor = after_deletion.replace(final_init, predecessor_init)
    path = functional_repo / "core.c"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    deleted_id = _display_id_for_text(view, deleted_line.rstrip("\n"))
    deletion = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        deleted_id,
        "--no-auto-advance",
        check=False,
    )
    assert deletion.returncode == 0, deletion.stderr
    assert path.read_text() == after_deletion

    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    init_first = int(_display_id_for_text(view, "int core_init"))
    transform = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        f"{init_first}-{init_first + 20}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_init,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/monitor-events",
            "core.c",
        )
        == final
    )
    state = json.loads(
        _show_file(
            functional_repo,
            "refs/git-stage-batch/state/monitor-events",
            "batch.json",
        )
    )
    source_commit = state["files"]["core.c"]["batch_source_commit"]
    expected_source = final + predecessor
    assert _show_file(functional_repo, source_commit, "core.c") == expected_source

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "core.c"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add monitor-event predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-events",
        "--file",
        "core.c",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_transformed_function_replays_after_call_and_definition_deletions(
    functional_repo,
):
    """Two separated deletions must compose with a later F21-to-P6 edit."""
    deleted_call = "\tcastkms_cec_core_refresh_connector(&output->connector->base);\n"
    deleted_definition = (
        "void castkms_cec_core_refresh_connector(struct drm_connector *connector)\n"
    )
    final_init = (
        "int castkms_cec_core_init(struct drm_device *dev)\n"
        "{\n"
        "\tstruct drm_connector_list_iter iter;\n"
        "\tstruct drm_connector *connector;\n"
        "\tint ret = 0;\n"
        "\n"
        "\tdrm_connector_list_iter_begin(dev, &iter);\n"
        "\tdrm_for_each_connector_iter(connector, &iter) {\n"
        "\t\tstruct castkms_connector *castkms_connector;\n"
        "\n"
        "\t\tif (connector->connector_type == DRM_MODE_CONNECTOR_WRITEBACK)\n"
        "\t\t\tcontinue;\n"
        "\t\tcastkms_connector = drm_connector_to_castkms_connector(connector);\n"
        "\t\tret = castkms_cec_core_connector_init(castkms_connector);\n"
        "\t\tif (ret)\n"
        "\t\t\tbreak;\n"
        "\t}\n"
        "\tdrm_connector_list_iter_end(&iter);\n"
        "\treturn ret;\n"
        "}\n\n"
    )
    predecessor_init = (
        "int castkms_cec_core_init(struct drm_device *dev)\n"
        "{\n"
        "\t(void)dev;\n"
        "\treturn 0;\n"
        "}\n\n"
    )
    final = (
        "prefix\n"
        + deleted_call
        + "keep\n\n"
        + deleted_definition
        + final_init
        + "suffix\n"
    )
    after_deletions = final.replace(deleted_call, "").replace(
        deleted_definition,
        "",
    )
    predecessor = after_deletions.replace(final_init, predecessor_init)
    path = functional_repo / "core.c"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    call_id = _display_id_for_text(view, deleted_call.rstrip("\n"))
    definition_id = _display_id_for_text(
        view,
        deleted_definition.rstrip("\n"),
    )
    deletions = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        f"{call_id},{definition_id}",
        "--no-auto-advance",
        check=False,
    )
    assert deletions.returncode == 0, deletions.stderr
    assert path.read_text() == after_deletions

    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    init_first = int(_display_id_for_text(view, "int castkms_cec_core_init"))
    transform = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        f"{init_first}-{init_first + 20}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_init,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/monitor-events",
            "core.c",
        )
        == final
    )
    state = json.loads(
        _show_file(
            functional_repo,
            "refs/git-stage-batch/state/monitor-events",
            "batch.json",
        )
    )
    source_commit = state["files"]["core.c"]["batch_source_commit"]
    expected_source = final + predecessor
    assert _show_file(functional_repo, source_commit, "core.c") == expected_source

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "core.c"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add monitor-event predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-events",
        "--file",
        "core.c",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_transformed_function_replays_after_composed_deletion_geometries(
    functional_repo,
):
    """Separately replayable deletion groups must compose before a transform."""
    deleted_call = "\tcastkms_cec_core_refresh_connector(&output->connector->base);\n"
    deleted_definition = (
        "void castkms_cec_core_refresh_connector(struct drm_connector *connector)\n"
    )
    final_init = (
        "int castkms_cec_core_init(struct drm_device *dev)\n"
        "{\n"
        "\tstruct drm_connector_list_iter iter;\n"
        "\tstruct drm_connector *connector;\n"
        "\tint ret = 0;\n"
        "\n"
        "\tdrm_connector_list_iter_begin(dev, &iter);\n"
        "\tdrm_for_each_connector_iter(connector, &iter) {\n"
        "\t\tstruct castkms_connector *castkms_connector;\n"
        "\n"
        "\t\tif (connector->connector_type == DRM_MODE_CONNECTOR_WRITEBACK)\n"
        "\t\t\tcontinue;\n"
        "\t\tcastkms_connector = drm_connector_to_castkms_connector(connector);\n"
        "\t\tret = castkms_cec_core_connector_init(castkms_connector);\n"
        "\t\tif (ret)\n"
        "\t\t\tbreak;\n"
        "\t}\n"
        "\tdrm_connector_list_iter_end(&iter);\n"
        "\treturn ret;\n"
        "}\n\n"
    )
    predecessor_init = (
        "int castkms_cec_core_init(struct drm_device *dev)\n"
        "{\n"
        "\t(void)dev;\n"
        "\treturn 0;\n"
        "}\n\n"
    )
    repeated_anchor = "\t\tspin_unlock_irqrestore(&output->lock, flags);\n"
    anchored_deletion = "\t\trefresh_connector(connector);\n"
    final = (
        "prefix\n"
        + deleted_call
        + "keep\n\n"
        + deleted_definition
        + final_init
        + repeated_anchor
        + "\t\treturn;\n"
        + "static void suspend_connector(void)\n"
        "{\n" + repeated_anchor + anchored_deletion + "\t\treturn;\n"
        "}\n"
        "suffix\n"
    )
    after_deletions = (
        final.replace(deleted_call, "")
        .replace(deleted_definition, "")
        .replace(anchored_deletion, "")
    )
    predecessor = after_deletions.replace(final_init, predecessor_init)
    path = functional_repo / "core.c"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    call_id = _display_id_for_text(view, deleted_call.rstrip("\n"))
    definition_id = _display_id_for_text(
        view,
        deleted_definition.rstrip("\n"),
    )
    anchored_id = _display_id_for_text(view, anchored_deletion.rstrip("\n"))
    deletions = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        f"{call_id},{definition_id},{anchored_id}",
        "--no-auto-advance",
        check=False,
    )
    assert deletions.returncode == 0, deletions.stderr
    assert path.read_text() == after_deletions

    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    init_first = int(_display_id_for_text(view, "int castkms_cec_core_init"))
    transform = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        f"{init_first}-{init_first + 20}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_init,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/monitor-events",
            "core.c",
        )
        == final
    )
    state = json.loads(
        _show_file(
            functional_repo,
            "refs/git-stage-batch/state/monitor-events",
            "batch.json",
        )
    )
    source_commit = state["files"]["core.c"]["batch_source_commit"]
    expected_source = final + predecessor
    assert _show_file(functional_repo, source_commit, "core.c") == expected_source

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "core.c"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add monitor-event predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-events",
        "--file",
        "core.c",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_transformed_function_replays_after_deleted_connector_init(
    functional_repo,
):
    """A complete intervening function must compose with the fixed anchors."""
    deleted_call = "\tcastkms_cec_core_refresh_connector(&output->connector->base);\n"
    retained_refresh = (
        "void castkms_cec_core_refresh_connector("
        "struct drm_connector *connector)\n"
        "{\n"
        "\tstruct castkms_cec_output *output = connector_to_cec(connector);\n"
        "\tbool should_be_valid;\n"
        "\tunsigned long flags;\n"
        "\n"
        "\tif (!output)\n"
        "\t\treturn;\n"
        "#if IS_ENABLED(CONFIG_KUNIT)\n"
        "\tif (output->test_ops)\n"
        "\t\treturn;\n"
        "#endif\n"
        "\n"
        "\tspin_lock_irqsave(&output->lock, flags);\n"
        "\tshould_be_valid = READ_ONCE(output->connector->monitor_attached) &&\n"
        "\t\t\t  output->transport &&\n"
        "\t\t\t  castkms_capture_authority_is_active("
        "output->transport->authority) &&\n"
        "\t\t\t  output->transport_online &&\n"
        "\t\t\t  connector->display_info.source_physical_address !=\n"
        "\t\t\t  CEC_PHYS_ADDR_INVALID;\n"
        "\tspin_unlock_irqrestore(&output->lock, flags);\n"
        "\n"
        "\tif (should_be_valid)\n"
        "\t\tdrm_connector_cec_phys_addr_set(connector);\n"
        "\telse\n"
        "\t\tdrm_connector_cec_phys_addr_invalidate(connector);\n"
        "}\n\n"
    )
    deleted_connector_init = (
        "int castkms_cec_core_connector_init("
        "struct castkms_connector *connector)\n"
        "{\n"
        "\tstruct drm_connector *base = &connector->base;\n"
        "\tstruct drm_device *dev = base->dev;\n"
        "\tstruct castkms_cec_output *output;\n"
        "\tchar name[64];\n"
        "\tint ret;\n"
        "\n"
        "\toutput = drmm_kzalloc(dev, sizeof(*output), GFP_KERNEL);\n"
        "\tif (!output) {\n"
        '\t\tdrm_warn(dev, "castkms: failed to allocate CEC output %u\\n",\n'
        "\t\t\t connector->output_index);\n"
        "\t\treturn -ENOMEM;\n"
        "\t}\n"
        "\n"
        "\toutput->connector = connector;\n"
        "\tspin_lock_init(&output->lock);\n"
        "\tinit_waitqueue_head(&output->transport_wait);\n"
        "\tINIT_DELAYED_WORK(&output->tx_timeout_work, cec_tx_timeout_work_fn);\n"
        "\toutput->next_cookie = 1;\n"
        "\n"
        '\tsnprintf(name, sizeof(name), "%s-output-%u",\n'
        "\t\t dev_name(dev->dev), connector->output_index);\n"
        "\tret = drmm_connector_hdmi_cec_register(base, &castkms_cec_funcs,\n"
        "\t\t\t\t\t       name, 1, dev->dev);\n"
        "\tif (ret) {\n"
        '\t\tdrm_err(dev, "castkms: CEC register failed for output %u: %d\\n",\n'
        "\t\t\tconnector->output_index, ret);\n"
        "\t\treturn ret;\n"
        "\t}\n"
        "\n"
        "\tconnector->cec = output;\n"
        "\treturn 0;\n"
        "}\n\n"
    )
    final_init = (
        "int castkms_cec_core_init(struct drm_device *dev)\n"
        "{\n"
        "\tstruct drm_connector_list_iter iter;\n"
        "\tstruct drm_connector *connector;\n"
        "\tint ret = 0;\n"
        "\n"
        "\tdrm_connector_list_iter_begin(dev, &iter);\n"
        "\tdrm_for_each_connector_iter(connector, &iter) {\n"
        "\t\tstruct castkms_connector *castkms_connector;\n"
        "\n"
        "\t\tif (connector->connector_type == DRM_MODE_CONNECTOR_WRITEBACK)\n"
        "\t\t\tcontinue;\n"
        "\t\tcastkms_connector = drm_connector_to_castkms_connector(connector);\n"
        "\t\tret = castkms_cec_core_connector_init(castkms_connector);\n"
        "\t\tif (ret)\n"
        "\t\t\tbreak;\n"
        "\t}\n"
        "\tdrm_connector_list_iter_end(&iter);\n"
        "\treturn ret;\n"
        "}\n\n"
    )
    predecessor_init = (
        "int castkms_cec_core_init(struct drm_device *dev)\n"
        "{\n"
        "\t(void)dev;\n"
        "\treturn 0;\n"
        "}\n\n"
    )
    repeated_anchor = "\t\tspin_unlock_irqrestore(&output->lock, flags);\n"
    anchored_deletion = "\t\tcastkms_cec_core_refresh_connector(connector);\n"
    final = (
        "prefix\n"
        + deleted_call
        + "keep\n\n"
        + retained_refresh
        + deleted_connector_init
        + final_init
        + repeated_anchor
        + "\t\treturn;\n"
        + "static void suspend_connector(void)\n"
        + "{\n"
        + repeated_anchor
        + anchored_deletion
        + "\t\treturn;\n"
        + "}\n"
        + "suffix\n"
    )
    after_deletions = (
        final.replace(deleted_call, "")
        .replace(deleted_connector_init, "")
        .replace(anchored_deletion, "")
    )
    predecessor = after_deletions.replace(final_init, predecessor_init)
    path = functional_repo / "core.c"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    call_id = _display_id_for_text(view, deleted_call.rstrip("\n"))
    connector_first = int(
        _display_id_for_text(view, "int castkms_cec_core_connector_init")
    )
    init_first = int(_display_id_for_text(view, "int castkms_cec_core_init"))
    anchored_id = _display_id_for_text(view, anchored_deletion.rstrip("\n"))
    deletions = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        f"{call_id},{connector_first}-{init_first - 1},{anchored_id}",
        "--no-auto-advance",
        check=False,
    )
    assert deletions.returncode == 0, deletions.stderr
    assert path.read_text() == after_deletions

    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    init_first = int(_display_id_for_text(view, "int castkms_cec_core_init"))
    transform = git_stage_batch(
        "discard",
        "--to",
        "monitor-events",
        "--line",
        f"{init_first}-{init_first + 20}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_init,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == predecessor
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/monitor-events",
            "core.c",
        )
        == final
    )
    state = json.loads(
        _show_file(
            functional_repo,
            "refs/git-stage-batch/state/monitor-events",
            "batch.json",
        )
    )
    source_commit = state["files"]["core.c"]["batch_source_commit"]
    expected_source = final + predecessor
    assert _show_file(functional_repo, source_commit, "core.c") == expected_source

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "core.c"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add monitor-event predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    assert path.read_text() == predecessor
    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-events",
        "--file",
        "core.c",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_second_disjoint_transform_preserves_first_saved_region(
    functional_repo,
):
    """A later F24-to-P4 edit must retain an earlier F60-to-P34 final."""

    def block(lines):
        return "\n".join(lines) + "\n"

    first_final = block(
        [
            "static void castkms_cec_resource_suspend("
            "struct castkms_capture_authority_resource *resource,",
            "\t\t\t\t\t int status)",
            "{",
            "\tstruct castkms_cec_transport *transport =",
            "\t\tcontainer_of(resource, struct castkms_cec_transport, resource);",
            "\tstruct castkms_cec_output *output = transport->output;",
            "\tunsigned long flags;",
            "\tbool aborted;",
            "",
            "\t(void)status;",
            "\tspin_lock_irqsave(&output->lock, flags);",
            "\tif (output->transport != transport) {",
            "\t\tspin_unlock_irqrestore(&output->lock, flags);",
            "\t\treturn;",
            "\t}",
            "\taborted = cec_abort_pending_locked(output);",
            "\toutput->transport_online = false;",
            "\toutput->transport_cleanup++;",
            "\toutput->state_generation++;",
            "\tspin_unlock_irqrestore(&output->lock, flags);",
            "",
            "\tcastkms_cec_core_finish_cleanup(output, transport, aborted, false);",
            "}",
            "",
            "static void castkms_cec_resource_revoke("
            "struct castkms_capture_authority_resource *resource,",
            "\t\t\t\t\tint status)",
            "{",
            "\tstruct castkms_cec_transport *transport =",
            "\t\tcontainer_of(resource, struct castkms_cec_transport, resource);",
            "\tstruct castkms_cec_output *output = transport->output;",
            "\tunsigned long flags;",
            "\tbool aborted = false;",
            "\tbool attached = false;",
            "",
            "\t(void)status;",
            "\tspin_lock_irqsave(&output->lock, flags);",
            "\tif (output->transport == transport) {",
            "\t\taborted = cec_abort_pending_locked(output);",
            "\t\toutput->transport = NULL;",
            "\t\toutput->transport_online = false;",
            "\t\toutput->transport_cleanup++;",
            "\t\toutput->state_generation++;",
            "\t\tattached = true;",
            "\t}",
            "\tspin_unlock_irqrestore(&output->lock, flags);",
            "",
            "\tif (attached) {",
            "\t\tcastkms_cec_core_finish_cleanup(output, transport, aborted, true);",
            "\t} else {",
            "\t\ttransport->ops->release(transport->data);",
            "\t\tkfree(transport);",
            "\t}",
            "}",
            "",
            "static const struct castkms_capture_authority_resource_ops",
            "castkms_cec_resource_ops = {",
            "\t.suspend = castkms_cec_resource_suspend,",
            "\t.revoke = castkms_cec_resource_revoke,",
            "};",
            "",
        ]
    )
    first_predecessor = block(
        [
            "static void castkms_cec_resource_suspend("
            "struct castkms_capture_authority_resource *resource,",
            "\t\t\t\t\t int status)",
            "{",
            "\t(void)resource;",
            "\t(void)status;",
            "}",
            "",
            "static void castkms_cec_resource_revoke("
            "struct castkms_capture_authority_resource *resource,",
            "\t\t\t\t\tint status)",
            "{",
            "\tstruct castkms_cec_transport *transport =",
            "\t\tcontainer_of(resource, struct castkms_cec_transport, resource);",
            "\tstruct castkms_cec_output *output = transport->output;",
            "\tunsigned long flags;",
            "",
            "\t(void)status;",
            "\tspin_lock_irqsave(&output->lock, flags);",
            "\tif (output->transport == transport) {",
            "\t\toutput->transport = NULL;",
            "\t\toutput->transport_online = false;",
            "\t\toutput->state_generation++;",
            "\t}",
            "\tspin_unlock_irqrestore(&output->lock, flags);",
            "",
            "\ttransport->ops->release(transport->data);",
            "\tkfree(transport);",
            "}",
            "",
            "static const struct castkms_capture_authority_resource_ops",
            "castkms_cec_resource_ops = {",
            "\t.suspend = castkms_cec_resource_suspend,",
            "\t.revoke = castkms_cec_resource_revoke,",
            "};",
            "",
        ]
    )
    second_final = block(
        [
            "void castkms_cec_core_suspend_connector(struct drm_connector *connector)",
            "{",
            "\tstruct castkms_cec_output *output = connector_to_cec(connector);",
            "\tstruct castkms_cec_transport *transport;",
            "\tunsigned long flags;",
            "\tbool aborted;",
            "",
            "\tif (!output)",
            "\t\treturn;",
            "",
            "\t/* Keep the binding across monitor reattachment. */",
            "\tspin_lock_irqsave(&output->lock, flags);",
            "\ttransport = output->transport;",
            "\tif (!transport) {",
            "\t\tspin_unlock_irqrestore(&output->lock, flags);",
            "\t\treturn;",
            "\t}",
            "\taborted = cec_abort_pending_locked(output);",
            "\toutput->transport_cleanup++;",
            "\toutput->state_generation++;",
            "\tspin_unlock_irqrestore(&output->lock, flags);",
            "",
            "\tfinish_cleanup(output, transport, aborted);",
            "}",
        ]
    )
    second_predecessor = block(
        [
            "void castkms_cec_core_suspend_connector(struct drm_connector *connector)",
            "{",
            "\t(void)connector;",
            "}",
        ]
    )
    retained = "static int retained(void)\n{\n\treturn 0;\n}\n\n"
    suffix = "EXPORT_SYMBOL_IF_KUNIT(castkms_cec_core_suspend_connector);\nsuffix\n"
    assert len(first_final.splitlines()) == 60
    assert len(first_predecessor.splitlines()) == 34
    assert len(second_final.splitlines()) == 24
    assert len(second_predecessor.splitlines()) == 4

    final = "prefix\n" + first_final + retained + second_final + suffix
    after_first = "prefix\n" + first_predecessor + retained + second_final + suffix
    predecessor = (
        "prefix\n" + first_predecessor + retained + second_predecessor + suffix
    )
    path = functional_repo / "core.c"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    first = int(_display_id_for_text(view, "static void castkms_cec_resource_suspend"))
    retained_first = int(_display_id_for_text(view, "static int retained"))
    transform = git_stage_batch(
        "discard",
        "--to",
        "authority-cleanup",
        "--line",
        f"{first}-{retained_first - 1}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=first_predecessor,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    assert path.read_text() == after_first
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/authority-cleanup",
            "core.c",
        )
        == final
    )

    view = git_stage_batch("show", "--file", "core.c", "--page", "all").stdout
    second = int(_display_id_for_text(view, "void castkms_cec_core_suspend_connector"))
    export = int(
        _display_id_for_text(
            view,
            "EXPORT_SYMBOL_IF_KUNIT(castkms_cec_core_suspend_connector)",
        )
    )
    transform = git_stage_batch(
        "discard",
        "--to",
        "authority-cleanup",
        "--line",
        f"{second}-{export - 1}",
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
            "refs/git-stage-batch/batches/authority-cleanup",
            "core.c",
        )
        == final
    )


def test_single_line_transform_survives_prior_same_file_deletion(
    functional_repo,
):
    """A usage replacement must survive an earlier deletion in the batch."""

    removed = (
        "static int open_device(const char *path)\n{\n\treturn path != NULL;\n}\n\n"
    )
    final_usage = (
        '\tfprintf(stderr, "usage: %s [--grant-fd FD] [DRM-DEVICE]\\n", program);\n'
    )
    predecessor_usage = '\tfprintf(stderr, "usage: %s [--grant-fd FD]\\n", program);\n'
    retained = (
        "static void usage(const char *program)\n"
        "{\n" + final_usage + "}\n\n"
        "int main(void)\n"
        "{\n"
        '\tusage("tool");\n'
        "\treturn 0;\n"
        "}\n"
    )
    final = "prefix\n" + removed + retained
    after_delete = "prefix\n" + retained
    predecessor = after_delete.replace(final_usage, predecessor_usage)
    path = functional_repo / "tool.c"
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "tool.c", "--page", "all").stdout
    removed_first = int(_display_id_for_text(view, "static int open_device"))
    usage_first = int(_display_id_for_text(view, "static void usage"))
    deletion = git_stage_batch(
        "discard",
        "--to",
        "authority-cleanup",
        "--line",
        f"{removed_first}-{usage_first - 1}",
        "--no-auto-advance",
        check=False,
    )
    assert deletion.returncode == 0, deletion.stderr
    assert path.read_text() == after_delete
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/authority-cleanup",
            "tool.c",
        )
        == removed
    )

    view = git_stage_batch("show", "--file", "tool.c", "--page", "all").stdout
    usage_line = _display_id_for_text(view, "[--grant-fd FD] [DRM-DEVICE]")
    transform = git_stage_batch(
        "discard",
        "--to",
        "authority-cleanup",
        "--line",
        usage_line,
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_usage,
        check=False,
    )
    assert transform.returncode == 0, transform.stderr
    actual_live = path.read_text()
    assert (
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/authority-cleanup",
            "tool.c",
        )
        == final
    )
    state = json.loads(
        _show_file(
            functional_repo,
            "refs/git-stage-batch/state/authority-cleanup",
            "batch.json",
        )
    )
    source_commit = state["files"]["tool.c"]["batch_source_commit"]
    assert _show_file(functional_repo, source_commit, "tool.c") == final + predecessor
    assert actual_live == predecessor

    git_stage_batch("stop")
    path.write_text(predecessor)
    subprocess.run(
        ["git", "add", "tool.c"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add authority-cleanup predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "authority-cleanup",
        "--file",
        "tool.c",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == final


def test_whole_file_expansion_materializes_complete_replacement(
    functional_repo,
):
    """Replacing every current line with a longer file must keep the full text."""

    predecessor = "header\nkeep alpha\nkeep omega\nfooter\n"
    added_delta = "query one\nquery two\nquery three\n"
    final = predecessor + added_delta
    path = functional_repo / "whole-file.txt"
    path.write_text(predecessor)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show",
        "--file",
        "whole-file.txt",
        "--page",
        "all",
    ).stdout
    first = _display_id_for_text(view, "header")
    last = _display_id_for_text(view, "footer")
    transform = git_stage_batch(
        "discard",
        "--to",
        "whole-file-recovery",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=final,
        check=False,
    )

    assert transform.returncode == 0, transform.stderr
    actual_batch = _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/whole-file-recovery",
        "whole-file.txt",
    )
    actual_live = path.read_text()
    assert (actual_batch, actual_live) == (predecessor, final), (
        f"expected batch/live {(predecessor, final)!r}, got "
        f"{(actual_batch, actual_live)!r}; "
        f"live-delta-only={actual_live == added_delta}"
    )
    _assert_whole_file_replacement_geometry(
        functional_repo,
        batch_name="whole-file-recovery",
        file_path="whole-file.txt",
        predecessor=predecessor,
        final=final,
    )

    git_stage_batch("stop")
    path.unlink()
    replay = git_stage_batch(
        "apply",
        "--from",
        "whole-file-recovery",
        "--file",
        "whole-file.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == predecessor


def test_whole_file_internal_expansion_materializes_complete_replacement(
    functional_repo,
):
    """A full replacement with an internal expansion must remain one file."""

    predecessor = "header\nkeep alpha\nkeep omega\nfooter\n"
    final = (
        "header\nkeep alpha\nquery one\nquery two\nquery three\nkeep omega\nfooter\n"
    )
    path = functional_repo / "whole-file-internal.txt"
    path.write_text(predecessor)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show",
        "--file",
        "whole-file-internal.txt",
        "--page",
        "all",
    ).stdout
    first = _display_id_for_text(view, "header")
    last = _display_id_for_text(view, "footer")
    transform = git_stage_batch(
        "discard",
        "--to",
        "whole-file-internal-recovery",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=final,
        check=False,
    )

    assert transform.returncode == 0, transform.stderr
    actual_batch = _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/whole-file-internal-recovery",
        "whole-file-internal.txt",
    )
    actual_live = path.read_text()
    expected_batch = predecessor
    expected_live = final
    assert (actual_batch, actual_live) == (expected_batch, expected_live), (
        f"expected batch/live {(expected_batch, expected_live)!r}, got "
        f"{(actual_batch, actual_live)!r}"
    )
    _assert_whole_file_replacement_geometry(
        functional_repo,
        batch_name="whole-file-internal-recovery",
        file_path="whole-file-internal.txt",
        predecessor=predecessor,
        final=final,
    )

    git_stage_batch("stop")
    path.unlink()
    replay = git_stage_batch(
        "apply",
        "--from",
        "whole-file-internal-recovery",
        "--file",
        "whole-file-internal.txt",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == predecessor


def test_trailing_function_delete_after_prior_feature_batches_does_not_add_separator(
    functional_repo,
):
    """A trailing feature peel must not add another end-of-file blank."""

    api_path = functional_repo / "api.c"
    retained_prefix = (
        "#include <stddef.h>\n\nstatic_assert(sizeof(struct base_request) == 4);\n"
    )
    completion_assertion = "static_assert(sizeof(struct completion_request) == 8);\n"
    receive_assertion = "static_assert(sizeof(struct receive_request) == 12);\n"
    query_assertion = "static_assert(sizeof(struct query_request) == 16);\n"
    retained_function = "\nstatic int keep_request(void)\n{\n\treturn 1;\n}\n"
    completion_function = "\nstatic int complete_request(void)\n{\n\treturn 2;\n}\n"
    receive_function = "\nstatic int receive_request(void)\n{\n\treturn 3;\n}\n"
    query_function = "\nstatic int query_request(void)\n{\n\treturn 4;\n}\n"
    api_final = (
        retained_prefix
        + completion_assertion
        + receive_assertion
        + query_assertion
        + retained_function
        + completion_function
        + receive_function
        + query_function
    )
    after_query = (
        api_final.replace(query_assertion, "").replace(query_function, "") + "\n"
    )
    after_receive = (
        after_query.replace(receive_assertion, "").replace(receive_function, "") + "\n"
    )
    api_predecessor = after_receive.replace(completion_assertion, "").replace(
        completion_function,
        "",
    )
    api_path.write_text(api_final)

    git_stage_batch("start", "--no-auto-advance")
    for batch_name, assertion, function, return_line, expected in (
        (
            "state-query",
            "sizeof(struct query_request)",
            "static int query_request",
            "return 4",
            after_query,
        ),
        (
            "receive-injection",
            "sizeof(struct receive_request)",
            "static int receive_request",
            "return 3",
            after_receive,
        ),
    ):
        view = git_stage_batch("show", "--file", "api.c", "--page", "all").stdout
        assertion_id = _display_id_for_text(view, assertion)
        function_first = int(_display_id_for_text(view, function))
        function_return = int(_display_id_for_text(view, return_line))
        prior_discard = git_stage_batch(
            "discard",
            "--to",
            batch_name,
            "--line",
            f"{assertion_id},{function_first}-{function_return + 1}",
            "--no-auto-advance",
            check=False,
        )
        assert prior_discard.returncode == 0, prior_discard.stderr
        assert api_path.read_text() == expected

    view = git_stage_batch("show", "--file", "api.c", "--page", "all").stdout
    assertion_id = _display_id_for_text(view, "sizeof(struct completion_request)")
    function_first = int(_display_id_for_text(view, "static int complete_request"))
    complete_return = int(_display_id_for_text(view, "return 2"))
    first_discard = git_stage_batch(
        "discard",
        "--to",
        "transmit-completion",
        "--line",
        f"{assertion_id},{function_first}-{complete_return + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert first_discard.returncode == 0, first_discard.stderr
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/transmit-completion",
        "api.c",
    ) == completion_assertion + completion_function.removeprefix("\n")

    actual_api = api_path.read_text()
    assert actual_api == api_predecessor, (
        f"expected predecessor {api_predecessor!r}, got "
        f"{actual_api!r}; trailing blank reintroduced="
        f"{actual_api == api_predecessor + chr(10)}"
    )


def test_trailing_function_delete_after_prior_suffixes_does_not_add_separator(
    functional_repo,
):
    """Earlier sibling functions must not add an end-of-file blank."""

    def feature_function(name, value, *, authority=False):
        return (
            "\n"
            f"static int {name}(void)\n"
            "{\n"
            f"\tint ret = {value};\n"
            "\n"
            "\tprepare_request();\n"
            "\tret = execute_request();\n"
            + ("out_authority:\n" if authority else "\n")
            + "\tfinish_request();\n"
            "\trelease_request();\n"
            "out_dev:\n"
            "\tleave_request();\n"
            "\treturn ret;\n"
            "}\n"
        )

    def claimed_lines(batch_name):
        state = json.loads(
            _show_file(
                functional_repo,
                f"refs/git-stage-batch/state/{batch_name}",
                "batch.json",
            )
        )
        file_state = state["files"]["api.c"]
        lines = set()
        for claim in file_state["presence_claims"]:
            for spec in claim["source_lines"]:
                for token in spec.split(","):
                    if "-" in token:
                        first, last = (int(value) for value in token.split("-"))
                        lines.update(range(first, last + 1))
                    else:
                        lines.add(int(token))
        return file_state["batch_source_commit"], lines

    api_path = functional_repo / "api.c"
    retained_prefix = (
        "#include <stddef.h>\n\nstatic_assert(sizeof(struct base_request) == 4);\n"
    )
    completion_assertion = "static_assert(sizeof(struct completion_request) == 8);\n"
    receive_assertion = "static_assert(sizeof(struct receive_request) == 12);\n"
    query_assertion = "static_assert(sizeof(struct query_request) == 16);\n"
    retained_function = "\nstatic int keep_request(void)\n{\n\treturn 1;\n}\n"
    completion_function = feature_function("complete_request", 2)
    receive_function = feature_function("receive_request", 3)
    query_function = feature_function("query_request", 4, authority=True)
    api_final = (
        retained_prefix
        + completion_assertion
        + receive_assertion
        + query_assertion
        + retained_function
        + completion_function
        + receive_function
        + query_function
    )
    after_query = api_final.replace(query_assertion, "").replace(
        query_function,
        "",
    )
    after_receive = after_query.replace(receive_assertion, "").replace(
        receive_function,
        "",
    )
    api_predecessor = after_receive.replace(completion_assertion, "").replace(
        completion_function,
        "",
    )
    api_path.write_text(api_final)

    git_stage_batch("start", "--no-auto-advance")
    for (
        batch_name,
        assertion,
        assertion_text,
        function_name,
        function_text,
        expected,
    ) in (
        (
            "state-query-shared-tail",
            "sizeof(struct query_request)",
            query_assertion,
            "static int query_request",
            query_function,
            after_query,
        ),
        (
            "receive-injection-shared-tail",
            "sizeof(struct receive_request)",
            receive_assertion,
            "static int receive_request",
            receive_function,
            after_receive,
        ),
    ):
        view = git_stage_batch("show", "--file", "api.c", "--page", "all").stdout
        assertion_id = _display_id_for_text(view, assertion)
        function_first = int(_display_id_for_text(view, function_name))
        function_lines = len(function_text.removeprefix("\n").splitlines())
        prior_discard = git_stage_batch(
            "discard",
            "--to",
            batch_name,
            "--line",
            f"{assertion_id},{function_first - 1}-{function_first + function_lines - 1}",
            "--no-auto-advance",
            check=False,
        )
        assert prior_discard.returncode == 0, prior_discard.stderr
        assert api_path.read_text() == expected
        assert (
            _show_file(
                functional_repo,
                f"refs/git-stage-batch/batches/{batch_name}",
                "api.c",
            )
            == assertion_text + function_text
        )

    query_source, query_lines = claimed_lines("state-query-shared-tail")
    receive_source, receive_lines = claimed_lines("receive-injection-shared-tail")
    assert query_source == receive_source
    assert not query_lines & receive_lines
    assert api_path.read_text() == after_receive

    view = git_stage_batch("show", "--file", "api.c", "--page", "all").stdout
    assertion_id = _display_id_for_text(view, "sizeof(struct completion_request)")
    function_first = int(_display_id_for_text(view, "static int complete_request"))
    function_lines = len(completion_function.removeprefix("\n").splitlines())
    completion_discard = git_stage_batch(
        "discard",
        "--to",
        "transmit-completion-shared-tail",
        "--line",
        f"{assertion_id},{function_first}-{function_first + function_lines - 1}",
        "--no-auto-advance",
        check=False,
    )
    assert completion_discard.returncode == 0, completion_discard.stderr
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/transmit-completion-shared-tail",
        "api.c",
    ) == completion_assertion + completion_function.removeprefix("\n")

    actual_api = api_path.read_text()
    assert actual_api == api_predecessor, (
        f"expected predecessor {api_predecessor!r}, got "
        f"{actual_api!r}; trailing blank reintroduced="
        f"{actual_api == api_predecessor + chr(10)}"
    )


def test_completion_replay_restores_leading_separator(
    functional_repo,
):
    """The freshly created three-batch lineage must round-trip byte-exactly."""

    test_trailing_function_delete_after_prior_suffixes_does_not_add_separator(
        functional_repo,
    )

    api_path = functional_repo / "api.c"
    predecessor = api_path.read_text()
    completion_assertion = "static_assert(sizeof(struct completion_request) == 8);\n"
    completion_function = (
        "\n"
        "static int complete_request(void)\n"
        "{\n"
        "\tint ret = 2;\n"
        "\n"
        "\tprepare_request();\n"
        "\tret = execute_request();\n"
        "\n"
        "\tfinish_request();\n"
        "\trelease_request();\n"
        "out_dev:\n"
        "\tleave_request();\n"
        "\treturn ret;\n"
        "}\n"
    )
    expected_final = (
        predecessor.replace(
            "static_assert(sizeof(struct base_request) == 4);\n",
            "static_assert(sizeof(struct base_request) == 4);\n" + completion_assertion,
        )
        + completion_function
    )
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/transmit-completion-shared-tail",
        "api.c",
    ) == completion_assertion + completion_function.removeprefix("\n")

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", "api.c"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Commit exact shared-tail predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "transmit-completion-shared-tail",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr

    actual_final = api_path.read_text()
    assert actual_final == expected_final, (
        f"expected replay {expected_final!r}, got {actual_final!r}; "
        "leading separator dropped="
        f"{actual_final == expected_final.replace(completion_function, completion_function.removeprefix(chr(10)))}"
    )

    inverse = git_stage_batch(
        "discard",
        "--from",
        "transmit-completion-shared-tail",
        check=False,
    )
    assert inverse.returncode == 0, inverse.stderr
    assert api_path.read_text() == predecessor


def test_exact_multirange_replays_after_whole_file_predecessor_transform(
    functional_repo,
):
    """A later exact peel must replay over the transformed predecessor."""
    path = functional_repo / "tool.c"
    final = "base one\nrequest one\nbase two\nrequest two\ncompletion\ntail\n"
    request_snapshot = final.replace("completion\n", "")
    predecessor = request_snapshot.replace("request one\n", "").replace(
        "request two\n",
        "",
    )
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "tool.c", "--page", "all").stdout
    first = int(_display_id_for_text(view, "base one"))
    last = int(_display_id_for_text(view, "tail"))
    transformed = git_stage_batch(
        "discard",
        "--to",
        "completion",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=request_snapshot,
        check=False,
    )
    assert transformed.returncode == 0, transformed.stderr
    assert path.read_text() == request_snapshot

    view = git_stage_batch("show", "--file", "tool.c", "--page", "all").stdout
    request_one = _display_id_for_text(view, "request one")
    request_two = _display_id_for_text(view, "request two")
    exact = git_stage_batch(
        "discard",
        "--to",
        "request",
        "--line",
        f"{request_one},{request_two}",
        "--no-auto-advance",
        check=False,
    )
    assert exact.returncode == 0, exact.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "request",
        "--file",
        "tool.c",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == request_snapshot

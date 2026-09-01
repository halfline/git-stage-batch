"""Regression coverage for sequential replacements in Markdown additions."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_sequential_markdown_replacements_keep_batch_sections_ordered(
    functional_repo,
):
    """Narrowing several added sections must preserve the target batch tree."""
    path = functional_repo / "contract.md"
    baseline = (
        "# Contract\n"
        "\n"
        "The holder already exists.\n"
        "\n"
        "Status: attached.\n"
        "\n"
        "## Holder file\n"
        "\n"
        "The holder carries the capability.\n"
        "\n"
        "## Kernel implementation\n"
        "\n"
        "The adapter contains only UAPI concerns: grant ID, optional close-to-revoke\n"
        "file, holder lifetime, and events. Other objects retain the authority.\n"
    )
    target = (
        "# Contract\n"
        "\n"
        "The holder already exists.\n"
        "\n"
        "Status: detached cleanup retained.\n"
        "\n"
        "Creating a grant returns a grantor descriptor. Closing it revokes the\n"
        "holder, while polling reports terminal hangup. The relationship is\n"
        "observable in both directions. Final close ends the grant.\n"
        "\n"
        "## Grantor file\n"
        "\n"
        "The grantor is close-to-revoke and has two operations:\n"
        "\n"
        "- close the final reference to revoke synchronously;\n"
        "- poll for sticky terminal hangup.\n"
        "\n"
        "The grantor retains the device and conveys no holder rights.\n"
        "\n"
        "## Holder file\n"
        "\n"
        "The holder carries the capability.\n"
        "\n"
        "## Kernel implementation\n"
        "\n"
        "The adapter contains only UAPI concerns: grant ID, optional creator-file\n"
        "revocation, holder and grantor lifetime, grantor polling, and events.\n"
        "Other objects retain the authority rather than the adapter.\n"
    )
    predecessor = (
        "# Contract\n"
        "\n"
        "The holder already exists.\n"
        "\n"
        "Status: attached.\n"
        "\n"
        "Creating a grant returns a grantor descriptor. Closing it revokes the\n"
        "holder. Final close ends the grant.\n"
        "\n"
        "## Grantor file\n"
        "\n"
        "The grantor is close-to-revoke. Closing its final reference revokes\n"
        "synchronously.\n"
        "\n"
        "The grantor retains the device and conveys no holder rights.\n"
        "\n"
        "## Holder file\n"
        "\n"
        "The holder carries the capability.\n"
        "\n"
        "## Kernel implementation\n"
        "\n"
        "The adapter contains only UAPI concerns: grant ID, optional creator-file\n"
        "revocation, holder and grantor lifetime, and events. Other objects retain\n"
        "the authority rather than the adapter.\n"
    )
    expected_batch = target.replace(
        "Status: detached cleanup retained.", "Status: attached."
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add contract fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    status = _display_id_for_text(view, "Status: detached cleanup retained.")
    earlier = git_stage_batch(
        "discard",
        "--to",
        "earlier",
        "--line",
        str(status),
        "--no-auto-advance",
        check=False,
    )
    assert earlier.returncode == 0, earlier.stderr
    git_stage_batch("new", "poll", "--note", "Publish terminal hangup")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    start = _display_id_for_text(view, "Creating a grant returns")
    end = _display_id_for_text(view, "observable in both directions") + 1
    first = git_stage_batch(
        "discard",
        "--to",
        "poll",
        "--line",
        f"{start}-{end}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=(
            "Creating a grant returns a grantor descriptor. Closing it revokes the\n"
            "holder. Final close ends the grant.\n"
            "\n"
        ),
        check=False,
    )
    assert first.returncode == 0, first.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    start = _display_id_for_text(view, "## Grantor file")
    end = _display_id_for_text(view, "conveys no holder rights") + 1
    second = git_stage_batch(
        "discard",
        "--to",
        "poll",
        "--line",
        f"{start}-{end}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=(
            "## Grantor file\n"
            "\n"
            "The grantor is close-to-revoke. Closing its final reference revokes\n"
            "synchronously.\n"
            "\n"
            "The grantor retains the device and conveys no holder rights.\n"
            "\n"
        ),
        check=False,
    )
    assert second.returncode == 0, second.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    old = _display_id_for_text(view, "optional close-to-revoke")
    new = _display_id_for_text(view, "rather than the adapter")
    third = git_stage_batch(
        "discard",
        "--to",
        "poll",
        "--line",
        f"{old}-{new}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=(
            "The adapter contains only UAPI concerns: grant ID, optional creator-file\n"
            "revocation, holder and grantor lifetime, and events. Other objects retain\n"
            "the authority rather than the adapter.\n"
        ),
        check=False,
    )
    assert third.returncode == 0, third.stderr

    assert path.read_text() == predecessor
    batch_file = subprocess.run(
        ["git", "show", "refs/git-stage-batch/batches/poll:contract.md"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert batch_file == expected_batch

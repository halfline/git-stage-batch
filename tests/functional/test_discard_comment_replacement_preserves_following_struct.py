"""Regression coverage for a comment replacement before an evolved struct."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_comment_replacement_preserves_following_struct(functional_repo):
    """Replacing comment diff lines must not consume unchanged struct context."""
    path = functional_repo / "grant.h"
    baseline = (
        "#define GRANT 1\n"
        "\n"
        "/**\n"
        " * Grant contract.\n"
        " * Delegated grants remain live. The kernel creates the holder fd.\n"
        " */\n"
        "struct grant {\n"
        "    int holder_fd;\n"
        "};\n"
    )
    target = (
        "#define GRANT 1\n"
        "\n"
        "/**\n"
        " * GRANT_CONTROL:\n"
        " * Closing control revokes the grant. Polling reports terminal hangup.\n"
        " */\n"
        "#define GRANT_CONTROL 2\n"
        "\n"
        "/**\n"
        " * Grant contract.\n"
        " * Delegated grants remain live. Closing control revokes every grant\n"
        " * mode. The grantor reports POLLHUP once the grant is revoked. The kernel\n"
        " * creates both output descriptors.\n"
        " */\n"
        "struct grant {\n"
        "    int holder_fd;\n"
        "    int control_fd;\n"
        "};\n"
    )
    predecessor = (
        "#define GRANT 1\n"
        "\n"
        "/**\n"
        " * GRANT_CONTROL:\n"
        " * Closing control revokes the grant.\n"
        " */\n"
        "#define GRANT_CONTROL 2\n"
        "\n"
        "/**\n"
        " * Grant contract.\n"
        " * Delegated grants remain live. Closing control revokes every grant\n"
        " * mode. The kernel creates both output descriptors.\n"
        " */\n"
        "struct grant {\n"
        "    int holder_fd;\n"
        "    int control_fd;\n"
        "};\n"
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add grant fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("new", "poll", "--note", "Publish terminal hangup")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    control = _display_id_for_text(view, "GRANT_CONTROL:")
    control_end = _display_id_for_text(view, "#define GRANT_CONTROL") + 2
    first = git_stage_batch(
        "discard",
        "--to",
        "poll",
        "--line",
        f"{control}-{control_end}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=(
            " * GRANT_CONTROL:\n"
            " * Closing control revokes the grant.\n"
            " */\n"
            "#define GRANT_CONTROL 2\n"
            "\n"
            "/**\n"
        ),
        check=False,
    )
    assert first.returncode == 0, first.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    start = _display_id_for_text(view, "The kernel creates the holder fd")
    end = _display_id_for_text(view, "creates both output descriptors")
    result = git_stage_batch(
        "discard",
        "--to",
        "poll",
        "--line",
        f"{start}-{end}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=(
            " * Delegated grants remain live. Closing control revokes every grant\n"
            " * mode. The kernel creates both output descriptors.\n"
        ),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    control_fd = _display_id_for_text(view, "int control_fd")
    included = git_stage_batch(
        "include",
        "--to",
        "poll",
        "--line",
        str(control_fd),
        "--no-auto-advance",
        check=False,
    )
    assert included.returncode == 0, included.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    start = _display_id_for_text(view, "The kernel creates the holder fd")
    end = _display_id_for_text(view, "mode. The kernel creates both output descriptors")
    result = git_stage_batch(
        "discard",
        "--to",
        "poll",
        "--line",
        f"{start}-{end}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=(
            " * Delegated grants remain live. Closing control revokes every grant\n"
            " * mode. The kernel\n"
            " * creates both output descriptors.\n"
        ),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert " * mode. The kernel\n * creates both output descriptors.\n" in path.read_text()

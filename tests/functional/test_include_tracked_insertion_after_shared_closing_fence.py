"""Regression coverage for a tracked insertion after a shared closing fence."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view: str, text: str) -> int:
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_insertion_stays_after_unselected_shared_closing_fence(functional_repo):
    """A new function must follow the retained predecessor's closing brace."""
    path = functional_repo / "driver.c"
    baseline = (
        "static int modeset_init(void)\n"
        "{\n"
        "    return output_init();\n"
        "}\n"
        "\n"
        "int create(void)\n"
        "{\n"
        "    return 0;\n"
        "}\n"
    )
    target = (
        "static int modeset_init(void)\n"
        "{\n"
        "    return topology_init();\n"
        "}\n"
        "\n"
        "static void owner_fini(void)\n"
        "{\n"
        "    cleanup();\n"
        "}\n"
        "\n"
        "int create(void)\n"
        "{\n"
        "    return 0;\n"
        "}\n"
    )
    expected = baseline.replace(
        "\nint create(void)\n",
        "\nstatic void owner_fini(void)\n"
        "{\n"
        "    cleanup();\n"
        "}\n"
        "\nint create(void)\n",
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name], cwd=functional_repo, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Add driver fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    owner_fini = _display_id_for_text(view, "static void owner_fini")
    cleanup = _display_id_for_text(view, "cleanup();")
    result = git_stage_batch(
        "include",
        "--line",
        f"{owner_fini}-{cleanup}",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    staged = subprocess.run(
        ["git", "show", f":{path.name}"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert staged == expected

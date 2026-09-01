"""Regression coverage for replacement beside unselected deletions."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view: str, text: str) -> int:
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_line_as_preserves_adjacent_unselected_deletions(functional_repo):
    """Replacing one deleted line must retain neighboring baseline lines."""
    path = functional_repo / "driver.c"
    baseline = (
        "DEFINE_MODE_OPS(driver_ops);\n"
        "\n"
        "bool keep_enabled(void)\n"
        "{\n"
        "    return true;\n"
        "}\n"
    )
    replacement = (
        "static const struct mode_ops driver_ops = {\n"
        "    .open = open_mode,\n"
        "};\n"
    )
    working = replacement
    expected = replacement + baseline.split("\n", 1)[1]

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add driver fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(working)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    old_macro = _display_id_for_text(view, "DEFINE_MODE_OPS")
    result = git_stage_batch(
        "include",
        "--line",
        str(old_macro),
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement,
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

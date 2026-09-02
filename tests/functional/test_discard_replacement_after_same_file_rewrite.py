"""Regression coverage for a replacement after rewriting the same batch file."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _commit_file(repo, path):
    subprocess.run(
        ["git", "add", path.name],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", f"Add {path.name} baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_modified_line_replacement_retains_requested_partial_target(functional_repo):
    """Replacing the added side must preserve requested lower-concern words."""
    path = functional_repo / "Makefile"
    path.write_text(".PHONY: all clean\nall:\n\tbuild modules\n")
    _commit_file(functional_repo, path)
    git_stage_batch("stop", check=False)
    path.write_text(
        "AUDIO=y\n"
        ".PHONY: all matrix check clean\n"
        "all:\n"
        "\tbuild $(OPTIONS) modules\n"
        "check:\n"
        "\ttest -n yes\n"
    )
    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    audio = _display_id_for_text(view, "AUDIO=y")
    discard_audio = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        str(audio),
        "--no-auto-advance",
        check=False,
    )
    assert discard_audio.returncode == 0, discard_audio.stderr

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    old_command = _display_id_for_text(view, "build modules")
    new_command = _display_id_for_text(view, "build $(OPTIONS) modules")
    discard_command = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        f"{min(old_command, new_command)}-{max(old_command, new_command)}",
        "--no-auto-advance",
        check=False,
    )
    assert discard_command.returncode == 0, discard_command.stderr

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    target = _display_id_for_text(view, ".PHONY: all matrix check clean")
    replacement = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        str(target),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=".PHONY: all check clean\n",
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == (
        ".PHONY: all check clean\nall:\n\tbuild modules\ncheck:\n\ttest -n yes\n"
    )


def test_replacement_after_same_file_rewrite_restores_modified_line(functional_repo):
    """A later modified line must remain replaceable after the batch source moves."""
    path = functional_repo / "Makefile"
    base = "KDIR ?= /kernel\n\n.PHONY: all clean\n\nall:\n\tbuild modules\n"
    target = (
        "KDIR ?= /kernel\n"
        "AUDIO ?= y\n"
        "\n"
        "OPTIONS := \\\n"
        "\tAUDIO=$(AUDIO)\n"
        "\n"
        ".PHONY: all matrix check clean\n"
        "\n"
        "all:\n"
        "\tbuild $(OPTIONS) modules\n"
    )
    predecessor = (
        "KDIR ?= /kernel\n\n.PHONY: all check clean\n\nall:\n\tbuild modules\n"
    )
    path.write_text(base)
    _commit_file(functional_repo, path)
    git_stage_batch("stop", check=False)
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    audio = _display_id_for_text(view, "AUDIO ?= y")
    first = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        str(audio),
        "--no-auto-advance",
        check=False,
    )
    assert first.returncode == 0, first.stderr

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    options = _display_id_for_text(view, "OPTIONS :=")
    phony = _display_id_for_text(view, ".PHONY: all matrix check clean")
    rewrite = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        f"{options}-{phony}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=".PHONY: all check clean\n",
        check=False,
    )
    assert rewrite.returncode == 0, rewrite.stderr

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    command = _display_id_for_text(view, "build $(OPTIONS) modules")
    replacement = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        str(command),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="\tbuild modules\n",
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == predecessor


def test_apply_after_same_file_rewrite_and_discard_restores_target(functional_repo):
    """A mixed replacement/discard batch must replay onto its exact predecessor."""
    path = functional_repo / "Makefile"
    base = "KDIR ?= /kernel\n\n.PHONY: all clean\n\nall:\n\tbuild modules\n"
    target = (
        "KDIR ?= /kernel\n"
        "AUDIO ?= y\n"
        "\n"
        "OPTIONS := \\\n"
        "\tAUDIO=$(AUDIO)\n"
        "\n"
        ".PHONY: all matrix check clean\n"
        "\n"
        "all:\n"
        "\tbuild $(OPTIONS) modules\n"
    )
    predecessor = (
        "KDIR ?= /kernel\n\n.PHONY: all check clean\n\nall:\n\tbuild modules\n"
    )
    path.write_text(base)
    _commit_file(functional_repo, path)
    git_stage_batch("stop", check=False)
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    audio = _display_id_for_text(view, "AUDIO ?= y")
    first = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        str(audio),
        "--no-auto-advance",
        check=False,
    )
    assert first.returncode == 0, first.stderr

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    options = _display_id_for_text(view, "OPTIONS :=")
    phony = _display_id_for_text(view, ".PHONY: all matrix check clean")
    rewrite = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        f"{options}-{phony}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=".PHONY: all check clean\n",
        check=False,
    )
    assert rewrite.returncode == 0, rewrite.stderr

    view = git_stage_batch("show", "--file", "Makefile", "--page", "all").stdout
    old_command = _display_id_for_text(view, "build modules")
    new_command = _display_id_for_text(view, "build $(OPTIONS) modules")
    discard = git_stage_batch(
        "discard",
        "--to",
        "optional-audio",
        "--line",
        f"{min(old_command, new_command)}-{max(old_command, new_command)}",
        "--no-auto-advance",
        check=False,
    )
    assert discard.returncode == 0, discard.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "optional-audio",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target

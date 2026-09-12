"""Deferred methods survive formatting of an earlier staged file version."""

from pathlib import Path
import subprocess

from .conftest import git_stage_batch


FIXTURES = Path(__file__).parent / "fixtures"


def _git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def test_apply_after_removing_blank_line_from_staged_file(functional_repo):
    staged = (FIXTURES / "castkms_framebuffer_format_staged.rs").read_text()
    target = (FIXTURES / "castkms_framebuffer_format_target.rs").read_text()
    path = functional_repo / "framebuffer.rs"
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("include", "--file", path.name, "--as-stdin", input_text=staged)
    assert _git("show", f":{path.name}") == staged
    assert path.read_text() == target

    git_stage_batch("discard", "--to", "mapping", "--files", "**")
    assert path.read_text() == staged

    # rustfmt removes the empty line between the last method and its impl's
    # closing brace. Reproduce the exact edit without depending on rustfmt.
    assert staged.endswith("    }\n\n}\n")
    formatted = staged.removesuffix("    }\n\n}\n") + "    }\n}\n"
    path.write_text(formatted)
    git_stage_batch("include", "--file", path.name)
    _git("commit", "-m", "Add formatted framebuffer validator")
    assert _git("diff", "--", path.name) == ""
    head = _git("rev-parse", "HEAD")
    index = _git("ls-files", "--stage")

    result = git_stage_batch(
        "apply", "--from", "mapping", "--file", path.name, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert path.read_text() == target
    assert _git("rev-parse", "HEAD") == head
    assert _git("ls-files", "--stage") == index

"""Regression coverage for undoing replacements separated by file reviews."""

from .conftest import git_stage_batch


def _display_id_for_text(view: str, text: str) -> int:
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _content(prefix: str, count: int) -> str:
    return "".join(f"{prefix} {line:04d}\n" for line in range(1, count + 1))


def test_sequential_undo_crosses_intervening_large_file_reviews(functional_repo):
    """Fresh large-file reviews must not make an older undo conflict with itself."""
    specs = (
        ("capture.c", 1432, 656),
        ("grant.c", 1545, 1443),
        ("drm.c", 196, 178),
        ("drm.h", 40, 36),
    )
    paths = []

    for name, target_lines, predecessor_lines in specs:
        path = functional_repo / name
        target = _content(name, target_lines)
        predecessor = _content(name, predecessor_lines)
        path.write_text(target)
        paths.append((path, target, predecessor, target_lines))

    git_stage_batch("new", "reviewed-replacements")
    git_stage_batch("start", "--no-auto-advance")

    for path, _target, predecessor, target_lines in paths:
        view = git_stage_batch(
            "show", "--file", path.name, "--line", f"1-{target_lines}"
        ).stdout
        first = _display_id_for_text(view, f"{path.name} 0001")
        last = _display_id_for_text(view, f"{path.name} {target_lines:04d}")
        result = git_stage_batch(
            "discard",
            "--to",
            "reviewed-replacements",
            "--line",
            f"{first}-{last}",
            "--as-stdin",
            "--no-edge-overlap",
            "--no-auto-advance",
            input_text=predecessor,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert path.read_text() == predecessor

    git_stage_batch("validate")

    for path, target, _predecessor, _target_lines in reversed(paths):
        undo = git_stage_batch("undo", check=False)
        assert undo.returncode == 0, undo.stderr
        assert path.read_text() == target

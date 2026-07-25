"""Functional tests for include --line transient batch staging."""

import subprocess

from .conftest import git_stage_batch


def _commit_file(repo, path: str, content: str) -> None:
    file_path = repo / path
    file_path.write_text(content)
    subprocess.run(["git", "add", path], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"Add {path}"], check=True, cwd=repo, capture_output=True)


def _commit_file_bytes(repo, path: str, content: bytes) -> None:
    file_path = repo / path
    file_path.write_bytes(content)
    subprocess.run(["git", "add", path], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"Add {path}"], check=True, cwd=repo, capture_output=True)


def _index_content(repo, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f":{path}"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _index_bytes(repo, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f":{path}"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return result.stdout


def _batch_content(repo, batch_name: str, path: str) -> str:
    result = subprocess.run(
        [
            "git",
            "show",
            f"refs/git-stage-batch/batches/{batch_name}:{path}",
        ],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _prepare_staged_replacement(repo) -> None:
    _commit_file(
        repo,
        "file.txt",
        "head\n"
        "orig-one\n"
        "orig-two\n"
        "tail\n",
    )
    (repo / "file.txt").write_text(
        "staged-prefix\n"
        "head\n"
        "staged-one\n"
        "staged-two\n"
        "tail\n"
    )
    subprocess.run(
        ["git", "add", "file.txt"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    (repo / "file.txt").write_text(
        "staged-prefix\n"
        "head\n"
        "work-one\n"
        "work-two\n"
        "tail\n"
    )


def _prepare_ambiguous_middle_insertion(repo) -> None:
    _commit_file(
        repo,
        "file.txt",
        "top\n"
        "\n"
        "bottom\n",
    )
    (repo / "file.txt").write_text(
        "top\n"
        "import-a\n"
        "import-b\n"
        "import-c\n"
        "\n"
        "save-a\n"
        "save-b\n"
        "later-a\n"
        "\n"
        "bottom\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    git_stage_batch("include", "--line", "1-2", "--no-auto-advance")
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    git_stage_batch("include", "--line", "3", "--no-auto-advance")
    git_stage_batch("show", "--file", "file.txt", "--page", "all")


def test_include_line_transient_staging_first_replace_row(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1,3")

    assert _index_content(functional_repo, "file.txt") == "A\nb\n"


def test_include_line_transient_staging_second_replace_row(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "2,4")

    assert _index_content(functional_repo, "file.txt") == "a\nB\n"


def test_include_line_transient_staging_same_cardinality_replacement_by_position(functional_repo):
    _commit_file(functional_repo, "file.txt", "red\nblue\n")
    (functional_repo / "file.txt").write_text("circle\nsquare\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1,3")

    assert _index_content(functional_repo, "file.txt") == "circle\nblue\n"


def test_include_line_transient_staging_full_replace_selection(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1-4")

    assert _index_content(functional_repo, "file.txt") == "A\nB\n"


def test_include_line_transient_staging_pure_addition(functional_repo):
    _commit_file(functional_repo, "file.txt", "base\n")
    (functional_repo / "file.txt").write_text("base\nfoo\nbar\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_content(functional_repo, "file.txt") == "base\nfoo\n"


def test_discard_to_batch_after_consecutive_line_includes(functional_repo):
    _prepare_ambiguous_middle_insertion(functional_repo)

    result = git_stage_batch(
        "discard",
        "--to",
        "saved",
        "--line",
        "5-6",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_content(functional_repo, "saved", "file.txt") == (
        "top\n"
        "save-a\n"
        "save-b\n"
        "\n"
        "bottom\n"
    )
    assert _index_content(functional_repo, "file.txt") == (
        "top\n"
        "import-a\n"
        "import-b\n"
        "import-c\n"
        "\n"
        "bottom\n"
    )
    assert (functional_repo / "file.txt").read_text() == (
        "top\n"
        "import-a\n"
        "import-b\n"
        "import-c\n"
        "\n"
        "later-a\n"
        "\n"
        "bottom\n"
    )

    git_stage_batch("apply", "--from", "saved", "--file", "file.txt")

    assert (functional_repo / "file.txt").read_text() == (
        "top\n"
        "import-a\n"
        "import-b\n"
        "import-c\n"
        "\n"
        "save-a\n"
        "save-b\n"
        "later-a\n"
        "\n"
        "bottom\n"
    )


def test_discard_translates_repeated_boundary_from_staged_index(functional_repo):
    _commit_file(
        functional_repo,
        "file.txt",
        "top\n"
        "\n"
        "top\n"
        "\n"
        "bottom\n",
    )
    (functional_repo / "file.txt").write_text(
        "staged-u\n"
        "staged-v\n"
        "top\n"
        "\n"
        "top\n"
        "\n"
        "bottom\n"
    )
    subprocess.run(
        ["git", "add", "file.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    (functional_repo / "file.txt").write_text(
        "staged-u\n"
        "staged-v\n"
        "top\n"
        "import-a\n"
        "import-b\n"
        "import-c\n"
        "\n"
        "save-a\n"
        "save-b\n"
        "later-a\n"
        "\n"
        "top\n"
        "\n"
        "bottom\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    result = git_stage_batch(
        "discard",
        "--to",
        "saved",
        "--line",
        "5-6",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_content(functional_repo, "saved", "file.txt") == (
        "top\n"
        "save-a\n"
        "save-b\n"
        "\n"
        "top\n"
        "\n"
        "bottom\n"
    )


def test_discard_translates_deletion_position_from_staged_index(functional_repo):
    """Saved deletions use the batch baseline, not shifted index coordinates."""
    _commit_file(functional_repo, "file.txt", "a\nb\na\nb\n")
    (functional_repo / "file.txt").write_text(
        "staged-one\n"
        "staged-two\n"
        "a\n"
        "b\n"
        "a\n"
        "b\n"
    )
    subprocess.run(
        ["git", "add", "file.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    (functional_repo / "file.txt").write_text(
        "staged-one\n"
        "staged-two\n"
        "a\n"
        "a\n"
        "b\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    result = git_stage_batch(
        "discard",
        "--to",
        "saved",
        "--file",
        "file.txt",
        "--line",
        "1",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_content(functional_repo, "saved", "file.txt") == "a\na\nb\n"


def test_include_to_batch_projects_partial_staged_replacement(functional_repo):
    """Replacement removal bytes come from the persistent batch baseline."""
    _prepare_staged_replacement(functional_repo)

    git_stage_batch("start", "--no-auto-advance")
    result = git_stage_batch(
        "include",
        "--to",
        "saved",
        "--line",
        "1,3",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_content(functional_repo, "saved", "file.txt") == (
        "head\n"
        "work-one\n"
        "orig-two\n"
        "tail\n"
    )


def test_discard_to_batch_projects_partial_staged_replacement(functional_repo):
    """Discarded replacements suppress HEAD content rather than index content."""
    _prepare_staged_replacement(functional_repo)

    git_stage_batch("start", "--no-auto-advance")
    result = git_stage_batch(
        "discard",
        "--to",
        "saved",
        "--line",
        "1,3",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_content(functional_repo, "saved", "file.txt") == (
        "head\n"
        "work-one\n"
        "orig-two\n"
        "tail\n"
    )


def test_discard_translates_replacement_origin_for_older_batch(functional_repo):
    """Replacement fallback uses the persisted batch baseline coordinates."""
    original = (
        "section1\n"
        "x\n"
        "old\n"
        "y\n"
        "section2\n"
        "x\n"
        "old\n"
        "y\n"
        "end\n"
    )
    _commit_file(functional_repo, "file.txt", original)
    git_stage_batch("new", "saved")

    (functional_repo / "file.txt").write_text(
        "section2\n"
        "x\n"
        "old\n"
        "y\n"
        "end\n"
    )
    subprocess.run(
        ["git", "add", "file.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Remove first section"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    (functional_repo / "file.txt").write_text(
        "section2\n"
        "x\n"
        "NEW\n"
        "y\n"
        "end\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    result = git_stage_batch(
        "discard",
        "--to",
        "saved",
        "--line",
        "1-2",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_content(functional_repo, "saved", "file.txt") == original.replace(
        "section2\nx\nold\n",
        "section2\nx\nNEW\n",
    )


def test_include_to_batch_translates_position_from_staged_index(functional_repo):
    """Persistent includes project index-relative gaps onto the batch baseline."""
    _commit_file(functional_repo, "file.txt", "a\nb\nb\nb\nb\n")
    (functional_repo / "file.txt").write_text(
        "staged\n"
        "a\n"
        "b\n"
        "b\n"
        "b\n"
        "b\n"
    )
    subprocess.run(
        ["git", "add", "file.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    (functional_repo / "file.txt").write_text(
        "staged\n"
        "a\n"
        "unselected-before\n"
        "b\n"
        "b\n"
        "selected\n"
        "unselected-after\n"
        "b\n"
        "b\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    result = git_stage_batch(
        "include",
        "--to",
        "saved",
        "--line",
        "2",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_content(functional_repo, "saved", "file.txt") == (
        "a\n"
        "b\n"
        "b\n"
        "selected\n"
        "b\n"
        "b\n"
    )


def test_consecutive_line_includes_stage_ambiguous_middle_insertion(functional_repo):
    _prepare_ambiguous_middle_insertion(functional_repo)

    result = git_stage_batch(
        "include",
        "--line",
        "5-6",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _index_content(functional_repo, "file.txt") == (
        "top\n"
        "import-a\n"
        "import-b\n"
        "import-c\n"
        "\n"
        "save-a\n"
        "save-b\n"
        "bottom\n"
    )
    assert (functional_repo / "file.txt").read_text() == (
        "top\n"
        "import-a\n"
        "import-b\n"
        "import-c\n"
        "\n"
        "save-a\n"
        "save-b\n"
        "later-a\n"
        "\n"
        "bottom\n"
    )


def test_include_line_transient_staging_pure_addition_preserves_blank_anchor(functional_repo):
    _commit_file(
        functional_repo,
        "CONTRIBUTING.md",
        "# Contributing\n"
        "\n"
        "```bash\n"
        "uv run pytest -n auto\n"
        "```\n"
        "\n"
        "## Commit Message Guidelines\n",
    )
    (functional_repo / "CONTRIBUTING.md").write_text(
        "# Contributing\n"
        "\n"
        "```bash\n"
        "uv run pytest -n auto\n"
        "```\n"
        "\n"
        "Use the xdist form (`-n auto`) for full-suite runs.\n"
        "\n"
        "## Commit Message Guidelines\n"
    )

    git_stage_batch("start", "-U0")
    git_stage_batch("include", "--line", "1-2")

    assert _index_content(functional_repo, "CONTRIBUTING.md") == (
        "# Contributing\n"
        "\n"
        "```bash\n"
        "uv run pytest -n auto\n"
        "```\n"
        "\n"
        "Use the xdist form (`-n auto`) for full-suite runs.\n"
        "\n"
        "## Commit Message Guidelines\n"
    )
    result = subprocess.run(
        ["git", "diff", "--", "CONTRIBUTING.md"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
        text=True,
    )
    assert result.stdout == ""


def test_include_line_as_pure_addition_preserves_anchor(functional_repo):
    _commit_file(functional_repo, "file.txt", "A\nC\n")
    (functional_repo / "file.txt").write_text("A\nB\nC\n")

    git_stage_batch("start", "-U0")
    git_stage_batch("include", "--line", "1", "--as", "X")

    assert _index_content(functional_repo, "file.txt") == "A\nX\nC\n"


def test_discard_line_pure_deletion_preserves_anchor(functional_repo):
    _commit_file(functional_repo, "file.txt", "A\nB\nC\n")
    (functional_repo / "file.txt").write_text("A\nC\n")

    git_stage_batch("start", "-U0")
    git_stage_batch("discard", "--line", "1")

    assert (functional_repo / "file.txt").read_text() == "A\nB\nC\n"
    result = subprocess.run(
        ["git", "diff", "--", "file.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
        text=True,
    )
    assert result.stdout == ""


def test_discard_file_line_pure_deletion_preserves_anchor(functional_repo):
    _commit_file(
        functional_repo,
        "file.txt",
        "line1\nold-a\nline3\nline4\nline5\nold-b\nline7\n",
    )
    (functional_repo / "file.txt").write_text(
        "line1\nline3\nline4\nline5\nline7\n"
    )

    git_stage_batch("start", "-U0")
    git_stage_batch("discard", "--file", "file.txt", "--line", "1")

    assert (functional_repo / "file.txt").read_text() == (
        "line1\nold-a\nline3\nline4\nline5\nline7\n"
    )


def test_include_line_transient_staging_pure_deletion(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\nc\n")
    (functional_repo / "file.txt").write_text("a\nc\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_content(functional_repo, "file.txt") == "a\nc\n"


def test_include_line_transient_staging_handles_partial_replace_row(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    result = git_stage_batch("include", "--line", "1")

    assert result.returncode == 0
    assert _index_content(functional_repo, "file.txt") == "b\n"


def test_include_line_uses_batch_order_for_ambiguous_replace_rows(functional_repo):
    _commit_file(functional_repo, "file.txt", "same\nsame\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    result = git_stage_batch("include", "--line", "1,3")

    assert result.returncode == 0
    assert _index_content(functional_repo, "file.txt") == "A\nsame\n"


def test_include_line_uses_batch_order_for_reorder_like_replacement(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("B\nA\n")

    git_stage_batch("start")
    result = git_stage_batch("include", "--line", "1,3")

    assert result.returncode == 0
    assert _index_content(functional_repo, "file.txt") == "B\nb\n"


def test_include_line_batch_round_trip_without_intervening_tree_change(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    git_stage_batch("include", "--to", "round-trip", "--line", "1,3")
    git_stage_batch("include", "--from", "round-trip", "--file", "file.txt")

    assert _index_content(functional_repo, "file.txt") == "A\nb\n"
    assert (functional_repo / "file.txt").read_text() == "A\nB\n"


def test_include_line_transient_staging_replacement_preserves_missing_trailing_newline(functional_repo):
    _commit_file_bytes(functional_repo, "file.txt", b"a\nb")
    (functional_repo / "file.txt").write_bytes(b"A\nB")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1,3")

    assert _index_bytes(functional_repo, "file.txt") == b"A\nb"


def test_include_line_transient_staging_preserves_crlf_line_endings(functional_repo):
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    _commit_file_bytes(functional_repo, "file.txt", b"a\r\nb\r\nc\r\n")
    (functional_repo / "file.txt").write_bytes(b"a\r\nB\r\nc\r\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1-2")

    assert _index_bytes(functional_repo, "file.txt") == b"a\r\nB\r\nc\r\n"


def test_include_line_transient_staging_addition_preserves_missing_trailing_newline(functional_repo):
    _commit_file_bytes(functional_repo, "file.txt", b"base\n")
    (functional_repo / "file.txt").write_bytes(b"base\nfoo")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_bytes(functional_repo, "file.txt") == b"base\nfoo"


def test_include_line_transient_staging_preserves_missing_trailing_newline(functional_repo):
    _commit_file_bytes(functional_repo, "file.txt", b"a\nb")
    (functional_repo / "file.txt").write_bytes(b"A\nB")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_bytes(functional_repo, "file.txt") == b"b"


def test_include_line_transient_staging_preserves_unrelated_index_state_for_paired_lines(functional_repo):
    _commit_file(functional_repo, "file.txt", "x\na\nb\ny\n")

    file_path = functional_repo / "file.txt"
    file_path.write_text("X\na\nb\ny\n")
    subprocess.run(["git", "add", "file.txt"], check=True, cwd=functional_repo, capture_output=True)

    file_path.write_text("X\nA\nB\ny\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1,3")

    assert _index_content(functional_repo, "file.txt") == "X\nA\nb\ny\n"


def test_include_line_transient_staging_preserves_unrelated_index_state_for_single_line(functional_repo):
    _commit_file(functional_repo, "file.txt", "x\na\nb\ny\n")

    file_path = functional_repo / "file.txt"
    file_path.write_text("X\na\nb\ny\n")
    subprocess.run(["git", "add", "file.txt"], check=True, cwd=functional_repo, capture_output=True)

    file_path.write_text("X\nA\nB\ny\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_content(functional_repo, "file.txt") == "X\nb\ny\n"


def test_include_line_transient_staging_handles_replacement_plus_trailing_insertion(functional_repo):
    _commit_file(functional_repo, "file.txt", "keep\nold value\n")
    (functional_repo / "file.txt").write_text("keep\nworking value\nextra line\n")

    git_stage_batch("start")
    result = git_stage_batch("include", "--line", "1,2")

    assert result.returncode == 0
    assert _index_content(functional_repo, "file.txt") == "keep\nworking value\n"


def test_include_line_transient_staging_handles_move_plus_edit(functional_repo):
    _commit_file(
        functional_repo,
        "workflow.yml",
        "steps:\n"
        "  - name: Set up venv\n"
        "    run: uv venv\n"
        "\n"
        "  - name: Set up Python\n"
        "    run: uv python install 3.10\n"
        "\n"
        "  - name: Run tests\n"
        "    run: uv run pytest\n",
    )
    (functional_repo / "workflow.yml").write_text(
        "steps:\n"
        "  - name: Set up Python\n"
        "    run: uv python install 3.10\n"
        "\n"
        "  - name: Set up venv\n"
        "    run: uv venv --python 3.10\n"
        "\n"
        "  - name: Run tests\n"
        "    run: uv run pytest -n auto\n"
    )

    git_stage_batch("start", "-U0")
    git_stage_batch("show", "--file", "workflow.yml", "--page", "all")
    git_stage_batch("include", "--line", "1-6")

    assert _index_content(functional_repo, "workflow.yml") == (
        "steps:\n"
        "  - name: Set up Python\n"
        "    run: uv python install 3.10\n"
        "\n"
        "  - name: Set up venv\n"
        "    run: uv venv --python 3.10\n"
        "\n"
        "  - name: Run tests\n"
        "    run: uv run pytest\n"
    )

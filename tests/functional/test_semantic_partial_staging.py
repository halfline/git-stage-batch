"""Functional tests for semantic line-level staging."""

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


def test_semantic_partial_staging_first_replace_row(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1,3")

    assert _index_content(functional_repo, "file.txt") == "A\nb\n"


def test_semantic_partial_staging_second_replace_row(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "2,4")

    assert _index_content(functional_repo, "file.txt") == "a\nB\n"


def test_semantic_partial_staging_same_cardinality_replacement_by_position(functional_repo):
    _commit_file(functional_repo, "file.txt", "red\nblue\n")
    (functional_repo / "file.txt").write_text("circle\nsquare\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1,3")

    assert _index_content(functional_repo, "file.txt") == "circle\nblue\n"


def test_semantic_partial_staging_full_replace_selection(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1-4")

    assert _index_content(functional_repo, "file.txt") == "A\nB\n"


def test_semantic_partial_staging_pure_addition(functional_repo):
    _commit_file(functional_repo, "file.txt", "base\n")
    (functional_repo / "file.txt").write_text("base\nfoo\nbar\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_content(functional_repo, "file.txt") == "base\nfoo\n"


def test_semantic_partial_staging_pure_addition_preserves_blank_anchor(functional_repo):
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


def test_semantic_partial_staging_pure_deletion(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\nc\n")
    (functional_repo / "file.txt").write_text("a\nc\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_content(functional_repo, "file.txt") == "a\nc\n"


def test_semantic_partial_staging_falls_back_for_partial_replace_row(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    result = git_stage_batch("include", "--line", "1")

    assert result.returncode == 0
    assert _index_content(functional_repo, "file.txt") == "b\n"


def test_semantic_partial_staging_falls_back_for_ambiguous_replace_rows(functional_repo):
    _commit_file(functional_repo, "file.txt", "same\nsame\n")
    (functional_repo / "file.txt").write_text("A\nB\n")

    git_stage_batch("start")
    result = git_stage_batch("include", "--line", "1,3")

    assert result.returncode == 0
    assert _index_content(functional_repo, "file.txt") == "same\nA\n"


def test_semantic_partial_staging_falls_back_for_reorder_like_replacement(functional_repo):
    _commit_file(functional_repo, "file.txt", "a\nb\n")
    (functional_repo / "file.txt").write_text("B\nA\n")

    git_stage_batch("start")
    result = git_stage_batch("include", "--line", "1,3")

    assert result.returncode == 0
    assert _index_content(functional_repo, "file.txt") == "b\nB\n"


def test_semantic_partial_staging_replacement_preserves_missing_trailing_newline(functional_repo):
    _commit_file_bytes(functional_repo, "file.txt", b"a\nb")
    (functional_repo / "file.txt").write_bytes(b"A\nB")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1,3")

    assert _index_bytes(functional_repo, "file.txt") == b"A\nb"


def test_semantic_partial_staging_addition_preserves_missing_trailing_newline(functional_repo):
    _commit_file_bytes(functional_repo, "file.txt", b"base\n")
    (functional_repo / "file.txt").write_bytes(b"base\nfoo")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_bytes(functional_repo, "file.txt") == b"base\nfoo"


def test_semantic_partial_staging_fallback_preserves_missing_trailing_newline(functional_repo):
    _commit_file_bytes(functional_repo, "file.txt", b"a\nb")
    (functional_repo / "file.txt").write_bytes(b"A\nB")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_bytes(functional_repo, "file.txt") == b"b"


def test_semantic_partial_staging_preserves_unrelated_index_state(functional_repo):
    _commit_file(functional_repo, "file.txt", "x\na\nb\ny\n")

    file_path = functional_repo / "file.txt"
    file_path.write_text("X\na\nb\ny\n")
    subprocess.run(["git", "add", "file.txt"], check=True, cwd=functional_repo, capture_output=True)

    file_path.write_text("X\nA\nB\ny\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1,3")

    assert _index_content(functional_repo, "file.txt") == "X\nA\nb\ny\n"


def test_semantic_partial_staging_fallback_preserves_unrelated_index_state(functional_repo):
    _commit_file(functional_repo, "file.txt", "x\na\nb\ny\n")

    file_path = functional_repo / "file.txt"
    file_path.write_text("X\na\nb\ny\n")
    subprocess.run(["git", "add", "file.txt"], check=True, cwd=functional_repo, capture_output=True)

    file_path.write_text("X\nA\nB\ny\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")

    assert _index_content(functional_repo, "file.txt") == "X\nb\ny\n"


def test_semantic_partial_staging_falls_back_for_replacement_plus_trailing_insertion(functional_repo):
    _commit_file(functional_repo, "file.txt", "keep\nold value\n")
    (functional_repo / "file.txt").write_text("keep\nworking value\nextra line\n")

    git_stage_batch("start")
    result = git_stage_batch("include", "--line", "1,2")

    assert result.returncode == 0
    assert _index_content(functional_repo, "file.txt") == "keep\nworking value\n"


def test_semantic_partial_staging_falls_back_for_move_plus_edit(functional_repo):
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

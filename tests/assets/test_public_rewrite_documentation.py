"""Tests for public rewrite positioning and cache documentation."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_SUMMARY = "Fine-grained Git staging and deterministic draft-history refinement"


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_public_entry_points_describe_staging_and_rewriting() -> None:
    """Project summaries should expose both first-class product workflows."""
    readme = _read("README.md")
    project = _read("pyproject.toml")
    site = _read("mkdocs.yml")
    manual = _read("man/git-stage-batch.1.in")
    package = _read("src/git_stage_batch/__init__.py")

    assert PRODUCT_SUMMARY in readme
    assert f'description = "{PRODUCT_SUMMARY}"' in project
    assert f"site_description: {PRODUCT_SUMMARY}" in site
    assert PRODUCT_SUMMARY in manual
    assert f'"""{PRODUCT_SUMMARY}."""' in package
    assert ".BR git-stage-batch-rewrite (1)" in manual


def test_public_guides_state_the_rewrite_and_publication_boundary() -> None:
    """Guides should neither hide rewriting nor imply permission to push."""
    homepage = _read("docs/index.md")
    readme = _read("README.md")
    assistants = _read("docs/ai-assistants.md")
    examples = _read("docs/examples.md")
    combined = "\n".join((homepage, readme, assistants, examples))

    assert "Sometimes, intentionally." in homepage
    assert "reword or split commits, integrate later repairs" in homepage
    assert "git-stage-batch rewrite scan" in examples
    assert "git-stage-batch rewrite apply" in examples
    assert "git-stage-batch rewrite verify" in examples

    forbidden = (
        "Usually no.",
        "larger history-rewrite tools",
        "git-stage-batch helps you create better commits in the first place",
    )
    for phrase in forbidden:
        assert phrase not in combined


def test_rewrite_manual_states_remote_and_abort_boundaries() -> None:
    """Installed rewrite help should describe its mutation limits."""
    manual = " ".join(_read("man/git-stage-batch-rewrite.1.in").split())

    assert "Apply does not contact a remote" in manual
    assert "never performs or authorizes a push or force-push" in manual
    assert "restores the original branch only when the live tip" in manual
    assert "Foreign movement is never overwritten" in manual

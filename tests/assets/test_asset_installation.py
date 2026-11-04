"""Installed skill resources must work from an unrelated project."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from git_stage_batch.commands.install_assets import command_install_assets


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("global_install", [False, True])
@pytest.mark.parametrize(
    ("group", "assistant"),
    [("codex-skills", ".agents"), ("claude-skills", ".claude")],
)
def test_installed_helpers_and_companions_work_outside_installation(
    tmp_path, monkeypatch, global_install, group, assistant,
):
    """Install the complete dependency closure and run the documented helpers."""
    install_root = tmp_path / "installed assets"
    install_root.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: install_root))
    if not global_install:
        subprocess.run(["git", "init", str(install_root)], check=True, capture_output=True)
    monkeypatch.chdir(install_root)
    command_install_assets(
        group,
        ["publish-unpushed-commits", "decompose-and-commit-unstaged-changes"],
        global_install=global_install,
    )
    asset_root = install_root / assistant
    default_guide = asset_root / "internal/CONTRIBUTING.md"
    assert default_guide.read_bytes() == (
        PROJECT_ROOT / "assets/common/CONTRIBUTING.md"
    ).read_bytes()
    assert not (install_root / "CONTRIBUTING.md").exists()

    project = tmp_path / "another project"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    monkeypatch.chdir(project)
    helper_paths: set[Path] = set()
    references: set[Path] = set()
    for document in asset_root.rglob("*.md"):
        text = document.read_text()
        for relative in re.findall(r'\$ASSET_ROOT/([^\s`"\)]+)', text):
            target = asset_root / relative
            assert target.exists(), f"Missing companion {target} in {document}"
            references.add(target)
        for relative in re.findall(
            r'(?:python |PUBLISH_HELPER=)"\$ASSET_ROOT/([^\s"]+)"', text,
        ):
            helper_paths.add(asset_root / relative)
        for relative in re.findall(
            r'PUBLISH_HELPER="\$\{CLAUDE_SKILL_DIR\}/([^\s"]+)"', text,
        ):
            helper_paths.add(document.parent / relative)

    assert default_guide in references
    assert asset_root / "skills/refine-commit-messages/references/message-guidelines.md" in references
    assert len(helper_paths) >= 3
    for helper in sorted(helper_paths):
        result = subprocess.run(
            [sys.executable, str(helper), "--help"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "TMPDIR": "/var/tmp"},
        )
        assert "usage:" in result.stdout

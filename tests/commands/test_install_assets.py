"""Tests for bundled asset installation."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.commands.install_assets import command_install_assets
from git_stage_batch.exceptions import CommandError


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestCommandInstallAssets:
    """Tests for install-assets command."""

    def test_install_all_assets_by_default(self, temp_git_repo, capsys):
        """Installing without a group should install all bundled asset groups."""
        command_install_assets()

        claude_agent = temp_git_repo / ".claude" / "agents" / "commit-message-drafter.md"
        claude_unstaged = temp_git_repo / ".claude" / "skills" / "commit-unstaged-changes" / "SKILL.md"
        claude_staged = temp_git_repo / ".claude" / "skills" / "commit-staged-changes" / "SKILL.md"
        codex_internal_drafter = temp_git_repo / ".agents" / "internal" / "commit-message-drafter.md"
        codex_unstaged = temp_git_repo / ".agents" / "skills" / "commit-unstaged-changes" / "SKILL.md"
        codex_staged = temp_git_repo / ".agents" / "skills" / "commit-staged-changes" / "SKILL.md"
        codex_config = temp_git_repo / ".codex" / "config.toml"
        assert claude_agent.exists()
        assert claude_unstaged.exists()
        assert claude_staged.exists()
        assert codex_internal_drafter.exists()
        assert codex_unstaged.exists()
        assert codex_staged.exists()
        assert codex_config.exists()

        captured = capsys.readouterr()
        assert "Installed Claude agent 'commit-message-drafter'" in captured.err
        assert "Installed Claude skills: commit-staged-changes, commit-unstaged-changes" in captured.err
        assert "Installed Codex skills: commit-staged-changes, commit-unstaged-changes" in captured.err

    def test_install_all_claude_agents(self, temp_git_repo, capsys):
        """Installing a Claude agent group should write bundled agents."""
        command_install_assets("claude-agents")

        agent_file = temp_git_repo / ".claude" / "agents" / "commit-message-drafter.md"
        assert agent_file.exists()
        assert "name: commit-message-drafter" in agent_file.read_text(encoding="utf-8")

        captured = capsys.readouterr()
        assert "Installed Claude agent 'commit-message-drafter'" in captured.err

    def test_install_single_claude_agent(self, temp_git_repo):
        """Selecting one Claude agent should install only that agent."""
        command_install_assets("claude-agents", ["commit-message-drafter"])

        agent_dir = temp_git_repo / ".claude" / "agents"
        assert (agent_dir / "commit-message-drafter.md").exists()
        assert sorted(path.name for path in agent_dir.iterdir()) == ["commit-message-drafter.md"]

    def test_install_all_claude_skills(self, temp_git_repo, capsys):
        """Installing a Claude asset group should write bundled skills."""
        command_install_assets("claude-skills")

        agent_file = temp_git_repo / ".claude" / "agents" / "commit-message-drafter.md"
        staged_skill = temp_git_repo / ".claude" / "skills" / "commit-staged-changes" / "SKILL.md"
        unstaged_skill = temp_git_repo / ".claude" / "skills" / "commit-unstaged-changes" / "SKILL.md"
        assert agent_file.exists()
        assert staged_skill.exists()
        assert unstaged_skill.exists()
        assert "name: commit-staged-changes" in staged_skill.read_text(encoding="utf-8")
        assert "name: commit-unstaged-changes" in unstaged_skill.read_text(encoding="utf-8")

        captured = capsys.readouterr()
        assert "Installed Claude skills: commit-staged-changes, commit-unstaged-changes" in captured.err

    def test_install_single_skill(self, temp_git_repo):
        """Selecting one skill should install only that skill."""
        command_install_assets("claude-skills", ["commit-unstaged-changes"])

        agent_dir = temp_git_repo / ".claude" / "agents"
        skill_dir = temp_git_repo / ".claude" / "skills"
        assert (agent_dir / "commit-message-drafter.md").exists()
        assert (skill_dir / "commit-unstaged-changes" / "SKILL.md").exists()
        assert sorted(path.name for path in skill_dir.iterdir()) == ["commit-unstaged-changes"]

    def test_install_all_codex_skills(self, temp_git_repo, capsys):
        """Installing a Codex asset group should write bundled skills."""
        command_install_assets("codex-skills")

        internal_drafter = temp_git_repo / ".agents" / "internal" / "commit-message-drafter.md"
        staged_skill = temp_git_repo / ".agents" / "skills" / "commit-staged-changes" / "SKILL.md"
        unstaged_skill = temp_git_repo / ".agents" / "skills" / "commit-unstaged-changes" / "SKILL.md"
        codex_config = temp_git_repo / ".codex" / "config.toml"
        staged_openai_yaml = (
            temp_git_repo
            / ".agents"
            / "skills"
            / "commit-staged-changes"
            / "agents"
            / "openai.yaml"
        )
        unstaged_openai_yaml = (
            temp_git_repo
            / ".agents"
            / "skills"
            / "commit-unstaged-changes"
            / "agents"
            / "openai.yaml"
        )
        assert internal_drafter.exists()
        assert staged_skill.exists()
        assert unstaged_skill.exists()
        assert codex_config.exists()
        assert staged_openai_yaml.exists()
        assert unstaged_openai_yaml.exists()
        assert "# Commit Message Drafter" in internal_drafter.read_text(encoding="utf-8")
        assert "name: commit-staged-changes" in staged_skill.read_text(encoding="utf-8")
        assert "name: commit-unstaged-changes" in unstaged_skill.read_text(encoding="utf-8")
        assert 'sandbox_mode = "workspace-write"' in codex_config.read_text(encoding="utf-8")

        captured = capsys.readouterr()
        assert "Installed Codex skills: commit-staged-changes, commit-unstaged-changes" in captured.err

    def test_install_single_codex_skill(self, temp_git_repo):
        """Selecting one Codex skill should install only that skill."""
        command_install_assets("codex-skills", ["commit-unstaged-changes"])

        skill_dir = temp_git_repo / ".agents" / "skills"
        assert (temp_git_repo / ".codex" / "config.toml").exists()
        assert (temp_git_repo / ".agents" / "internal" / "commit-message-drafter.md").exists()
        assert (skill_dir / "commit-unstaged-changes" / "SKILL.md").exists()
        assert sorted(path.name for path in skill_dir.iterdir()) == ["commit-unstaged-changes"]

    def test_install_filtered_assets_across_all_groups(self, temp_git_repo, capsys):
        """Filtering without a group should install matches from every asset group."""
        command_install_assets(filters=["commit-*"])

        assert (temp_git_repo / ".claude" / "agents" / "commit-message-drafter.md").exists()
        assert (temp_git_repo / ".agents" / "internal" / "commit-message-drafter.md").exists()
        assert (temp_git_repo / ".claude" / "skills" / "commit-staged-changes" / "SKILL.md").exists()
        assert (temp_git_repo / ".claude" / "skills" / "commit-unstaged-changes" / "SKILL.md").exists()
        assert (temp_git_repo / ".agents" / "skills" / "commit-staged-changes" / "SKILL.md").exists()
        assert (temp_git_repo / ".agents" / "skills" / "commit-unstaged-changes" / "SKILL.md").exists()
        assert (temp_git_repo / ".codex" / "config.toml").exists()

        captured = capsys.readouterr()
        assert "Installed Claude agent 'commit-message-drafter'" in captured.err
        assert "Installed Claude skills: commit-staged-changes, commit-unstaged-changes" in captured.err
        assert "Installed Codex skills: commit-staged-changes, commit-unstaged-changes" in captured.err

    def test_install_from_subdirectory_uses_repo_root(self, temp_git_repo, monkeypatch):
        """Assets should install relative to the repository root."""
        nested = temp_git_repo / "nested" / "deeper"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        command_install_assets("claude-skills", ["commit-unstaged-changes"])

        assert (temp_git_repo / ".claude" / "skills" / "commit-unstaged-changes" / "SKILL.md").exists()

    def test_unmatched_filter_raises_error(self, temp_git_repo):
        """Unmatched filters in one group should raise a command error."""
        with pytest.raises(CommandError, match="No bundled assets in 'claude-skills' matched: missing-skill"):
            command_install_assets("claude-skills", ["missing-skill"])

    def test_codex_internal_drafter_is_not_installable_by_name(self, temp_git_repo):
        """The internal Codex drafter brief should not be selectable as a skill."""
        with pytest.raises(
            CommandError,
            match="No bundled assets in 'codex-skills' matched: commit-message-drafter",
        ):
            command_install_assets("codex-skills", ["commit-message-drafter"])

    def test_unmatched_filter_across_all_groups_raises_error(self, temp_git_repo):
        """Unmatched filters without a group should raise a generic command error."""
        with pytest.raises(CommandError, match="No bundled assets in 'all asset groups' matched: missing-skill"):
            command_install_assets(filters=["missing-skill"])

    def test_existing_skill_requires_force(self, temp_git_repo):
        """Existing installed skills should not be overwritten by default."""
        skill_file = temp_git_repo / ".claude" / "skills" / "commit-unstaged-changes" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("local override\n", encoding="utf-8")

        with pytest.raises(
            CommandError,
            match="Refusing to overwrite existing claude skill 'commit-unstaged-changes'",
        ):
            command_install_assets("claude-skills", ["commit-unstaged-changes"])

        assert skill_file.read_text(encoding="utf-8") == "local override\n"

    def test_existing_required_agent_blocks_claude_skill_install(self, temp_git_repo):
        """Installing a Claude skill should refuse to overwrite its required agent."""
        agent_file = temp_git_repo / ".claude" / "agents" / "commit-message-drafter.md"
        agent_file.parent.mkdir(parents=True)
        agent_file.write_text("local override\n", encoding="utf-8")

        with pytest.raises(
            CommandError,
            match="Refusing to overwrite existing claude agent '\\.claude/agents/commit-message-drafter.md'",
        ):
            command_install_assets("claude-skills", ["commit-unstaged-changes"])

        assert agent_file.read_text(encoding="utf-8") == "local override\n"

    def test_existing_claude_agent_requires_force(self, temp_git_repo):
        """Existing installed Claude agents should not be overwritten by default."""
        agent_file = temp_git_repo / ".claude" / "agents" / "commit-message-drafter.md"
        agent_file.parent.mkdir(parents=True)
        agent_file.write_text("local override\n", encoding="utf-8")

        with pytest.raises(
            CommandError,
            match="Refusing to overwrite existing claude agent 'commit-message-drafter'",
        ):
            command_install_assets("claude-agents", ["commit-message-drafter"])

        assert agent_file.read_text(encoding="utf-8") == "local override\n"

    def test_force_overwrites_existing_skill(self, temp_git_repo):
        """Force mode should replace an existing installed skill."""
        skill_file = temp_git_repo / ".claude" / "skills" / "commit-unstaged-changes" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("local override\n", encoding="utf-8")

        command_install_assets("claude-skills", ["commit-unstaged-changes"], force=True)

        content = skill_file.read_text(encoding="utf-8")
        assert "name: commit-unstaged-changes" in content
        assert "local override" not in content

    def test_force_overwrites_required_agent_during_claude_skill_install(self, temp_git_repo):
        """Force mode should replace a required Claude agent during skill install."""
        agent_file = temp_git_repo / ".claude" / "agents" / "commit-message-drafter.md"
        agent_file.parent.mkdir(parents=True)
        agent_file.write_text("local override\n", encoding="utf-8")

        command_install_assets("claude-skills", ["commit-unstaged-changes"], force=True)

        content = agent_file.read_text(encoding="utf-8")
        assert "name: commit-message-drafter" in content
        assert "local override" not in content

    def test_force_overwrites_existing_claude_agent(self, temp_git_repo):
        """Force mode should replace an existing installed Claude agent."""
        agent_file = temp_git_repo / ".claude" / "agents" / "commit-message-drafter.md"
        agent_file.parent.mkdir(parents=True)
        agent_file.write_text("local override\n", encoding="utf-8")

        command_install_assets("claude-agents", ["commit-message-drafter"], force=True)

        content = agent_file.read_text(encoding="utf-8")
        assert "name: commit-message-drafter" in content
        assert "local override" not in content

    def test_existing_codex_skill_requires_force(self, temp_git_repo):
        """Existing installed Codex skills should not be overwritten by default."""
        skill_file = temp_git_repo / ".agents" / "skills" / "commit-unstaged-changes" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("local override\n", encoding="utf-8")

        with pytest.raises(
            CommandError,
            match="Refusing to overwrite existing codex skill 'commit-unstaged-changes'",
        ):
            command_install_assets("codex-skills", ["commit-unstaged-changes"])

        assert skill_file.read_text(encoding="utf-8") == "local override\n"

    def test_existing_codex_config_requires_force(self, temp_git_repo):
        """Existing repo-local Codex config should not be overwritten by default."""
        config_file = temp_git_repo / ".codex" / "config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("sandbox_mode = \"read-only\"\n", encoding="utf-8")

        with pytest.raises(
            CommandError,
            match=r"Refusing to overwrite existing codex config '\.codex/config.toml'",
        ):
            command_install_assets("codex-skills", ["commit-unstaged-changes"])

        assert config_file.read_text(encoding="utf-8") == "sandbox_mode = \"read-only\"\n"

    def test_file_named_dot_codex_raises_command_error(self, temp_git_repo):
        """A file blocking the Codex config directory should not cause a traceback."""
        dot_codex = temp_git_repo / ".codex"
        dot_codex.write_text("", encoding="utf-8")

        with pytest.raises(
            CommandError,
            match=r"Cannot install bundled assets because '\.codex' is not a directory",
        ):
            command_install_assets("codex-skills", ["commit-unstaged-changes"])

    def test_force_overwrites_existing_codex_skill(self, temp_git_repo):
        """Force mode should replace an existing installed Codex skill."""
        skill_file = temp_git_repo / ".agents" / "skills" / "commit-unstaged-changes" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("local override\n", encoding="utf-8")

        command_install_assets("codex-skills", ["commit-unstaged-changes"], force=True)

        content = skill_file.read_text(encoding="utf-8")
        assert "name: commit-unstaged-changes" in content
        assert "local override" not in content

    def test_force_overwrites_existing_codex_config(self, temp_git_repo):
        """Force mode should replace an existing repo-local Codex config."""
        config_file = temp_git_repo / ".codex" / "config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("sandbox_mode = \"read-only\"\n", encoding="utf-8")

        command_install_assets("codex-skills", ["commit-unstaged-changes"], force=True)

        content = config_file.read_text(encoding="utf-8")
        assert 'sandbox_mode = "workspace-write"' in content
        assert 'sandbox_mode = "read-only"' not in content

    def test_outside_repo_raises_error(self, tmp_path, monkeypatch):
        """Installing assets outside a repository should fail."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_install_assets()

"""Tests for git index and tree plumbing helpers."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.utils import git_index as git_index_module
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_index import (
    git_add_paths,
    git_add_paths_from_stdin,
    git_commit_tree,
    git_read_tree,
    git_restore_intent_to_add_path,
    git_restore_intent_to_add_paths,
    git_update_index,
    git_update_index_entries,
    GitIndexEntryUpdate,
    git_write_tree,
    temp_git_index,
)
from git_stage_batch.utils.git_object_io import create_git_blob


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    return repo


class TestGitIndexPlumbing:
    """Tests for temporary index plumbing helpers."""

    def test_temp_index_builds_commit_without_touching_main_index(self, temp_git_repo):
        """Test creating a commit from a temporary index."""
        blob_sha = create_git_blob([b"from temp index\n"])

        with temp_git_index() as env:
            temp_index_path = Path(env["GIT_INDEX_FILE"])
            git_read_tree("HEAD", env=env)
            git_update_index(
                mode="100644",
                blob_sha=blob_sha,
                file_path="nested/file.txt",
                env=env,
            )
            tree_sha = git_write_tree(env=env)

        assert not temp_index_path.exists()

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Temporary index commit",
        )
        result = run_git_command(["show", f"{commit_sha}:nested/file.txt"])

        assert result.stdout == "from temp index\n"
        assert run_git_command(["status", "--short"]).stdout == ""

    def test_update_index_cacheinfo_handles_comma_paths(self, temp_git_repo):
        """Test that cacheinfo paths are passed as separate arguments."""
        blob_sha = create_git_blob([b"comma path\n"])
        file_path = "dir/name,with,commas.txt"

        with temp_git_index() as env:
            git_read_tree("HEAD", env=env)
            git_update_index(
                mode="100644",
                blob_sha=blob_sha,
                file_path=file_path,
                env=env,
            )
            tree_sha = git_write_tree(env=env)

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Comma path commit",
        )
        result = run_git_command(["show", f"{commit_sha}:{file_path}"])

        assert result.stdout == "comma path\n"

    def test_update_index_force_remove_deletes_index_entry(self, temp_git_repo):
        """Test force-removing a path from a temporary index."""
        with temp_git_index() as env:
            git_read_tree("HEAD", env=env)
            git_update_index(file_path="README.md", force_remove=True, env=env)
            tree_sha = git_write_tree(env=env)

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Remove file from temp index",
        )
        result = run_git_command(["show", f"{commit_sha}:README.md"], check=False)

        assert result.returncode != 0
        assert run_git_command(["status", "--short"]).stdout == ""

    def test_restore_intent_to_add_preserves_saved_index_mode(self, temp_git_repo):
        """Newer worktree metadata must not replace the checkpointed index mode."""
        run_git_command(["config", "core.fileMode", "false"])
        target = temp_git_repo / "intent.txt"
        target.write_text("worktree content\n")
        target.chmod(0o755)
        empty_blob = create_git_blob([b""])
        git_update_index(
            file_path="intent.txt",
            mode="100644",
            blob_sha=empty_blob,
        )

        git_restore_intent_to_add_path("intent.txt")

        stage_entry = run_git_command(
            ["ls-files", "--stage", "--", "intent.txt"]
        ).stdout
        debug_entry = run_git_command(
            ["ls-files", "--debug", "--", "intent.txt"]
        ).stdout
        assert stage_entry.startswith(f"100644 {empty_blob} 0\t")
        assert "flags: 20004000" in debug_entry
        assert target.read_text() == "worktree content\n"
        assert target.stat().st_mode & 0o111

    def test_restore_symlink_intent_ignores_disabled_symlink_config(
        self,
        temp_git_repo,
    ):
        """Temporary symlinks must retain their saved index mode."""
        run_git_command(["config", "core.symlinks", "false"])
        empty_blob = create_git_blob([b""])
        git_update_index(
            file_path="link",
            mode="120000",
            blob_sha=empty_blob,
        )

        git_restore_intent_to_add_path("link")

        stage_entry = run_git_command(
            ["ls-files", "--stage", "--", "link"]
        ).stdout
        debug_entry = run_git_command(
            ["ls-files", "--debug", "--", "link"]
        ).stdout
        assert stage_entry.startswith(f"120000 {empty_blob} 0\t")
        assert "flags: 20004000" in debug_entry

    def test_restore_intent_outside_sparse_checkout_cone(self, temp_git_repo):
        """Recovery paths must not be silently skipped by sparse checkout."""
        run_git_command(["sparse-checkout", "init", "--cone"])
        run_git_command(["sparse-checkout", "set", "included"])
        empty_blob = create_git_blob([b""])
        git_update_index(
            file_path="outside/new.txt",
            mode="100644",
            blob_sha=empty_blob,
        )

        git_restore_intent_to_add_path("outside/new.txt")

        stage_entry = run_git_command(
            ["ls-files", "--stage", "--", "outside/new.txt"]
        ).stdout
        debug_entry = run_git_command(
            ["ls-files", "--debug", "--", "outside/new.txt"]
        ).stdout
        assert stage_entry.startswith(f"100644 {empty_blob} 0\t")
        assert "flags: 20004000" in debug_entry

    def test_restore_intent_to_add_paths_batches_missing_worktree_entries(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """Several absent paths should be published by one alternate-worktree add."""
        empty_blob = create_git_blob([b""])
        for file_path, mode in (("plain.txt", "100644"), ("tool.sh", "100755")):
            git_update_index(
                file_path=file_path,
                mode=mode,
                blob_sha=empty_blob,
            )
        real_run_git_command = git_index_module.run_git_command
        intent_add_calls = 0

        def count_intent_publications(arguments, *args, **kwargs):
            nonlocal intent_add_calls
            if "add" in arguments and "-N" in arguments:
                intent_add_calls += 1
            return real_run_git_command(arguments, *args, **kwargs)

        monkeypatch.setattr(
            git_index_module,
            "run_git_command",
            count_intent_publications,
        )

        git_restore_intent_to_add_paths(["plain.txt", "tool.sh"])

        assert intent_add_calls == 1
        stage_entries = run_git_command(
            ["ls-files", "--stage", "--", "plain.txt", "tool.sh"]
        ).stdout
        debug_entries = run_git_command(
            ["ls-files", "--debug", "--", "plain.txt", "tool.sh"]
        ).stdout
        assert f"100644 {empty_blob} 0\tplain.txt" in stage_entries
        assert f"100755 {empty_blob} 0\ttool.sh" in stage_entries
        assert debug_entries.count("flags: 20004000") == 2

    def test_restore_gitlink_intent_ignores_caller_git_environment(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """Placeholder Git commands must not inherit caller index or config."""
        git_update_index(
            file_path="nested",
            mode="160000",
            blob_sha="1" * 40,
        )
        config_directory = temp_git_repo.parent / "xdg-config" / "git"
        config_directory.mkdir(parents=True)
        (config_directory / "config").write_text(
            "[commit]\n"
            "\tgpgSign = true\n"
            "[gpg]\n"
            "\tprogram = false\n"
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_directory.parent))
        monkeypatch.setenv(
            "GIT_INDEX_FILE",
            str(temp_git_repo / ".git" / "index"),
        )

        git_restore_intent_to_add_path("nested")

        stage_entry = run_git_command(
            ["ls-files", "--stage", "--", "nested"]
        ).stdout
        debug_entry = run_git_command(
            ["ls-files", "--debug", "--", "nested"]
        ).stdout
        assert stage_entry.startswith("160000 ")
        assert "flags: 20004000" in debug_entry

    def test_restore_intent_rolls_back_index_when_publication_fails(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A failed alternate-worktree add must not remove the saved entry."""
        empty_blob = create_git_blob([b""])
        git_update_index(
            file_path="intent.txt",
            mode="100644",
            blob_sha=empty_blob,
        )
        original_entry = run_git_command(
            ["ls-files", "--stage", "--", "intent.txt"]
        ).stdout
        real_run_git_command = git_index_module.run_git_command

        def fail_intent_publication(arguments, *args, **kwargs):
            if "add" in arguments and "-N" in arguments:
                raise subprocess.CalledProcessError(1, ["git", *arguments])
            return real_run_git_command(arguments, *args, **kwargs)

        monkeypatch.setattr(
            git_index_module,
            "run_git_command",
            fail_intent_publication,
        )

        with pytest.raises(subprocess.CalledProcessError):
            git_restore_intent_to_add_path("intent.txt")

        assert run_git_command(
            ["ls-files", "--stage", "--", "intent.txt"]
        ).stdout == original_entry

    def test_add_paths_from_stdin_preserves_nul_safe_names(self, temp_git_repo):
        """Bulk intent-to-add should support Unicode and newline path names."""
        paths = ["unicodé.txt", "line\nbreak.txt", ":(glob)*.txt"]
        for path in paths:
            (temp_git_repo / path).write_text(f"{path}\n")

        git_add_paths_from_stdin(paths, intent_to_add=True)

        result = run_git_command(
            ["ls-files", "-z", "--", *paths],
            text_output=False,
        )
        assert set(result.stdout.rstrip(b"\0").split(b"\0")) == {
            path.encode("utf-8") for path in paths
        }
        assert run_git_command(["diff", "--cached", "--name-only"]).stdout == ""

    def test_add_paths_treats_pathspec_metacharacters_literally(self, temp_git_repo):
        """An exact filename must not expand into other worktree paths."""
        (temp_git_repo / "*").write_text("literal star\n")
        (temp_git_repo / "other.txt").write_text("other\n")

        git_add_paths(["*"])

        result = run_git_command(
            ["diff", "--cached", "--name-only", "-z"],
            text_output=False,
            requires_index_lock=False,
        )
        assert result.stdout.split(b"\0") == [b"*", b""]

    def test_batched_force_remove_uses_repository_object_width(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Index-info removal should use a SHA-256-width null object ID."""
        repo = tmp_path / "sha256-index"
        subprocess.run(
            ["git", "init", "--object-format=sha256", str(repo)],
            check=True,
            capture_output=True,
        )
        monkeypatch.chdir(repo)
        (repo / "tracked.txt").write_text("tracked\n")
        subprocess.run(["git", "add", "tracked.txt"], check=True, cwd=repo)

        git_update_index_entries(
            [GitIndexEntryUpdate(file_path="tracked.txt", force_remove=True)]
        )

        assert run_git_command(["ls-files", "tracked.txt"]).stdout == ""

    def test_update_index_rejects_ambiguous_modes(self, temp_git_repo):
        """Test that update-index helper modes are explicit."""
        with pytest.raises(ValueError, match="mode and blob_sha are required"):
            git_update_index(file_path="README.md")

        with pytest.raises(ValueError, match="cannot be used with force_remove"):
            git_update_index(
                file_path="README.md",
                mode="100644",
                blob_sha="0" * 40,
                force_remove=True,
            )

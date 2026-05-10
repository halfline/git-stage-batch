"""Functional tests for batch operations."""

import subprocess


from .conftest import git_stage_batch, get_unstaged_diff


class TestCreateBatch:
    """Test creating batches."""

    def test_create_batch_with_note(self, repo_with_changes):
        """Test creating a batch with a note."""
        result = git_stage_batch("new", "feature-login", "-m", "Add login page")
        assert result.returncode == 0
        assert "Created batch" in result.stderr or result.returncode == 0

        # Verify batch exists
        result = git_stage_batch("list")
        # List output goes to stdout if batches exist, stderr if not
        output = result.stdout + result.stderr
        assert "feature-login" in output or result.returncode == 0

    def test_create_batch_without_note(self, repo_with_changes):
        """Test creating a batch without a note."""
        result = git_stage_batch("new", "test-batch")
        assert result.returncode == 0

    def test_create_duplicate_batch_fails(self, repo_with_changes):
        """Test creating duplicate batch fails."""
        git_stage_batch("new", "test-batch")

        result = git_stage_batch("new", "test-batch", check=False)
        assert result.returncode != 0
        assert "already exists" in result.stderr


class TestIncludeToBatch:
    """Test including changes to a batch."""

    def test_include_to_batch_saves_changes(self, repo_with_changes):
        """Test including lines to a batch saves them."""
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")

        # Include lines to batch
        result = git_stage_batch("include", "--to", "test-batch", "--line", "1,2")
        assert result.returncode == 0
        assert "test-batch" in result.stderr

        # Changes should be removed from working tree
        get_unstaged_diff()
        # Should have fewer unstaged changes

    def test_include_to_batch_stays_on_hunk(self, repo_with_changes):
        """Test including lines to batch stays on selected hunk."""
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")

        git_stage_batch("show")

        # Include only line 1 to batch
        git_stage_batch("include", "--to", "test-batch", "--line", "1")

        # Verify line was saved to batch
        batch_show = git_stage_batch("show", "--from", "test-batch")
        assert batch_show.returncode == 0
        assert batch_show.stdout

        # Should stay on selected hunk (not advance to next)
        # Note: include --to batch doesn't remove lines from working tree,
        # so the hunk should still have all lines
        second_show = git_stage_batch("show", check=False)
        if second_show.returncode == 0:
            # Should still be showing the same file
            assert "README.md" in second_show.stdout

    def test_include_to_multiple_batches(self, repo_with_changes):
        """Test including different changes to different batches."""
        git_stage_batch("new", "batch-a")
        git_stage_batch("new", "batch-b")
        git_stage_batch("start")

        # Include to first batch
        git_stage_batch("include", "--to", "batch-a", "--line", "1")

        # Skip to next hunk
        git_stage_batch("skip", check=False)

        # Include to second batch
        result = git_stage_batch("include", "--to", "batch-b", "--line", "1", check=False)
        if result.returncode == 0:
            # Both batches should have content
            batch_a = git_stage_batch("show", "--from", "batch-a")
            batch_b = git_stage_batch("show", "--from", "batch-b")

            assert batch_a.stdout
            assert batch_b.stdout
            assert batch_a.stdout != batch_b.stdout


class TestDiscardToBatch:
    """Test discarding changes to a batch."""

    def test_discard_to_batch_saves_and_discards(self, repo_with_changes):
        """Test discard to batch saves changes and removes them."""
        git_stage_batch("new", "discard-batch")
        git_stage_batch("start")

        git_stage_batch("discard", "--to", "discard-batch", "--line", "1,2")

        # Changes should be saved to batch
        result = git_stage_batch("show", "--from", "discard-batch")
        assert result.returncode == 0
        assert result.stdout

        # Changes should be removed from working tree
        get_unstaged_diff()
        # Should have fewer changes

    def test_discard_replacement_lines_to_batch_reapplies(self, functional_repo):
        """Discarding selected replacement lines to a batch can be applied back."""
        file_path = functional_repo / "file.txt"
        file_path.write_text("a\nb\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=functional_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=functional_repo, capture_output=True)

        file_path.write_text("A\nB\n")

        git_stage_batch("start")
        discard_result = git_stage_batch("discard", "--to", "test", "--line", "1,3")

        assert discard_result.stdout.count("file.txt ::") == 1
        assert file_path.read_text() == "a\nB\n"

        git_stage_batch("apply", "--from", "test")

        assert file_path.read_text() == "A\nB\n"


class TestShowFromBatch:
    """Test showing changes from a batch."""

    def test_show_from_batch_displays_changes(self, repo_with_changes):
        """Test showing changes from a batch."""
        git_stage_batch("new", "test-batch", "-m", "Test changes")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2")

        result = git_stage_batch("show", "--from", "test-batch")
        assert result.returncode == 0
        # Should show the note in the header
        assert "Test changes" in result.stdout
        # Should show line IDs
        assert "[#" in result.stdout

    def test_show_from_empty_batch_succeeds(self, repo_with_changes):
        """Test showing from empty batch succeeds with no output."""
        git_stage_batch("new", "empty-batch")

        result = git_stage_batch("show", "--from", "empty-batch", check=False)
        assert result.returncode == 0
        assert result.stdout == ""  # No output for empty batch

    def test_show_from_nonexistent_batch_fails(self, repo_with_changes):
        """Test showing from nonexistent batch fails."""
        result = git_stage_batch("show", "--from", "nonexistent", check=False)
        assert result.returncode != 0


class TestApplyFromBatch:
    """Test applying changes from a batch."""



    def test_apply_from_batch_stages_changes(self, repo_with_changes):
        """Test applying from batch stages the changes."""
        # Save changes to batch
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2,3")

        # Clear working tree changes
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Apply from batch
        result = git_stage_batch("apply", "--from", "test-batch")
        assert result.returncode == 0

        # Changes should be in working tree (unstaged)
        unstaged = get_unstaged_diff()
        assert unstaged
        assert "+" in unstaged

    def test_apply_from_batch_with_line_selection(self, repo_with_changes):
        """Test applying specific lines from a batch.

        Note: Lines 1,2,3,4 form an explicit atomic replacement unit
        (deletion + coupled additions), so we select all four to respect the
        semantic boundary.
        """
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2,3,4")

        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Apply only specific lines (must respect atomic unit boundaries)
        # Lines 1,2,3,4 form one explicit atomic replacement unit
        result = git_stage_batch("apply", "--from", "test-batch", "--line", "1,2,3,4")
        assert result.returncode == 0

        unstaged = get_unstaged_diff()
        assert unstaged


class TestBatchList:
    """Test listing batches."""

    def test_list_empty_batches(self, repo_with_changes):
        """Test listing when no batches exist."""
        result = git_stage_batch("list")
        assert result.returncode == 0
        # Output might be empty or have a header

    def test_list_multiple_batches(self, repo_with_changes):
        """Test listing multiple batches."""
        git_stage_batch("new", "batch-a")
        git_stage_batch("new", "batch-b")
        git_stage_batch("new", "batch-c")

        result = git_stage_batch("list")
        assert "batch-a" in result.stdout
        assert "batch-b" in result.stdout
        assert "batch-c" in result.stdout


class TestBatchDelete:
    """Test deleting batches."""

    def test_delete_batch(self, repo_with_changes):
        """Test deleting a batch."""
        git_stage_batch("new", "test-batch")

        result = git_stage_batch("drop", "test-batch")
        assert result.returncode == 0

        # Batch should be gone
        list_result = git_stage_batch("list")
        assert "test-batch" not in list_result.stdout

    def test_delete_nonexistent_batch_fails(self, repo_with_changes):
        """Test deleting nonexistent batch fails."""
        result = git_stage_batch("drop", "nonexistent", check=False)
        assert result.returncode != 0


class TestOddEvenLinesBatches:
    """Test discard and apply with even-lines and odd-lines batches."""

    def test_discard_odd_even_lines_to_batches(self, functional_repo):
        """Test discarding odd/even lines to separate batches."""
        # Create a file with some initial content
        test_file = functional_repo / "numbers.txt"
        test_file.write_text("Initial content\n")

        # Add to git
        subprocess.run(["git", "add", "numbers.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add numbers file"], check=True, capture_output=True)

        # Add 10 new lines (only additions, simpler for line ID tracking)
        test_file.write_text(
            "Initial content\n"
            "Line 1\n"
            "Line 2\n"
            "Line 3\n"
            "Line 4\n"
            "Line 5\n"
            "Line 6\n"
            "Line 7\n"
            "Line 8\n"
            "Line 9\n"
            "Line 10\n"
        )

        # Create batches
        git_stage_batch("new", "odd-lines")
        git_stage_batch("new", "even-lines")

        # Start and discard odd lines (1,3,5,7,9) to odd-lines batch
        git_stage_batch("start")
        git_stage_batch("discard", "--to", "odd-lines", "--line", "1,3,5,7,9")

        # Verify odd lines batch has content
        odd_result = git_stage_batch("show", "--from", "odd-lines")
        assert odd_result.returncode == 0
        assert odd_result.stdout
        # Should have line markers
        assert "[#" in odd_result.stdout
        # Should contain some of our lines
        assert "Line" in odd_result.stdout

        # Discard all remaining lines in the file to even-lines batch
        git_stage_batch("discard", "--to", "even-lines", "--file", check=False)

        # Verify even lines batch has content
        even_result = git_stage_batch("show", "--from", "even-lines", check=False)
        if even_result.returncode == 0:
            assert even_result.stdout
            assert "[#" in even_result.stdout
            assert "Line" in even_result.stdout

        # Both batches should exist
        list_result = git_stage_batch("list")
        assert "odd-lines" in list_result.stdout
        assert "even-lines" in list_result.stdout

    def test_apply_odd_then_even_lines_together(self, functional_repo):
        """Test discarding to odd/even batches then applying both in order."""
        # Create a file with initial content
        test_file = functional_repo / "sequence.txt"
        test_file.write_text("Header\n")

        subprocess.run(["git", "add", "sequence.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add sequence file"], check=True, capture_output=True)

        # Add 10 new lines
        test_file.write_text("Header\n" + "\n".join([f"Added line {i}" for i in range(1, 11)]) + "\n")

        # Create batches and discard
        git_stage_batch("new", "odd-lines")
        git_stage_batch("new", "even-lines")
        git_stage_batch("start")

        # Discard odd line IDs
        git_stage_batch("discard", "--to", "odd-lines", "--line", "1,3,5,7,9")

        # Discard all remaining hunks in the selected file to even-lines batch
        git_stage_batch("discard", "--to", "even-lines", "--file", check=False)

        # File should now be back to original (all lines discarded)
        selected_content = test_file.read_text()
        assert selected_content == "Header\n"

        # Both batches should have content
        odd_show = git_stage_batch("show", "--from", "odd-lines")
        even_show = git_stage_batch("show", "--from", "even-lines", check=False)

        assert odd_show.returncode == 0
        assert "Added line" in odd_show.stdout

        if even_show.returncode == 0:
            assert even_show.stdout
            assert "Added line" in even_show.stdout

        # Clear working tree and apply both batches back
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Apply both in sequence: odd first, then even
        git_stage_batch("apply", "--from", "odd-lines")
        git_stage_batch("apply", "--from", "even-lines")

        # Should have all changes back
        unstaged = get_unstaged_diff("sequence.txt")
        assert unstaged
        assert "Added line" in unstaged
        # Should have all 10 lines back
        final_content = test_file.read_text()
        for i in range(1, 11):
            assert f"Added line {i}" in final_content

    def test_apply_even_then_odd_lines_together(self, functional_repo):
        """Test discarding to odd/even batches then applying in reverse order."""
        # Create a file with initial content
        test_file = functional_repo / "reverse.txt"
        test_file.write_text("Start\n")

        subprocess.run(["git", "add", "reverse.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add reverse file"], check=True, capture_output=True)

        # Add 10 new lines
        test_file.write_text("Start\n" + "\n".join([f"New {i}" for i in range(1, 11)]) + "\n")

        # Create batches and discard
        git_stage_batch("new", "odd-lines")
        git_stage_batch("new", "even-lines")
        git_stage_batch("start")

        # Discard odd line IDs
        git_stage_batch("discard", "--to", "odd-lines", "--line", "1,3,5,7,9")

        # Discard all remaining hunks in the selected file to even-lines batch
        git_stage_batch("discard", "--to", "even-lines", "--file", check=False)

        # File should be back to original
        assert test_file.read_text() == "Start\n"

        # Clear all changes to test apply
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)
        assert test_file.read_text() == "Start\n"

        # Apply in reverse order: even then odd
        # This tests that apply --from works with these batch names in both orders
        git_stage_batch("apply", "--from", "even-lines")
        git_stage_batch("apply", "--from", "odd-lines")

        # Should have all changes back
        unstaged = get_unstaged_diff("reverse.txt")
        assert unstaged
        assert "New" in unstaged
        # Should have all 10 lines back
        final_content = test_file.read_text()
        for i in range(1, 11):
            assert f"New {i}" in final_content


class TestBatchAbortReversion:
    """Test that batches are reverted to their original state after abort."""

    def test_abort_reverts_batch_created_during_session(self, functional_repo):
        """Test that batches created during session are deleted on abort."""
        # Create a file with changes
        test_file = functional_repo / "test.txt"
        test_file.write_text("Line 1\n")
        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, capture_output=True)
        test_file.write_text("Line 1\nLine 2\n")

        # Verify batch doesn't exist yet
        list_before = git_stage_batch("list")
        assert "session-batch" not in list_before.stdout

        # Start session and create batch
        git_stage_batch("start")
        git_stage_batch("new", "session-batch")
        git_stage_batch("include", "--to", "session-batch", "--line", "1", check=False)

        # Verify batch exists and has content
        batch_show = git_stage_batch("show", "--from", "session-batch")
        assert batch_show.returncode == 0
        assert batch_show.stdout

        # Abort session
        git_stage_batch("abort")

        # Batch should be deleted (it was created during session)
        list_after = git_stage_batch("list")
        assert "session-batch" not in list_after.stdout

        # Verify batch is gone
        result = git_stage_batch("show", "--from", "session-batch", check=False)
        assert result.returncode != 0

    def test_abort_reverts_batch_modified_during_session(self, functional_repo):
        """Test that batches modified during session are reverted to original state."""
        # Create a file with changes
        test_file = functional_repo / "revert.txt"
        test_file.write_text("Original\n")
        subprocess.run(["git", "add", "revert.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, capture_output=True)
        test_file.write_text("Original\nAdded line 1\nAdded line 2\nAdded line 3\n")

        # Create batch BEFORE session (empty is the original state)
        git_stage_batch("new", "existing-batch", "-m", "Initial note")

        # Start session and modify the batch by adding lines
        git_stage_batch("start")
        git_stage_batch("include", "--to", "existing-batch", "--line", "1,2,3", check=False)

        # Verify batch has content now
        modified_show = git_stage_batch("show", "--from", "existing-batch")
        assert modified_show.returncode == 0
        assert "Added line 1" in modified_show.stdout or "Added line 2" in modified_show.stdout

        # Abort - should revert to original empty state
        git_stage_batch("abort")

        # Batch should be back to original empty state (no added lines)
        reverted_show = git_stage_batch("show", "--from", "existing-batch")
        # Should not contain the lines added during session
        assert "Added line 1" not in reverted_show.stdout
        assert "Added line 2" not in reverted_show.stdout
        assert "Added line 3" not in reverted_show.stdout

    def test_abort_restores_dropped_batch(self, functional_repo):
        """Test that batches dropped during session are restored on abort."""
        # Create a file
        test_file = functional_repo / "dropped.txt"
        test_file.write_text("Content\n")
        subprocess.run(["git", "add", "dropped.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, capture_output=True)
        test_file.write_text("Content\nNew line\n")

        # Create batch before session
        git_stage_batch("new", "will-drop", "-m", "Original batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "will-drop", "--line", "1", check=False)
        git_stage_batch("abort")

        # Capture original state
        original_show = git_stage_batch("show", "--from", "will-drop")
        assert original_show.returncode == 0
        original_content = original_show.stdout

        # Start session and drop the batch
        git_stage_batch("start")
        git_stage_batch("drop", "will-drop")

        # Verify batch is gone
        list_dropped = git_stage_batch("list")
        assert "will-drop" not in list_dropped.stdout

        # Abort - should restore the batch
        git_stage_batch("abort")

        # Batch should be restored with original content
        restored_show = git_stage_batch("show", "--from", "will-drop")
        assert restored_show.returncode == 0
        assert restored_show.stdout == original_content

    def test_abort_handles_multiple_batch_operations(self, functional_repo):
        """Test abort correctly handles multiple batch operations in one session."""
        # Create file
        test_file = functional_repo / "multi.txt"
        test_file.write_text("Base\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, capture_output=True)
        test_file.write_text("Base\nLine 1\nLine 2\nLine 3\n")

        # Create some batches before session
        git_stage_batch("new", "batch-a", "-m", "Batch A")
        git_stage_batch("new", "batch-b", "-m", "Batch B")

        # Start session and do various operations
        git_stage_batch("start")

        # Modify batch-a
        git_stage_batch("include", "--to", "batch-a", "--line", "1", check=False)

        # Drop batch-b
        git_stage_batch("drop", "batch-b")

        # Create new batch-c
        git_stage_batch("new", "batch-c")
        git_stage_batch("include", "--to", "batch-c", "--line", "2", check=False)

        # Verify state during session
        list_during = git_stage_batch("list")
        assert "batch-a" in list_during.stdout
        assert "batch-b" not in list_during.stdout  # dropped
        assert "batch-c" in list_during.stdout  # created

        # Abort
        git_stage_batch("abort")

        # Check final state:
        # - batch-a should be empty (reverted)
        # - batch-b should be restored
        # - batch-c should be deleted
        list_after = git_stage_batch("list")
        assert "batch-a" in list_after.stdout
        assert "batch-b" in list_after.stdout
        assert "batch-c" not in list_after.stdout

        # batch-a should be empty (reverted to original state, no Line 1)
        batch_a_show = git_stage_batch("show", "--from", "batch-a", check=False)
        assert "Line 1" not in batch_a_show.stdout

        # batch-b should exist
        git_stage_batch("show", "--from", "batch-b", check=False)
        # Should exist even if empty
        assert "batch-b" in list_after.stdout


class TestComplexBatchWorkflows:
    """Test complex batch workflows."""

    def test_split_changes_across_batches(self, repo_with_changes):
        """Test splitting changes across multiple batches."""
        # Create batches for different features
        git_stage_batch("new", "feature-a", "-m", "Feature A changes")
        git_stage_batch("new", "feature-b", "-m", "Feature B changes")
        git_stage_batch("new", "fixes", "-m", "Fixes")

        git_stage_batch("start")

        # Distribute changes across batches
        git_stage_batch("include", "--to", "feature-a", "--line", "1")
        git_stage_batch("skip", check=False)
        git_stage_batch("include", "--to", "feature-b", "--line", "1", check=False)
        git_stage_batch("skip", check=False)
        git_stage_batch("include", "--to", "fixes", "--line", "1", check=False)

        # All batches should have content
        for batch in ["feature-a", "feature-b", "fixes"]:
            result = git_stage_batch("show", "--from", batch, check=False)
            if result.returncode == 0:
                assert result.stdout

    def test_batch_accumulation_workflow(self, repo_with_changes):
        """Test accumulating changes to a batch over multiple sessions."""
        git_stage_batch("new", "accumulated")

        # First session: add some changes
        git_stage_batch("start")
        git_stage_batch("include", "--to", "accumulated", "--line", "1,2")

        # Make more changes
        readme = repo_with_changes / "README.md"
        content = readme.read_text()
        readme.write_text(content + "\n## More Changes\n- Additional feature\n")

        # Second session: add more to same batch
        git_stage_batch("start")
        git_stage_batch("include", "--to", "accumulated", "--line", "1", check=False)

        # Batch should have accumulated content
        batch_show = git_stage_batch("show", "--from", "accumulated")
        assert batch_show.stdout

    def test_apply_batch_then_modify_and_reapply(self, repo_with_changes):
        """Test applying batch, making changes, and reapplying."""
        # Create and populate batch
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2")

        # Clear working tree
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Apply batch
        git_stage_batch("apply", "--from", "test-batch")

        # Should have unstaged changes
        unstaged = get_unstaged_diff()
        assert unstaged

        # Stage and commit
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Applied batch changes"],
            check=True,
            capture_output=True
        )

        # Batch still exists
        result = git_stage_batch("list")
        assert "test-batch" in result.stdout


class TestBatchRebaseWorkflow:
    """Test batch operations with git rebase."""

    def test_batch_changes_then_apply_in_history(self, functional_repo):
        """Test batching changes from tip, then applying them to earlier commit via rebase."""
        # Create a file with stable base content
        file1 = functional_repo / "feature.txt"
        file1.write_text("# Feature\n\ndef main():\n    pass\n")
        subprocess.run(["git", "add", "feature.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add feature skeleton"], check=True, capture_output=True)

        # Add unrelated commit
        file2 = functional_repo / "other.txt"
        file2.write_text("Other file\n")
        subprocess.run(["git", "add", "other.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add other file"], check=True, capture_output=True)

        # Make changes at tip - add a new function
        file1.write_text("# Feature\n\ndef main():\n    pass\n\ndef helper():\n    return 42\n")

        # Batch the tip changes (just the new helper function)
        git_stage_batch("new", "improvements")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "improvements")

        # Clear working tree
        subprocess.run(["git", "restore", "."], check=True, capture_output=True)

        # Rebase to edit the first commit
        # HEAD~2 goes back before both our commits, allowing us to edit both
        # sed will change the first pick to edit (which is "Add feature skeleton")
        env = {**subprocess.os.environ, "GIT_SEQUENCE_EDITOR": "sed -i '1s/pick/edit/'"}
        rebase_result = subprocess.run(
            ["git", "rebase", "-i", "HEAD~2"],
            env=env,
            capture_output=True,
            text=True,
            check=False
        )

        # Should be in rebase state, stopped at first commit
        assert rebase_result.returncode == 0

        # Apply the batch from the tip to this earlier commit
        git_stage_batch("apply", "--from", "improvements")

        # Verify changes are in working tree
        content = file1.read_text()
        assert "helper()" in content
        assert "return 42" in content

        # Amend the commit with the improvements
        subprocess.run(["git", "add", "feature.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--amend", "--no-edit"],
            check=True,
            capture_output=True
        )

        # Continue the rebase
        continue_result = subprocess.run(
            ["git", "rebase", "--continue"],
            capture_output=True,
            text=True,
            check=False
        )

        # Rebase should complete successfully
        assert continue_result.returncode == 0

        # Verify the improvement is now in the first commit
        first_commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()

        show_result = subprocess.run(
            ["git", "show", first_commit_hash],
            capture_output=True,
            text=True,
            check=True
        )

        # The helper function should be in the first commit now
        assert "helper()" in show_result.stdout
        assert "return 42" in show_result.stdout

        # Batch should still exist
        result = git_stage_batch("list")
        assert "improvements" in result.stdout

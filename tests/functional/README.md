# Functional Tests

Comprehensive end-to-end tests that verify git-stage-batch works correctly as users would actually use it.

## Purpose

These tests catch issues that unit tests miss:
- CLI commands actually execute
- Commands work together in realistic workflows
- Error messages are clear
- Edge cases are handled gracefully
- Features work end-to-end, not just in isolation

## Test Files

### `test_basic_workflow.py` (10 test classes, ~30 tests)
Tests fundamental git-stage-batch workflow:
- **TestStartSession**: Starting sessions (with/without changes, outside repo)
- **TestIncludeWorkflow**: Including lines (single, multiple, ranges, all)
- **TestSkipWorkflow**: Skipping through hunks
- **TestDiscardWorkflow**: Discarding changes
- **TestShowCommand**: Show command behavior
- **TestAbortSession**: Aborting and restoring state
- **TestCompleteWorkflow**: End-to-end scenarios (incremental staging, mixed operations, full commits)

### `test_batch_operations.py` (8 test classes, ~25 tests)
Tests batch features:
- **TestCreateBatch**: Creating batches with/without notes, duplicate detection
- **TestIncludeToBatch**: Saving changes to batches
- **TestDiscardToBatch**: Discarding to batches
- **TestShowFromBatch**: Viewing batch contents (with note display)
- **TestApplyFromBatch**: Applying batches back to working tree
- **TestBatchList**: Listing batches
- **TestBatchDelete**: Deleting batches
- **TestComplexBatchWorkflows**: Multi-batch scenarios, accumulation, reapplication

### `test_multi_file.py` (4 test classes, ~12 tests)
Tests multi-file scenarios:
- **TestMultiFileWorkflow**: Multiple files in single session
- **TestNewFileHandling**: New file staging and batching
- **TestLargeChangesets**: Many files with changes
- **TestFileWithManyChanges**: Large files with many changes

### `test_error_handling.py` (7 test classes, ~25 tests)
Tests error cases and edge cases:
- **TestInvalidInput**: Invalid line IDs, batch names, commands
- **TestOperationWithoutSession**: Operations requiring active session
- **TestNonexistentBatch**: Operations on missing batches
- **TestEdgeCases**: Empty working tree, staged changes, conselected sessions
- **TestBatchConflicts**: Batch operations with conflicts
- **TestPermissionErrors**: Read-only FS, missing .git, corrupted repo
- **TestRecovery**: Recovering from error states

### `test_status.py` (2 test classes, ~12 tests)
Tests status command:
- **TestStatusCommand**: Status display in various states, progress tracking, shorthand
- **TestStatusWithBatches**: Status with batch information

### `test_interactive.py` (9 test classes, ~45 tests)
Tests interactive/TUI mode:
- **TestInteractiveMode**: Starting interactive mode (-i flag, no changes)
- **TestInteractiveCommands**: Commands in interactive mode (include, skip, show, quit)
- **TestInteractiveWorkflow**: Multi-command workflows
- **TestInteractiveBatchOperations**: Batch operations in interactive mode
- **TestInteractiveEdgeCases**: Empty input, rapid commands, multiple quits
- **TestInteractiveSession**: Session management in interactive mode
- **TestInteractiveDisplay**: Output and progress display
- **TestInteractiveVsNonInteractive**: Comparing interactive and non-interactive modes

## Running Tests

```bash
# Run all functional tests
uv run pytest tests/functional/

# Run specific test file
uv run pytest tests/functional/test_basic_workflow.py

# Run specific test class
uv run pytest tests/functional/test_batch_operations.py::TestCreateBatch

# Run with verbose output
uv run pytest tests/functional/ -v

# Run and stop on first failure
uv run pytest tests/functional/ -x

# Run specific test
uv run pytest tests/functional/test_status.py::TestStatusCommand::test_status_after_start -xvs
```

## Test Coverage Summary

**Total Functional Tests**: ~150 tests covering:
- ✅ Basic workflow commands (start, include, skip, discard, show, abort)
- ✅ Batch operations (create, list, delete, include-to, discard-to, show-from, apply-from)
- ✅ Multi-file scenarios
- ✅ Status command
- ✅ Interactive/TUI mode (45 tests)
- ✅ Error handling and edge cases
- ✅ Session management
- ✅ Progress tracking
- ✅ Recovery from errors

## Current Status

**Status & Interactive Tests**: 45/45 passing ✅
**Other Functional Tests**: Some failures (expected - catching real issues)

The failing tests are **valuable** - they're catching actual bugs and edge cases that need fixing.

## Fixtures

All tests use shared fixtures from `conftest.py`:

- `functional_repo`: Clean git repo with initial commit
- `repo_with_changes`: Repo with realistic uncommitted changes
- `run_gsb()`: Helper to run git-stage-batch commands
- `get_git_status()`: Get selected git status
- `get_staged_files()`: List staged files
- `get_staged_diff()`: Get staged diff
- `get_unstaged_diff()`: Get unstaged diff
- `run_interactive()`: Run interactive mode with simulated input

## Writing New Tests

Example functional test:

```python
def test_my_feature(repo_with_changes):
    """Test my feature end-to-end."""
    # Start session
    run_gsb("start")

    # Use feature
    result = run_gsb("my-command", "arg1", "arg2")
    assert result.returncode == 0

    # Verify results
    staged = get_staged_diff()
    assert "expected content" in staged
```

## Philosophy

These tests verify **user experience**, not implementation details:
- Run actual CLI commands
- Verify observable behavior
- Test realistic workflows
- Catch "works in tests but fails in reality" issues
- Ensure error messages are helpful
- Validate commands work together

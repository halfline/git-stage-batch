# Development Guide for git-stage-batch

This document provides guidance for AI assistants working on this codebase.

## Project Structure

This is a Python package using modern tooling (uv) with a modular architecture:

```
src/git_stage_batch/
├── __init__.py          # Package metadata
├── state.py             # Foundation: git commands, file I/O, state management
├── models.py            # Data structures: HunkHeader, LineEntry, CurrentLines, SingleHunkPatch
├── parser.py            # Parse git diffs into structured models
├── hashing.py           # Compute stable hunk identities
├── line_selection.py    # Parse and persist line ID selections
├── display.py           # Print annotated hunks with line IDs
├── editor.py            # Core logic: apply line-level changes to index/worktree
├── commands.py          # Command implementations (start, include, skip, etc.)
└── cli.py               # CLI entry point and argument parsing

tests/                   # Comprehensive test suite (176 tests)
├── test_state.py
├── test_models.py
├── test_parser.py
├── test_hashing.py
├── test_line_selection.py
├── test_display.py
├── test_editor.py
└── test_commands.py
```

## Development Workflow

### Running Tests

Always run tests before committing:
```bash
uv run pytest
```

For specific test files:
```bash
uv run pytest tests/test_state.py -v
```

### Making Changes

1. **Add tests first** for new functionality
2. **Run tests** to ensure they pass
3. **Use this tool itself** to create atomic commits

### Commit Workflow

**IMPORTANT**: This project uses its own tool to create commits. Follow this process:

1. After making changes, run:
   ```bash
   uv run git-stage-batch start
   ```

2. For each presented hunk, use one of:
   - `uv run git-stage-batch include` or `uv run git-stage-batch i` (stage this hunk)
   - `uv run git-stage-batch skip` or `uv run git-stage-batch s` (skip for now)
   - `uv run git-stage-batch discard` or `uv run git-stage-batch d` (remove from working tree)
   - `uv run git-stage-batch` (no command defaults to include when session is active)

3. For fine-grained control:
   ```bash
   uv run git-stage-batch include-line 1,3,5-7  # or: il 1,3,5-7
   uv run git-stage-batch skip-line 2,4         # or: sl 2,4
   ```

4. After staging changes for one logical commit:
   ```bash
   git commit -m "..."
   ```

5. For the next commit:
   ```bash
   uv run git-stage-batch again
   ```

### Commit Message Format

**Follow the format in CONTRIBUTING.md exactly.** Three-paragraph structure:

**First line**: `prefix: Concise summary` (e.g., `parser:`, `tests:`, `editor:`)

**First paragraph**: Describe the code being changed (present tense, current state)

**Second paragraph**: Explain the problem with the existing state

**Third paragraph**: "This commit addresses that by..." (describe the solution)

**Do NOT use `Co-Authored-By` for AI contributions.**

### Code Conventions

- **Type hints**: Use modern Python type hints (`list[str]`, `dict[str, Any]`)
- **Imports**: Group into stdlib, third-party, local (separated by blank lines)
- **Docstrings**: Use for modules and complex functions
- **Line length**: Reasonable (aim for ~100 chars, not strict)
- **Testing**: Every new function should have tests

### Module Responsibilities

- **state.py**: Low-level git/filesystem operations, state path definitions
- **models.py**: Pure data structures, no I/O
- **parser.py**: Convert git diff text to structured models
- **hashing.py**: Compute SHA1 hashes of hunks for identity tracking
- **line_selection.py**: Parse user input like "1,3,5-7" into line ID lists
- **display.py**: Format and print hunks with line IDs
- **editor.py**: Reconstruct file content with selected line changes
- **commands.py**: Orchestrate modules to implement user commands
- **cli.py**: Parse arguments and dispatch to commands

### When Adding Features

1. **Determine which module** the feature belongs in
2. **Add tests first** in the corresponding test file
3. **Implement the feature** keeping module boundaries clean
4. **Update README** if user-facing
5. **Create atomic commits** using the tool itself

### Common Patterns

**Reading state files:**
```python
from .state import read_text_file_contents, get_some_path
content = read_text_file_contents(get_some_path())
```

**Writing state files:**
```python
from .state import write_text_file_contents, get_some_path
write_text_file_contents(get_some_path(), data)
```

**Running git commands:**
```python
from .state import run_git_command
result = run_git_command(["status", "--short"])
```

**Error handling:**
```python
from .state import exit_with_error
if not valid:
    exit_with_error("Clear error message for user")
```

## Testing Philosophy

- **Unit tests**: Test individual functions in isolation
- **Integration tests**: Test command workflows (see test_commands.py)
- **Use temp git repos**: The fixture `temp_git_repo` creates isolated test environments
- **Test edge cases**: Empty files, new files, deleted files, no trailing newline, etc.
- **Descriptive test names**: `test_include_line_stages_specific_lines` not `test_include`

## Dependencies

This project has **zero runtime dependencies** - only stdlib. Keep it that way.

Dev dependencies (pytest) are fine to add if they improve development experience.

## Documentation

- **README.md**: User-facing installation and usage
- **CONTRIBUTING.md**: Contributor guidelines and commit format
- **CLAUDE.md**: This file - AI assistant guidance
- **Docstrings**: For complex functions and modules
- **Type hints**: For all function signatures

## Questions?

Refer to:
- Existing code for patterns
- Tests for examples
- CONTRIBUTING.md for commit format
- README.md for user perspective

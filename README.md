<p align="center">
  <img src="https://halfline.github.io/git-stage-batch/assets/batch-of-patches.png" alt="git-stage-batch banner" width="600">
</p>

# git-stage-batch

[![PyPI version](https://img.shields.io/pypi/v/git-stage-batch)](https://pypi.org/project/git-stage-batch/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/git-stage-batch)](https://pypi.org/project/git-stage-batch/)
[![CI](https://github.com/halfline/git-stage-batch/actions/workflows/ci.yml/badge.svg)](https://github.com/halfline/git-stage-batch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Hunk-by-hunk and line-by-line staging for git, designed for building clean commit history.

**Writing code is messy. Git history doesn't have to be.**

<p align="center">
  <img src="https://github.com/halfline/git-stage-batch/releases/download/v0.5.0/demo.gif" alt="git-stage-batch demo" width="700">
</p>

During development we experiment, refactor, backtrack, and fix mistakes. If every step ends up as a commit, the history becomes noise. A curated history turns that process into a clear sequence of logical changes.

`git-stage-batch` helps you build that history incrementally by letting you stage changes hunk-by-hunk or line-by-line, shaping commits around meaning instead of the order the edits happened.

## Features

- **Command-based workflow** - Perfect for automation and AI coding assistants
- **Line-level control** - Stage specific lines within a hunk for maximum granularity
- **Interactive mode** - Menu-driven hunk-by-hunk workflow inspired by `git add -p`
- **State persistence** - Resume staging across multiple invocations
- **Batch operations** - Save hunks for later, organize complex changes
- **Machine-readable output** - `--porcelain` flag for scripting
- **No dependencies** - Pure Python standard library

## Quick Start

```bash
# Start reviewing hunks
git-stage-batch start

# Include the selected hunk (stage it)
git-stage-batch include

# Skip it for now
git-stage-batch skip

# Discard it (remove from working tree)
git-stage-batch discard

# Stage specific lines within a hunk
git-stage-batch include --line 1,3,5-7
git-stage-batch skip --line 2,4
git-stage-batch discard --line 8-10

# Check what's been processed
git-stage-batch status

# Start fresh after committing
git-stage-batch again
```

## Example Workflow

```bash
# You have changes in multiple files
git status
# modified:   auth.py
# modified:   config.py

# Start staging
git-stage-batch start
# auth.py :: @@ -10,5 +10,5 @@
# [#1] - old_hash_function()
# [#2] + new_hash_function()

# Include this for first commit
git-stage-batch i

# Create first commit
git commit -m "auth: Upgrade to new hash function"

# Continue with remaining changes
git-stage-batch a
```

## Why git-stage-batch?

Similar to `git add -p` but **more granular and flexible**:

- ✅ **Line-by-line staging** - Stage specific lines within a hunk
- ✅ **Interactive mode** - Continuous hunk-by-hunk workflow with menus
- ✅ **Batch operations** - Save hunks for later processing
- ✅ **Colored output** - Clear visual distinction in your terminal
- ✅ **File operations** - Stage/skip entire files at once

## Interactive Mode

For a continuous hunk-by-hunk workflow:

```bash
# Launch interactive mode
git-stage-batch -i

# Navigate with single-key commands
# [i]nclude, [s]kip, [d]iscard, [l]ines, [f]ile, [a]gain, [q]uit
```

## Machine-Readable Output

For scripting and automation, use the `--porcelain` flag:

```bash
# Get status as JSON
git-stage-batch status --porcelain

# Add active session status next to a __git_ps1 branch
PS1=$PS1'\r$(__git_ps1 "\n╎\e[32m%s$(git-stage-batch status --for-prompt=\|{status}\ {processed}/{total})\e[0m")\n'

# Check if a hunk exists (exit code 0/1)
git-stage-batch show --porcelain
```

## Batch Operations

Save hunks for later processing with named batches:

```bash
# Create a new batch
git-stage-batch new feature-work --note "Refactoring work"

# List all batches
git-stage-batch list

# Annotate a batch
git-stage-batch annotate feature-work "Updated description"

# Drop a batch when done
git-stage-batch drop feature-work
```

## Installation

```bash
# Using uv (recommended)
uv tool install git-stage-batch

# Using pipx
pipx install git-stage-batch

# Using pip
pip install git-stage-batch
```

## Requirements

- Python 3.10+
- No other dependencies (pure stdlib!)

## Documentation

- **[Full Documentation](https://halfline.github.io/git-stage-batch/)** - Complete guide and examples
- **[Installation Guide](https://halfline.github.io/git-stage-batch/installation/)** - All installation methods
- **[Commands Reference](https://halfline.github.io/git-stage-batch/commands/)** - Complete command documentation
- **[Examples](https://halfline.github.io/git-stage-batch/examples/)** - Common workflows and use cases
- **[AI Assistant Guide](https://halfline.github.io/git-stage-batch/ai-assistants/)** - Configure Claude, Cursor, etc.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for:

- Development setup instructions
- Commit message guidelines
- Code style conventions

## License

MIT License

## Links

- **Repository**: https://github.com/halfline/git-stage-batch
- **Documentation**: https://halfline.github.io/git-stage-batch/
- **Issues**: https://github.com/halfline/git-stage-batch/issues

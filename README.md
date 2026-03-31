# git-stage-batch

Hunk-by-hunk and line-by-line staging for git, designed for building clean commit history.

```
   ┌─────────────────┐
   │ git-stage-batch │
   └─────────────────┘

       o───o
      /
  o───o───o

 stage patches in batches
```

**Writing code is messy. Git history doesn't have to be.**

During development we experiment, refactor, backtrack, and fix mistakes. If every step ends up as a commit, the history becomes noise. A curated history turns that process into a clear sequence of logical changes.

`git-stage-batch` helps you build that history incrementally by letting you stage changes hunk-by-hunk, shaping commits around meaning instead of the order the edits happened.

## Features

- **Command-based workflow** - Perfect for automation and AI coding assistants
- **State persistence** - Resume staging across multiple invocations
- **No dependencies** - Pure Python standard library

## Quick Start

```bash
# Start reviewing hunks
git-stage-batch start

# Include the current hunk (stage it)
git-stage-batch include

# Skip it for now
git-stage-batch skip

# Discard it (remove from working tree)
git-stage-batch discard

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

- ✅ **Colored output** - Clear visual distinction in your terminal
- ✅ **File operations** - Stage/skip entire files at once

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

- Python 3.13+
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

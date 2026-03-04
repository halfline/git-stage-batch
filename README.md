# git-stage-batch

Non-interactive hunk-by-hunk and line-by-line staging for git, designed for AI-assisted development and automation.

Similar to `git add -p` (interactive patch mode), but non-interactive and more granular. Perfect for AI coding assistants to create atomic, well-structured commits.

## Features

- **Hunk-by-hunk staging**: Review and stage individual hunks one at a time
- **Line-by-line staging**: Stage specific lines within a hunk for maximum granularity
- **State persistence**: Track processed/skipped hunks across multiple command invocations
- **Three operations**:
  - `include` - stage to index
  - `skip` - skip for now
  - `discard` - remove from working tree
- **Non-interactive workflow**: Perfect for automation and AI assistants

## Installation

**Recommended** (install once, use everywhere):
```bash
uv tool install git-stage-batch
```

**Alternative** (try without installing):
```bash
uvx git-stage-batch start
```

## Quick Start

```bash
# Start reviewing hunks
git-stage-batch start

# Include the current hunk (stage it)
git-stage-batch include
# Or use the short alias:
git-stage-batch i
# Or just run with no command (defaults to include when session is active):
git-stage-batch

# Skip it (skip for now)
git-stage-batch skip    # or: git-stage-batch s

# Discard it (remove from working tree)
git-stage-batch discard # or: git-stage-batch d

# For fine-grained control, include/skip/discard specific lines
git-stage-batch include-line 1,3,5-7  # or: git-stage-batch il 1,3,5-7
git-stage-batch skip-line 2,4         # or: git-stage-batch sl 2,4
git-stage-batch discard-line 8-10     # or: git-stage-batch dl 8-10

# Check status
git-stage-batch status  # or: git-stage-batch st

# Start fresh after committing
git-stage-batch again   # or: git-stage-batch a

# Clear all state
git-stage-batch stop
```

## Commands

- **`start`** - Find and display the first unprocessed hunk; cache as "current"
- **`show`** - Reprint the cached "current" hunk (annotated with line IDs)
- **`include`** - Stage the cached hunk (entire hunk) to the index; advance
- **`skip`** - Mark the cached hunk as skipped; advance
- **`discard`** - Reverse-apply the cached hunk to the working tree; advance
- **`include-line IDS`** - Stage ONLY the listed changed line IDs (+/-) to the index
- **`skip-line IDS`** - Mark ONLY the listed changed line IDs as skipped (skip)
- **`discard-line IDS`** - Remove ONLY the listed changed line IDs from working tree
- **`again`** - Clear state and immediately start a fresh pass
- **`stop`** - Clear all state (blocklist and cached hunk)
- **`status`** - Show brief state (current hunk summary, remaining line IDs)

**Line ID syntax**: Comma-separated list with ranges, e.g. `1,3,5-7`

## Configuring Your AI Assistant

This tool is designed for AI coding assistants to create atomic, well-structured commits. Add the following instructions to your project's AI configuration:

### For Claude Code

Create or update `CLAUDE.md` in your repository root:

```markdown
## Commit Workflow

**Commits** should be atomic and well-structured. Each commit should represent one
logical step in the project's development. The goal is not to preserve the exact
twists and turns of drafting, but to produce a commit history that tells a clear
story of progress.

To aid in this endeavor, use the `git-stage-batch` tool. It provides
functionality similar to `git add -p` in a multi-command flow more suitable for
automation.

### Staging Process

1. Run `git-stage-batch start` to begin the process
2. For each presented patch hunk, run either:
   - `git-stage-batch include` (stage this hunk)
   - `git-stage-batch skip` (skip this hunk for now)

   These commands automatically display the next hunk to evaluate.

3. Repeatedly run these commands until all hunks relevant to the current commit
   are processed, then commit the results.

4. Run `git-stage-batch again` to run through all previously skipped
   and unprocessed hunks for the next commit.

5. Repeat until all commits are in place.

### Fine-Grained Line Selection

If a hunk is too coarse and contains multiple orthogonal changes, individual lines
may be included or skipped using:
- `git-stage-batch include-line 1,3,5-7` (stage specific lines)
- `git-stage-batch skip-line 2,4` (skip specific lines)

Line IDs are shown in the hunk output as `[#N]` markers.

### Commit Messages

Commit messages should aid **drive-by reviewers with limited context**. Assume
the reader does not know the project well.

Format:
- **First line**: a concise summary with a lowercase prefix (`module:`, `cli:`, etc.)
- **First paragraph**: summarize the code being changed (not the change itself)
- **Second paragraph**: explain the problem with the existing state
- **Third paragraph**: describe how the problem is solved ("This commit addresses
  that by...")

Write in the tense that reflects the state **just before** the commit is applied.
```

### For Cursor

Create or update `.cursorrules` in your repository root with the same instructions as above.

### For Other AI Assistants

Most AI coding assistants support project-specific instructions. Add the workflow
instructions above to your tool's configuration file. Common locations:
- `.continuerules` (Continue.dev)
- `.aider.conf.yml` (Aider)
- Custom instructions in your IDE settings

## Example Workflow

```bash
# You have changes in multiple files
$ git status
modified:   file1.py
modified:   file2.py

# Start staging process
$ git-stage-batch start
file1.py :: @@ -10,5 +10,5 @@
[#1] - old_function()
[#2] + new_function()
      context_line()

# Include this change for first commit
$ git-stage-batch include
file2.py :: @@ -20,3 +20,4 @@
[#1] + debug_line()
      production_code()

# This debug line shouldn't be committed, skip it
$ git-stage-batch skip
No pending hunks.

# Create first commit
$ git commit -m "refactor: Replace old_function with new_function

The codebase currently uses old_function for processing data.

old_function has a performance bottleneck and doesn't support the new
data format we need.

This commit addresses that by replacing old_function with new_function,
which is 2x faster and handles both old and new data formats."

# Go through skipped hunks for next commit
$ git-stage-batch again
file2.py :: @@ -20,3 +20,4 @@
[#1] + debug_line()
      production_code()

# Discard this debug line instead
$ git-stage-batch discard
No pending hunks.

# Working tree is now clean
$ git status
nothing to commit, working tree clean
```

### Fast Workflow with Aliases

For even faster operation, use short aliases and the bare command (which defaults to `include`):

```bash
$ git-stage-batch start
[hunk displayed]

$ git-stage-batch        # No command = include
[next hunk displayed]

$ git-stage-batch        # Include again
[next hunk displayed]

$ git-stage-batch s      # Skip this one
[next hunk displayed]

$ git-stage-batch        # Include
No pending hunks.

$ git commit -m "..."
$ git-stage-batch a      # Again
[first skipped hunk displayed]
```

## How It Works

The tool maintains state in `.git/git-stage-batch/`:
- `blocklist` - Hashes of hunks you've processed (included, skipped, or discarded)
- `current-hunk.patch` - The hunk currently being evaluated
- `current-lines.json` - Structured representation with line IDs
- `processed.include` / `processed.skip` - Track line-level decisions
- `snapshot-base` / `snapshot-new` - File snapshots for accurate reconstruction

State persists across invocations, allowing you to stage changes incrementally.

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Clone the repository
git clone https://github.com/halfline/git-stage-batch.git
cd git-stage-batch

# Run tests
uv run pytest

# Install from source
uv tool install .
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for commit message guidelines and development workflow.

## License

MIT

# Contributing to git-stage-batch

Thank you for your interest in contributing!

## Development Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and development workflows.

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/halfline/git-stage-batch.git
cd git-stage-batch

# Install dependencies (when added)
uv sync
```

## Commit Message Guidelines

We follow strict commit message conventions to maintain a clear and understandable project history.

### Key Principles

- **Write for drive-by reviewers with limited context.** Assume the reader does not know the project well.
- **Use the tense that reflects the state of the project just before the commit is applied.** When discussing the old behavior, treat it as the current behavior. When discussing the changes, treat them as new behavior.
- **Do not use `Co-Authored-By` for contributions produced from AI.** Only use it for human co-authors.
- **Only use the word `this` when referring to the commit itself** Use `that` or similar for other contexts.

### Format

Commit messages should follow this three-paragraph structure:

#### First Line (Summary)

```
prefix: Concise summary of the change
```

- Use a short, lowercase prefix (`project:`, `cli:`, `patch:`, `editor:`, `state:`, etc.)
- Capitalize the first word of the summary after the colon
- Keep the entire line under 72 characters
- If unsure which prefix to use, run `git log --pretty=oneline FILE` and see what prefixes were used previously

#### First Paragraph

Summarize **the code being changed** (not the change itself). Describe what currently exists in the codebase that this commit will modify.

#### Second Paragraph

Explain **the problem with the existing state of affairs.** What is broken, missing, unclear, or suboptimal about the current code?

#### Third Paragraph

Describe **how the problem is solved by the commit.** Use natural prose such as "This commit addresses that by..." or "This commit solves this by..."

### Example

```
cli: Add --verbose flag for detailed output

The CLI currently provides minimal feedback during operation, only showing
the current hunk without any indication of progress or internal state.

Users working with large changesets have no visibility into how many hunks
remain or what has been processed. This makes it difficult to gauge progress
or debug unexpected behavior.

This commit addresses that by adding a --verbose flag that displays
additional information including the number of blocked hunks, total hunks
processed, and the current hunk's position in the sequence. The flag is
optional and maintains the existing terse output when not specified.
```

### Anti-Patterns to Avoid

❌ **Don't write in past tense about the old state:**
```
The code used to only show minimal output...
```

✅ **Do write in present tense about the current state:**
```
The code currently provides minimal output...
```

❌ **Don't describe the change in the first paragraph:**
```
This commit adds verbose output to the CLI...
```

✅ **Do describe what code is being modified:**
```
The CLI currently provides minimal feedback during operation...
```

## Making Changes

1. **Keep commits atomic.** Each commit should represent one logical change.
2. **Use the `git-stage-batch` tool itself** to help stage micro-commits from larger working directory changes.
3. **Follow existing code style.** The project uses standard Python conventions.

## Questions?

Feel free to open an issue for discussion before starting major work.

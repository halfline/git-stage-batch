# AI Assistant Configuration

Configure AI coding assistants to use git-stage-batch for creating atomic, well-structured commits.

## Why Use git-stage-batch with AI?

AI coding assistants often make multiple changes across many files. git-stage-batch allows them to:

- ✅ Create atomic commits (one logical change per commit)
- ✅ Separate orthogonal changes that ended up together
- ✅ Build a clean, reviewable git history
- ✅ Stage changes incrementally with fine-grained control

## Claude Code

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
- `git-stage-batch include --line 1,3,5-7` (stage specific lines)
- `git-stage-batch skip --line 2,4` (skip specific lines)

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

## Cursor

Create or update `.cursorrules` in your repository root with the same instructions as above.

## Continue.dev

Create or update `.continuerules`:

```yaml
# git-stage-batch workflow
staging:
  tool: git-stage-batch
  workflow: |
    Use git-stage-batch for atomic commits:

    1. Start: git-stage-batch start
    2. Process hunks:
       - git-stage-batch include (stage)
       - git-stage-batch skip (skip for now)
       - git-stage-batch include --line 1,3,5-7 (specific lines)
    3. Commit when done: git commit -m "..."
    4. Review skipped: git-stage-batch again
    5. Repeat

    Line IDs shown as [#N] in output.
```

## Aider

Add to `.aider.conf.yml`:

```yaml
# Use git-stage-batch for staging
edit-format: whole
auto-commits: false

# Instructions for commit workflow
instructions: |
  Use git-stage-batch for creating atomic commits:

  - Start with: git-stage-batch start
  - Include hunks: git-stage-batch include
  - Skip hunks: git-stage-batch skip
  - Line-level: git-stage-batch include-line 1,3,5-7
  - After commit: git-stage-batch again

  Create separate commits for separate concerns.
```

## Example AI Workflow

Here's what an AI assistant would do:

```
# AI makes changes to multiple files
# Now needs to create atomic commits

# First commit: Authentication changes
git-stage-batch start
# Shows: auth.py hunk
git-stage-batch include
# Shows: database.py hunk
git-stage-batch skip
# Shows: config.py hunk (auth-related)
git-stage-batch include
# Shows: utils.py hunk
git-stage-batch skip
# No more hunks

git commit -m "auth: Implement OAuth2 authentication

The authentication system currently uses basic auth with password hashing.

OAuth2 is required for integration with third-party services and provides
better security through token-based authentication.

This commit addresses that by implementing OAuth2 flow with token refresh
and adding configuration for OAuth providers."

# Second commit: Database changes
git-stage-batch again
# Shows: database.py hunk (skipped earlier)
git-stage-batch include
# Shows: utils.py hunk
git-stage-batch skip
# No more hunks

git commit -m "database: Add connection pooling

The database module creates a new connection for each query.

This leads to performance issues under load and exhausts connection limits
when handling concurrent requests.

This commit addresses that by implementing connection pooling with configurable
pool size and timeout settings."

# Continue until all changes are committed...
```

## Machine-Readable Output

AI assistants can use `--porcelain` flags for programmatic access:

```
# Check if there's a current hunk
if git-stage-batch show --porcelain; then
    echo "Hunk exists"
fi

# Get structured status
status=$(git-stage-batch status --porcelain)
echo "$status" | jq '.current_hunk'
echo "$status" | jq '.remaining_line_ids'
echo "$status" | jq '.blocked_hunks'
```

## Tips for AI Configuration

1. **Be explicit** - Tell the AI to use git-stage-batch in your instructions
2. **Show examples** - Include example workflows in your config
3. **Emphasize atomic commits** - Make it clear commits should be focused
4. **Line-level control** - Remind the AI about `include --line` for mixed hunks
5. **`again` command** - Ensure the AI knows to run `again` after commits

## Benefits for AI-Assisted Development

| Without git-stage-batch | With git-stage-batch |
|------------------------|---------------------|
| One giant commit | Multiple atomic commits |
| Mixed concerns | Separated concerns |
| Hard to review | Easy to review |
| Difficult to revert | Easy to revert specific changes |
| Unclear history | Clear, logical progression |

## Common Patterns

### Pattern 1: Feature Implementation

```
# AI implements a feature touching multiple files
git-stage-batch start

# Include all feature-related hunks
git-stage-batch include  # feature code
git-stage-batch skip     # tests (separate commit)
git-stage-batch include  # feature code
git-stage-batch skip     # docs (separate commit)

git commit -m "feature: Implement user dashboard"

# Tests
git-stage-batch again
git-stage-batch include  # tests
git commit -m "tests: Add dashboard tests"

# Docs
git-stage-batch again
git-stage-batch include  # docs
git commit -m "docs: Document dashboard feature"
```

### Pattern 2: Refactoring

```
# AI refactors code with both rename and logic changes
git-stage-batch start

# Separate rename from logic changes using line-level
# Hunk shows both rename and logic change
git-stage-batch include --line 1-5    # rename only
git commit -m "refactor: Rename helper_function to process_data"

git-stage-batch again
git-stage-batch include             # logic changes
git commit -m "refactor: Improve process_data algorithm"
```

### Pattern 3: Bug Fix with Tests

```
# AI fixes bug and adds regression test
git-stage-batch start
git-stage-batch include  # bug fix
git-stage-batch skip     # test
git commit -m "fix: Handle edge case in parser"

git-stage-batch again
git-stage-batch include  # test
git commit -m "tests: Add regression test for parser edge case"
```

## Next Steps

- [Examples](examples.md) - See more workflow examples
- [Commands Reference](commands.md) - Full command documentation
- [Interactive Mode](interactive.md) - Alternative workflow

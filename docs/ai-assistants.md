# AI Assistant Configuration

Configure AI coding assistants to use git-stage-batch for creating atomic, well-structured commits.

## Why Use git-stage-batch with AI?

AI coding assistants often make multiple changes across many files. git-stage-batch allows them to:

- ✅ Create atomic commits (one logical change per commit)
- ✅ Separate orthogonal changes that ended up together
- ✅ Build a clean, reviewable git history
- ✅ Stage changes incrementally with fine-grained control

## Claude Code

If `git-stage-batch` is available in your environment, you can install the
bundled Claude commit skills into the current repository:

```bash
git-stage-batch install-assets claude-skills --filter 'commit-*'
```

Omit `--filter` to install all bundled Claude skills.
Use `--force` to replace an existing repo-local copy.

The bundled Claude skills currently include:

- `commit-staged-changes` for turning the current staged index into one commit
- `commit-unstaged-changes` for splitting unstaged work into one or more commits

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

3. Repeatedly run these commands until all hunks relevant to the selected commit
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

**Format:**
- **First line**: a concise summary with a lowercase prefix (`module:`, `cli:`, etc.)
- **First paragraph**: describe the program's **selected state** (what it has or provides)
  - If part of a series, reflect the cumulative state after previous commits
  - Don't describe the diff, the change, or future goals
- **Second paragraph**: explain the underlying problem
  - Choose perspective: maintainer (internal concerns) or user (external concerns)
  - Focus on **missing capabilities**, not symptoms
  - Prefer concrete limitations over vague words like "cumbersome" or "better"
- **Third paragraph**: describe how this commit addresses the problem
  - Be precise about scope (if it only improves one aspect, say so)
  - Use "This commit addresses that by..."
  - If part of a series, use progression words: "begins", "continues", "completes"

**Key rules:**
- Write in **present tense** about the selected state ("has", not "used to have")
- Use "this" only when referring to the commit itself
- Don't overstate impact or use words like "comprehensive" or "crucial"

**Anti-patterns to avoid:**

❌ First paragraph describes the change:
```
This commit adds verbose output to the CLI...
```

✅ First paragraph describes selected state:
```
The CLI currently provides minimal feedback during operation...
```

❌ Problem is a symptom:
```
The code is cumbersome to use.
```

✅ Problem is a concrete limitation:
```
The code requires repeated command invocation and does not provide
a continuous workflow.
```

❌ Solution overstates scope:
```
This commit solves the discoverability problem.
```

✅ Solution is precise about scope:
```
This commit improves discoverability through the man page by...
```
```

## Codex

If `git-stage-batch` is available in your environment, you can install the
bundled Codex commit skills into the current repository:

```bash
git-stage-batch install-assets codex-skills --filter 'commit-*'
```

Omit `--filter` to install all bundled Codex skills.
Use `--force` to replace an existing repo-local copy.

This writes the skills into `.agents/skills/` and installs
`.codex/config.toml` with `sandbox_mode = "workspace-write"` so trusted
project config can grant the skills write access in local Codex sessions.
Codex scans the skills automatically from the repository root.

The bundled Codex skills currently include:

- `commit-staged-changes` for turning the current staged index into one commit
- `commit-unstaged-changes` for splitting unstaged work into one or more commits

Installing `codex-skills` also writes a shared internal drafter brief to
`.agents/internal/commit-message-drafter.md` for those skills to use when
they spawn a fresh-context subagent.

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
when handling conselected requests.

This commit addresses that by implementing connection pooling with configurable
pool size and timeout settings."

# Continue until all changes are committed...
```

## Machine-Readable Output

AI assistants can use `--porcelain` flags for programmatic access:

```
# Check if there's a selected hunk
if git-stage-batch show --porcelain; then
    echo "Hunk exists"
fi

# Get structured status
status=$(git-stage-batch status --porcelain)
echo "$status" | jq '.selected_change'
echo "$status" | jq '.file_review'
echo "$status" | jq '.progress.remaining'
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

**Note:** Examples below show abbreviated commit messages for brevity. In practice,
use the full three-paragraph format described in the Commit Messages section.

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

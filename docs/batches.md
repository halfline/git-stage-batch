# Batch Operations

!!! info "Power User Feature"
    Batches are an advanced feature for complex workflows. Most users will not need them.
    The core commands (start, include, skip, discard) handle the majority of use cases.

Batches are named collections of accumulated changes that can be staged or discarded later as a unit. They persist across sessions and are stored as git commits under `refs/batches/<name>`.

Each batch captures not just the changes themselves, but also the working tree state at the time changes were saved (the **batch source**). This allows batch operations to intelligently merge or discard changes even when your code has evolved since the batch was created.

**When to use batches:**
- Accumulating related changes across multiple hunks for review together
- Deferring changes without losing them while working on other commits
- Grouping changes by type (e.g., debugging, refactoring) for separate handling

**When to avoid batches:**
- Simple linear workflows (just use skip and again for another pass)
- One-off staging decisions (include/skip/discard are simpler)

---

## How Batches Work

### Storage Model

When you save content to a batch (via `include --to` or `discard --to`), the tool captures:

1. **Batch source commit**: A snapshot of the working tree state at save time
2. **Ownership claims**: Which specific lines or line ranges are batch-owned
3. **Deletion claims**: Which sequences were deleted by the batch (if any)

This information is stored in:
- A Git commit under `refs/batches/<name>` containing the realized batch content
- Metadata tracking the batch source commit and ownership structure

### Application Model (include/apply --from)

When applying a batch to your working tree or index, the tool uses **structural merge**:

1. **Conservative matching**: Uses longest common subsequence-based alignment to map batch source lines to current file lines
2. **Presence constraints**: Ensures batch-claimed lines are present in the result
3. **Absence constraints**: Enforces batch deletions at exact anchored boundaries

This allows batches to be applied even when your code has evolved, as long as:
- Batch-claimed lines can be structurally located
- Changes have sufficient context (surrounding lines) for alignment
- File structure hasn't changed so drastically that alignment fails

### Reversal Model (discard --from)

When discarding a batch from your working tree, the tool uses **constraint-based reversal**:

1. **Region classification**: Analyzes how batch source differs from baseline using difflib's SequenceMatcher
   - EQUAL regions: unchanged lines
   - INSERT regions: batch-added content
   - REPLACE_LINE_BY_LINE regions: same-size changes with clear 1:1 line correspondence
   - REPLACE_BY_HUNK regions: different-size changes requiring atomic restoration

2. **Presence reversal**: For each batch-owned line in the working tree:
   - EQUAL/REPLACE_LINE_BY_LINE: restore individual baseline line
   - INSERT: remove (batch-added content)
   - REPLACE_BY_HUNK: verify full ownership, then restore entire baseline block atomically

3. **Absence restoration**: Re-insert batch-deleted sequences at their original anchored boundaries

This allows batches to be cleanly removed even when working tree has diverged, as long as:
- Batch-owned content can be unambiguously identified
- Modified regions have clear correspondence OR are fully batch-owned
- Deleted content can be re-inserted at original boundaries

### Bytes-Based Correctness

All batch operations work directly with bytes, not decoded text. This ensures:
- No data corruption from encoding assumptions
- Support for non-UTF-8 files (ISO-8859-1, Windows-1252, etc.)
- Correct handling of mixed encodings within a repository
- Preservation of CRLF line endings in cross-platform workflows

---

## `new`

Create a new named batch for accumulating changes.

```
❯ git-stage-batch new batch-name
```

**With description:**
```
❯ git-stage-batch new db-updates --note "Database migration changes"
```

The batch is stored as a git ref and persists until explicitly dropped.

---

## `list`

List all existing batches with their descriptions.

```
❯ git-stage-batch list
```

**Example output:**
```
Batches:
  db-updates: Database migration changes (created 2 hours ago)
  refactor: Code cleanup (created yesterday)
```

---

## `drop`

Delete a batch and remove its git ref.

```
❯ git-stage-batch drop batch-name
```

This permanently removes the batch and all changes stored in it.

---

## `annotate`

Add or update the description for a batch.

```
❯ git-stage-batch annotate batch-name "New description"
```

Useful for updating batch metadata as you accumulate changes.

---

## `show --from BATCH`

Show the accumulated changes stored in a batch.

```
❯ git-stage-batch show --from batch-name
```

Displays the diff representing all changes accumulated in the batch, showing what would be staged or discarded if you operate on the batch.

**Line-level filtering:**
```
❯ git-stage-batch show --from batch-name --line 1,3,5-7
```

Filter the display to show only specific line IDs from the batch.

---

## `include --from BATCH`

Stage the changes from a batch to the index.

**Stage entire batch:**
```
❯ git-stage-batch include --from batch-name
```

Applies the batch's accumulated changes to the index, staging them for commit.

**Line-level staging:**
```
❯ git-stage-batch include --from batch-name --line 1-5
```

Stage only specific lines from the batch, allowing partial application of batch changes.

**File-level staging (selected file):**
```
❯ git-stage-batch include --from batch-name --file
```

Stage changes from the batch for the selected hunk's file only. Use this during a staging session when you want to pull in batch changes for the file you're reviewing, without affecting other files in the batch.

**File-level staging (specific file):**
```
❯ git-stage-batch include --from batch-name --file src/config.py
```

Stage changes from the batch for `src/config.py` only, without needing a selected hunk. Useful for applying specific files from multi-file batches outside of an active staging session.

**Example - Selective file application:**
```bash
# Create batch with changes from multiple files
❯ git-stage-batch new refactor
❯ git-stage-batch discard --to refactor --file auth.py
❯ git-stage-batch discard --to refactor --file config.py
❯ git-stage-batch discard --to refactor --file utils.py

# Later, apply only config.py changes
❯ git-stage-batch include --from refactor --file config.py
# Only config.py is staged, auth.py and utils.py remain in batch
```

!!! warning "Merge-Based Application"
    `include --from BATCH` uses structural merge to intelligently apply batch changes
    to your current working tree, even if the code has evolved since the batch was created.

    The merge succeeds when:
    - Batch-claimed lines can be unambiguously located in the current file structure
    - Changes have context (surrounding unchanged lines) for alignment

    Failures occur when:
    - The file structure has changed so drastically that batch lines cannot be located
    - Claimed lines lack sufficient context for structural alignment
    - The batch attempts to delete content that no longer exists at expected positions

    On failure, run `show --from BATCH` to review the changes, or use `--line` or
    `--file` to apply only compatible parts.

---

## `discard --from BATCH`

Remove batch changes from the working tree.

**Discard entire batch:**
```
❯ git-stage-batch discard --from batch-name
```

Removes the batch's changes from your working tree by applying the reverse of the batch's diff.

**Line-level discarding:**
```
❯ git-stage-batch discard --from batch-name --line 2,4
```

Discard only specific lines from the batch, allowing surgical removal of batch changes.

**File-level discarding (selected file):**
```
❯ git-stage-batch discard --from batch-name --file
```

Remove batch changes from the working tree for the selected hunk's file only. Use this during a staging session when you want to discard batch changes for the file you're reviewing, without affecting other files in the batch.

**File-level discarding (specific file):**
```
❯ git-stage-batch discard --from batch-name --file src/experimental.py
```

Remove batch changes for `src/experimental.py` only, without needing a selected hunk. Useful for discarding specific files from multi-file batches.

!!! warning "Destructive Operation"
    This permanently removes changes from your working tree.

!!! warning "Constraint-Based Reversal"
    `discard --from BATCH` uses structural analysis to reverse batch changes by:
    - Removing batch-added content (insertions)
    - Restoring batch-modified lines to their baseline state
    - Re-inserting batch-deleted sequences at their original boundaries

    The operation succeeds when:
    - Batch-owned content can be unambiguously identified in the current file
    - Modified regions have clear line-by-line correspondence with baseline, OR
    - Modified regions are fully batch-owned (allowing atomic restoration)

    Failures occur when:
    - Partial ownership of regions that cannot be restored line-by-line
    - File structure has changed so drastically that batch content cannot be located
    - Deleted sequences cannot be re-inserted at original anchored boundaries

    The batch itself persists - only the working tree is modified. Use `--file` to
    filter to a specific file, or `--line` to discard only specific lines.

---

## `apply --from BATCH`

Apply batch changes to the working tree without staging them.

**Apply entire batch:**
```
❯ git-stage-batch apply --from batch-name
```

Applies the batch's accumulated changes to your working tree, leaving the index untouched. This is different from `include --from` which stages changes to the index.

**Use cases:**
- Temporarily applying batched changes to test them before committing
- Restoring changes that were saved with `discard --to`
- Previewing batch changes in your working tree before staging

**Line-level application:**
```
❯ git-stage-batch apply --from batch-name --line 1-3
```

Apply only specific lines from the batch to the working tree.

**File-level application (selected file):**
```
❯ git-stage-batch apply --from batch-name --file
```

Apply batch changes to the working tree for the selected hunk's file only. Use this during a staging session when you want to preview batch changes for the file you're reviewing, without affecting other files in the batch.

**File-level application (specific file):**
```
❯ git-stage-batch apply --from batch-name --file src/debug.py
```

Apply batch changes for `src/debug.py` only to the working tree, without needing a selected hunk. Useful for testing specific files from multi-file batches.

!!! warning "Merge-Based Application"
    `apply --from BATCH` uses the same structural merge as `include --from BATCH`,
    intelligently applying batch changes even if the working tree has evolved.

    See the warning under `include --from BATCH` for details on when merge succeeds
    or fails.

    On failure, run `show --from BATCH` to review the changes, or use `--file` to
    filter to a specific file, or `--line` to apply only specific lines.

!!! info "Working Tree Only"
    Unlike `include --from`, this command modifies only the working tree and leaves
    the index (staging area) untouched. Use this when you want to preview or test
    changes before staging them.

**Example workflow:**
```bash
# Save debugging changes to a batch
❯ git-stage-batch discard --to debug

# Later, temporarily restore them to test
❯ git-stage-batch apply --from debug

# Test the code with debug output...

# Remove them again when done
❯ git restore .
```

**Example - Selective file preview:**
```bash
# Batch has changes to auth.py, config.py, utils.py
❯ git-stage-batch apply --from refactor --file auth.py
# Only auth.py changes are in working tree, others remain in batch

# Test auth.py changes...

# Restore and try a different file
❯ git restore auth.py
❯ git-stage-batch apply --from refactor --file config.py
```

---

## `reset --from BATCH`

Remove claims from a batch without changing the working tree.

**Reset entire batch:**
```
❯ git-stage-batch reset --from batch-name
```

Clears all files from the batch.

**Reset selected file:**
```
❯ git-stage-batch reset --from batch-name --file src/debug.py
```

Removes only `src/debug.py` from the batch. If `--file` is used without a path, the selected hunk's file is used.

**Reset selected lines from a file:**
```
❯ git-stage-batch reset --from batch-name --file src/debug.py --line 1,3-5
```

Removes only those line claims from the batch. Line reset is resolved from the batch's stored source commit, not from the current working tree contents.

**Split selected claims into another batch:**
```
❯ git-stage-batch reset --from batch-name --to other-batch --file src/debug.py --line 1,3-5
```

Moves the selected claims into `other-batch` and removes them from `batch-name`. If `other-batch` does not exist, it is created with the source batch's baseline so the split is independent of the current working tree or current `HEAD`.

---

## `include --to BATCH`

Include the selected hunk in a batch for later staging.

```
❯ git-stage-batch include --to batch-name
```

This captures a snapshot of the current working tree state (the **batch source**) along with ownership information for the selected lines, then marks the hunk as processed. The changes remain in your working tree and can be staged later using `include --from BATCH`.

The batch source allows later operations to intelligently merge or discard changes even if your code has evolved since the batch was created.

**Save specific lines only:**
```
❯ git-stage-batch include --to batch-name --line 1,3,5-7
```

Use `--line` to save only selected line IDs to the batch, leaving the rest for the selected session.

**Auto-creation:**
If the batch doesn't exist, it will be automatically created with the note "Auto-created".

**Use cases:**
- Deferring changes for later review while continuing to process other hunks
- Grouping related changes across multiple files for a separate commit
- Temporarily setting aside changes you're uncertain about

**Line-level saving:**
```
❯ git-stage-batch include --to batch-name --line 1,3
```

Save only specific lines to the batch, allowing fine-grained accumulation of changes.

**File-level saving:**
```
❯ git-stage-batch include --to batch-name --file
```

Save the entire selected file to the batch instead of just the selected hunk. Useful when you want to defer an entire file's changes as a unit.

---

## `discard --to BATCH`

Save the selected hunk to a batch, then discard it from the working tree.

```
❯ git-stage-batch discard --to batch-name
```

This captures a snapshot of the current working tree state (the **batch source**) along with ownership information for the selected lines, then removes the changes from your working tree. The batch acts as a backup allowing later recovery via `apply --from BATCH` or `include --from BATCH`.

**Save and discard specific lines only:**
```
❯ git-stage-batch discard --to batch-name --line 1,3,5-7
```

Use `--line` to save and discard only selected line IDs, leaving other changes in the working tree.

!!! warning "Destructive Operation"
    This removes changes from your working tree after saving them to the batch.

**Auto-creation:**
If the batch doesn't exist, it will be automatically created.

**Use cases:**
- Removing debug code while keeping it available for later
- Discarding experimental changes but preserving them for potential reuse
- Cleaning up your working tree while maintaining a safety net

**Line-level saving and discarding:**
```
❯ git-stage-batch discard --to batch-name --line 2,4-6
```

Save and discard only specific lines, preserving other changes in your working tree.

**File-level saving and discarding:**
```
❯ git-stage-batch discard --to batch-name --file
```

Save the entire selected file to the batch, then discard the entire file from the working tree. Useful when you want to completely remove a file while preserving it for potential recovery.

**Example workflow:**
```bash
# Accidentally included debug logging in your changes
❯ git-stage-batch start
❯ git-stage-batch discard --to debug-logging

# Or save only the debug print statements (lines 5-7)
❯ git-stage-batch discard --to debug-logging --line 5-7

# Later, if you need the debug code back:
❯ git-stage-batch include --from debug-logging
```

---

## Advanced Workflow: Decomposing and Recomposing History

When you have a messy working tree with multiple logical changes intertwined, you can use batches to decompose the changes into layers, create clean checkpoints, then recompose them as a series of well-organized commits.

**Strategy:**
1. Use `discard --to` to peel off the topmost logical layer
2. Edit the tree to fix dependencies (remove calls to code you just discarded)
3. Repeat for each layer, working from outside to inside
4. Apply batches back in reverse order with clear commit messages

**Example workflow:**

```bash
# Starting state: messy working tree with authentication refactor,
# new API endpoint, and database migration all mixed together

❯ git-stage-batch start

# Layer 1: Peel off the API endpoint (topmost layer, depends on auth changes)
❯ git-stage-batch discard --to api-endpoint --note "Layer 3: API endpoint (depends on layer 2: auth)"
# Tree now has auth + database changes

# Fix dependencies: remove the API route registration that depended on the endpoint
❯ $EDITOR main.py  # Remove route registration

# Layer 2: Peel off authentication refactor (depends on database schema)
❯ git-stage-batch again  # Restart to see remaining hunks
❯ git-stage-batch discard --to auth-refactor --note "Layer 2: auth refactor (depends on layer 1: database)"
# Tree now has only database changes

# Fix dependencies: remove auth code that depended on new DB columns
❯ $EDITOR auth.py  # Remove references to new columns

# Layer 3: What remains is the foundation (database migration)
❯ git-stage-batch again
❯ git-stage-batch discard --to database-migration --note "Layer 1: database foundation (no dependencies)"
# Tree is now clean (or back to original state)

# Review the decomposition
❯ git-stage-batch list
Batches:
  database-migration: Layer 1: database foundation (no dependencies) (created 2 minutes ago)
  auth-refactor: Layer 2: auth refactor (depends on layer 1: database) (created 1 minute ago)
  api-endpoint: Layer 3: API endpoint (depends on layer 2: auth) (created 30 seconds ago)

# Now recompose in dependency order (reverse of discard order)

# Step 1: Apply foundation layer
❯ git-stage-batch include --from database-migration
❯ git commit -m "database: Add user preferences table

The application stores all configuration in code, preventing users from
customizing their experience across sessions.

Users need persistent storage for individual preferences like theme choice,
language selection, and timezone settings that survive across logins.

This commit adds a preferences table with columns for theme, language, and
timezone. Includes migration script and updated schema documentation."

# Step 2: Apply authentication layer
❯ git-stage-batch include --from auth-refactor
❯ git commit -m "auth: Load user preferences during session initialization

The authentication module creates sessions but doesn't populate user preferences,
requiring separate queries throughout the application to access settings.

Users experience slower page loads as each component independently queries for
preference data instead of loading it once at authentication time.

This commit updates the auth module to read user preferences from the new table
during session creation. Preferences are cached in the session object, eliminating
redundant database queries."

# Step 3: Apply API layer
❯ git-stage-batch include --from api-endpoint
❯ git commit -m "api: Add endpoint for updating user preferences

Users can view their preferences but have no way to modify them without direct
database access, forcing administrators to handle routine preference changes.

A self-service interface is needed for users to customize their experience without
administrative intervention.

This commit adds a /api/preferences endpoint accepting PUT requests with theme,
language, and timezone fields. Integrates with the authentication system to
validate sessions and update preferences atomically."

# Clean up batches
❯ git-stage-batch drop database-migration
❯ git-stage-batch drop auth-refactor
❯ git-stage-batch drop api-endpoint

# Result: clean, logical commit history instead of one messy commit
```

**Key insights:**

- Use `--note` to document layer dependencies when creating batches
- Update notes with `annotate` if you discover dependencies later
- The batches themselves are your backup - no need for checkpoint commits
- The decomposition order is outside-in (what depends on what)
- The recomposition order is inside-out (foundations first, dependents later)
- Edit the tree between `discard --to` operations to fix broken dependencies
- This pattern is powerful for untangling complex changesets into reviewable commits

---

## Frequently Asked Questions

### How are batches different from Git stashes?

A stash saves the entire state of your working tree so you can return to it later. A batch saves a **logical change** so you can organize it into a clean commit later.

Stashes are for temporarily setting work aside. Batches are for structuring and organizing work before committing it.

With a stash, you capture everything:

```bash
git stash
```

With a batch, you capture only the parts you choose:

```bash
git-stage-batch include --to parser
git-stage-batch include --to cli
git-stage-batch include --to docs
```

Later, you can turn each batch into a commit.

---

### Why not just use `git stash`?

Stashes are snapshots of your workspace. They are not designed to organize code changes into meaningful commits.

If your working tree contains multiple logical changes, a stash will bundle them all together. Batches let you separate them as you go.

For example:

```
working tree:
  parser work
  CLI changes
  documentation updates
```

With stashes, those changes are stored together.

With batches, they can be separated:

```
parser
cli
docs
```

Each batch can later become its own commit.

---

### Can I replace stashes with batches?

No. They solve different problems.

Use stashes when you need to quickly save your working state:

```bash
git stash
git pull
git stash pop
```

Use batches when you're organizing a messy working tree into clean commits.

---

### How are batches different from commits?

A commit is permanent project history. A batch is a temporary container for changes you are still organizing.

You should think of a batch as a **draft commit**.

Example workflow:

```bash
git-stage-batch include --to parser
git-stage-batch include --to parser
git-stage-batch include --to parser

git-stage-batch include --from parser
git commit -m "Add parser implementation"
```

The batch helps assemble the commit, but it is not part of the repository history itself.

---

### Why not just commit earlier?

Sometimes your working tree contains changes that belong to different commits but are mixed together.

For example:

```
working tree:
  parser feature
  CLI integration
  documentation
  refactor
```

You could commit everything at once, but that produces messy history.

Batches let you reorganize changes into logical commits before publishing them.

---

### Are batches like temporary branches?

Not really.

Branches organize commits. Batches organize **uncommitted changes**.

A branch looks like this:

```
commit → commit → commit
```

A batch looks more like this:

```
selected hunks → staged later → commit
```

They operate at different levels of the workflow.

---

### Do batches modify my Git history?

No.

Batches are stored separately from your commit history. They only affect how you prepare commits.

Once a batch is included and committed, the batch itself can be dropped.

---

### When should I use batches?

Batches are useful when your working tree contains multiple logical changes and you want to turn them into clean commits.

Typical cases include:

* splitting a large diff into logical commits
* organizing refactors before submitting a pull request
* reconstructing history for a patch series
* preparing changes before rebasing or squashing

---

### Are batches meant to be long-lived?

No. Batches are usually short-lived.

They exist while you are organizing a set of commits and are typically dropped once the commits have been created.

---

### Do batches replace `git add -p`?

No. Batches build on the same idea.

`git add -p` lets you stage parts of a change.
Batches let you **defer and group those parts** so they can become separate commits later.

---

### Why use batches instead of staging everything immediately?

Because sometimes you do not yet know which commit a change belongs in.

Batches let you postpone that decision while still organizing the changes.

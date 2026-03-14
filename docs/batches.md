# Batch Operations

!!! info "Power User Feature"
    Batches are an advanced feature for complex workflows. Most users will not need them.
    The core commands (start, include, skip, discard) handle the majority of use cases.

Batches are named collections of accumulated changes that can be staged or discarded later as a unit. They persist across sessions and are stored as git commits under `refs/batches/<name>`.

**When to use batches:**
- Accumulating related changes across multiple hunks for review together
- Deferring changes without losing them while working on other commits
- Grouping changes by type (e.g., debugging, refactoring) for separate handling

**When NOT to use batches:**
- Simple linear workflows (just use skip and again for another pass)
- One-off staging decisions (include/skip/discard are simpler)

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

```
❯ git-stage-batch include --from batch-name
```

Applies the batch's accumulated changes to the index, staging them for commit.

**Line-level staging:**
```
❯ git-stage-batch include --from batch-name --line 1-5
```

Stage only specific lines from the batch, allowing partial application of batch changes.

**File-level staging (wholesale mode):**
```
❯ git-stage-batch include --from batch-name --file
```

Stage all files from the batch as complete file replacements rather than patches. This wholesale mode is less likely to encounter conflicts when repository state has diverged.

!!! warning "Strict Application"
    `include --from BATCH` fails if the batch's changes cannot be applied cleanly
    to the current repository state. This happens when the code has diverged from the
    baseline when the batch was created.

    On failure, run `show --from BATCH` to review the changes, or use `--line` or
    `--file` to apply only compatible parts.

---

## `discard --from BATCH`

Remove batch changes from the working tree.

```
❯ git-stage-batch discard --from batch-name
```

Removes the batch's changes from your working tree by applying the reverse of the batch's diff.

**Line-level discarding:**
```
❯ git-stage-batch discard --from batch-name --line 2,4
```

Discard only specific lines from the batch, allowing surgical removal of batch changes.

**File-level discarding (wholesale mode):**
```
❯ git-stage-batch discard --from batch-name --file
```

Restore all files in the batch to their baseline state (before batch changes were made). This wholesale mode restores complete files rather than reversing patches, making it more robust when repository state has diverged.

!!! warning "Destructive Operation"
    This permanently removes changes from your working tree.

!!! warning "Strict Reversal"
    `discard --from BATCH` fails if the batch's changes cannot be reversed cleanly.
    The batch itself persists - only the working tree is modified. Use `--file` for
    wholesale restoration when patch reversal fails, or `--line` to discard only
    compatible parts.

---

## `apply --from BATCH`

Apply batch changes to the working tree without staging them.

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

**File-level application (wholesale mode):**
```
❯ git-stage-batch apply --from batch-name --file
```

Apply all files from the batch as complete file replacements. This wholesale mode is less likely to encounter conflicts when repository state has diverged.

!!! warning "Strict Application"
    `apply --from BATCH` fails if the batch's changes cannot be applied cleanly
    to the current working tree state.

    On failure, run `show --from BATCH` to review the changes, or use `--line` or
    `--file` to apply only compatible parts.

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

---

## `skip --to BATCH`

Save the current hunk to a batch instead of just skipping it.

```
❯ git-stage-batch skip --to batch-name
```

This saves the current working tree state of the file to the batch, then marks the hunk as skipped so you can continue processing other hunks.

**Save specific lines only:**
```
❯ git-stage-batch skip --to batch-name --line 1,3,5-7
```

Use `--line` to save only selected line IDs to the batch, leaving the rest for the current session.

**Auto-creation:**
If the batch doesn't exist, it will be automatically created with the note "Auto-created".

**Use cases:**
- Deferring changes for later review while continuing to process other hunks
- Grouping related changes across multiple files for a separate commit
- Temporarily setting aside changes you're uncertain about

**Line-level saving:**
```
❯ git-stage-batch skip --to batch-name --line 1,3
```

Save only specific lines to the batch, allowing fine-grained accumulation of changes.

**File-level saving:**
```
❯ git-stage-batch skip --to batch-name --file
```

Save the entire current file to the batch instead of just the current hunk. Useful when you want to defer an entire file's changes as a unit.

---

## `discard --to BATCH`

Save the current hunk to a batch, then discard it from the working tree.

```
❯ git-stage-batch discard --to batch-name
```

This first saves the working tree state to the batch, then removes the changes from your working tree. The batch acts as a backup allowing later recovery.

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

Save the entire current file to the batch, then discard the entire file from the working tree. Useful when you want to completely remove a file while preserving it for potential recovery.

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

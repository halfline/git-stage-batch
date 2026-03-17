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

**Filter to specific lines:**
```
❯ git-stage-batch show --from batch-name --line 1,3,5-7
```

Use `--line` to display only specific line IDs from the batch.

---

## `include --from BATCH`

Stage the changes from a batch to the index.

```
❯ git-stage-batch include --from batch-name
```

Applies the batch's accumulated changes to the index, staging them for commit.

**Stage specific lines only:**
```
❯ git-stage-batch include --from batch-name --line 1,3,5-7
```

Use `--line` to stage only selected line IDs from the batch, leaving others untouched.

!!! warning "Strict Application"
    `include --from BATCH` fails if the batch's changes cannot be applied cleanly
    to the selected repository state. This happens when the code has diverged from the
    baseline when the batch was created.

    On failure, run `show --from BATCH` to review the changes.

---

## `discard --from BATCH`

Remove batch changes from the working tree.

```
❯ git-stage-batch discard --from batch-name
```

Removes the batch's changes from your working tree by applying the reverse of the batch's diff.

**Discard specific lines only:**
```
❯ git-stage-batch discard --from batch-name --line 1,3,5-7
```

Use `--line` to discard only selected line IDs from the batch, leaving others in the working tree.

!!! warning "Destructive Operation"
    This permanently removes changes from your working tree.

!!! warning "Strict Reversal"
    `discard --from BATCH` fails if the batch's changes cannot be reversed cleanly.
    The batch itself persists - only the working tree is modified.

---

## `include --to BATCH`

Include the selected hunk in a batch for later staging.

```
❯ git-stage-batch include --to batch-name
```

This saves the selected working tree state of the file to the batch and marks the hunk as processed, allowing you to continue with other hunks. The changes remain in your working tree and can be staged later using `include --from BATCH`.

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

---

## `discard --to BATCH`

Save the selected hunk to a batch, then discard it from the working tree.

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

**Example workflow:**
```bash
# Accidentally included debug logging in your changes
❯ git-stage-batch start
❯ git-stage-batch discard --to debug-logging

# Later, if you need the debug code back:
❯ git-stage-batch include --from debug-logging
```

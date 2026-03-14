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

**When NOT to use batches:**
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

**Auto-creation:**
If the batch doesn't exist, it will be automatically created with the note "Auto-created".

**Use cases:**
- Defer changes to a separate batch for later commit
- Group related hunks together for thematic commits
- Save experimental changes without committing them

---

## `apply --from BATCH`

Apply batch changes to the working tree without staging them.

Save the selected hunk to a batch instead of just skipping it.

```
❯ git-stage-batch include --to batch-name
```

This saves the selected working tree state of the file to the batch, then marks the hunk as skipped so you can continue processing other hunks.

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

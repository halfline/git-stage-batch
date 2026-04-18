# Commands Reference

Complete reference of all available commands.

## Core Operations

### `start`

Find and display the first unprocessed hunk; cache as "selected".

```
❯ git-stage-batch start
```

**Options:**
- `-U N` or `--unified N`: Number of context lines in diff output (default: 3)

```
❯ git-stage-batch start -U5  # Show 5 lines of context
```

Resets state if a session is already in progress.

---

### `show [--file [PATH]]`

Display the cached "selected" hunk or an entire file's changes.

**Show selected hunk:**
```
❯ git-stage-batch show
```

**Show all changes from selected hunk's file:**
```
❯ git-stage-batch show --file
```

**Show all changes from specific file:**
```
❯ git-stage-batch show --file src/config.py
```

When `--file` is used, displays all changes from the entire file (all hunks merged into a unified view), rather than just the selected hunk. This is useful for reviewing all changes in a file before staging or discarding them.

**Options:**
- `--file [PATH]`: Display entire file instead of single hunk
  - Without PATH: uses selected hunk's file
  - With PATH: displays specified file
- `--porcelain`: Exit silently with status code only (no output)

**Exit codes:**
- `0` if hunk/file has changes
- `1` if no changes

**Usage in scripts:**
```bash
# Check if hunk exists before processing
if git-stage-batch show --porcelain; then
    echo "Hunk available for processing"
else
    echo "No hunks remaining"
fi

# Check if a specific file has changes
if git-stage-batch show --file auth.py --porcelain; then
    echo "auth.py has changes"
fi
```

---

### `include`

Stage the cached hunk (entire hunk) to the index; advance to next.

```
❯ git-stage-batch include
```

Or use the bare command when session is active:
```
❯ git-stage-batch
```

---

### `skip`

Mark the cached hunk as skipped; advance to next.

```
❯ git-stage-batch skip
```

Skipped hunks can be revisited with `again`.

---

### `discard`

Reverse-apply the cached hunk to the working tree; advance to next.

```
❯ git-stage-batch discard
```

!!! warning "Destructive Operation"
    This permanently removes changes from your working tree. Use with caution.

---

### `status`

Show session progress and remaining hunks.

```
❯ git-stage-batch status
```

**Example output:**
```
Session: iteration 1 (in progress)

Current hunk:
  auth.py:42
  [#1-3]

Progress this iteration:
  Included:  2 hunks
  Skipped:   1 hunks
  Discarded: 0 hunks
  Remaining: ~3 hunks

Skipped hunks:
  config.py:15 [#1,3-5]
```

**Options:**
- `--porcelain`: Output in machine-readable JSON format

**Porcelain output:**
```bash
❯ git-stage-batch status --porcelain
```

Outputs JSON with stable fields for script integration:
```json
{
  "session": {
    "iteration": 1,
    "in_progress": true
  },
  "selected": {
    "file": "auth.py",
    "line": 42,
    "ids": [1, 2, 3]
  },
  "progress": {
    "included": 2,
    "skipped": 1,
    "discarded": 0,
    "remaining": 3
  },
  "skipped_hunks": [
    {
      "file": "config.py",
      "line": 15,
      "ids": [1, 3, 4, 5]
    }
  ]
}
```

---

## Session Management

### `again`

Clear the blocklist and restart iteration through all hunks.

```
❯ git-stage-batch again
```

Useful for making another pass after committing some changes.

---

### `stop`

End the selected session and remove all state.

```
❯ git-stage-batch stop
```

---

### `abort`

Undo all changes made during the session, including commits and discards.

```
❯ git-stage-batch abort
```

This:
- Resets HEAD to where you started
- Restores your original working tree
- Restores batch state (drops created batches, restores dropped/mutated batches)
- Removes session state

!!! warning "Undo Commits"
    This will undo any commits made during the session. Make sure you want to discard all work before running abort.

---

### `undo`

Undo the most recent undoable session operation, restoring the repository
to its state before that operation.

```
❯ git-stage-batch undo
```

**Options:**
- `--force`: Overwrite changes made after the undo checkpoint

Refuses by default if the current state has changed since the checkpoint.

---

### `redo`

Redo the most recently undone session operation.

```
❯ git-stage-batch redo
```

**Options:**
- `--force`: Overwrite changes made after the undo

Refuses by default if the current state has changed since the undo.

Multiple undo/redo works in editor order:

```bash
# do A, do B, do C
❯ git-stage-batch undo      # removes C, redo stack: C
❯ git-stage-batch undo      # removes B, redo stack: B, C
❯ git-stage-batch redo      # reapplies B, redo stack: C
❯ git-stage-batch redo      # reapplies C, redo stack empty
```

A new undoable operation after undo clears the redo stack.

---

## File-Level Operations

### `include --file [PATH]`

Stage all hunks from a file.

**Stage selected hunk's file:**
```
❯ git-stage-batch include --file
```

**Stage specific file by path:**
```
❯ git-stage-batch include --file src/auth.py
```

Stages all hunks from the specified file and advances to the next file. When a path is provided, you can stage any file in your working tree regardless of which file the selected hunk is from.

**Use cases:**
- `--file` (no path): Stage all hunks from the file of the selected hunk
- `--file PATH`: Stage all hunks from the specified file, even if it's not the selected file

**Example workflow:**
```bash
❯ git-stage-batch start
# Current hunk is from config.py

# Stage a different file without changing selected position
❯ git-stage-batch include --file auth.py
# auth.py is now fully staged, selected hunk still from config.py

# Continue with selected file
❯ git-stage-batch include
```

---

### `skip --file`

Skip all hunks from the selected file.

```
❯ git-stage-batch skip --file
```

All hunks from the file are marked as skipped and can be revisited with `again`.

---

### `discard --file [PATH]`

Discard entire file from the working tree.

**Discard selected hunk's file:**
```
❯ git-stage-batch discard --file
```

**Discard specific file by path:**
```
❯ git-stage-batch discard --file src/debug.py
```

Removes all changes from the specified file. When a path is provided, you can discard any file in your working tree regardless of which file the selected hunk is from.

**Use cases:**
- `--file` (no path): Discard the entire file of the selected hunk
- `--file PATH`: Discard the specified file, even if it's not the selected file

!!! warning "Destructive Operation"
    This permanently removes the entire file from your working tree.

---

## Permanent File Exclusion

### `block-file`

Permanently exclude a file from all future sessions.

```
❯ git-stage-batch block-file
```

This:
- Adds the selected file to `.gitignore`
- Marks it as blocked in session state
- Skips all its hunks automatically

When run without a selected hunk, you can specify the file path:

```
❯ git-stage-batch block-file path/to/file.txt
```

Useful for build artifacts, IDE files, or other generated content.

---

### `unblock-file`

Remove a file from the blocked list.

```
❯ git-stage-batch unblock-file path/to/file.txt
```

This:
- Removes the file from `.gitignore`
- Removes it from the blocked files list
- Allows its hunks to be processed again

---

## Line-Level Operations

Work with individual lines within a hunk for maximum granularity.

### `include --line LINE_IDS`

Stage only specific lines from the selected hunk.

```
❯ git-stage-batch include --line 1,3,5-7
```

**Line ID syntax:**
- Single: `1`
- Multiple: `1,3,5`
- Range: `5-7`
- Combined: `1,3,5-7`

Lines are displayed with IDs in brackets when you run `show` or `start`:

```
auth.py :: @@ -10,5 +10,5 @@
[#1] - old_function()
[#2] + new_function()
[#3] + another_change()
      context_line()
```

To stage lines 1 and 3:
```
❯ git-stage-batch include --line 1,3
```

After processing, the hunk is recalculated to show remaining changes.

---

### `skip --line LINE_IDS`

Mark specific lines as skipped without staging them.

```
❯ git-stage-batch skip --line 2
```

Useful when you want to defer certain changes to a later commit.

---

### `discard --line LINE_IDS`

Remove specific lines from the working tree.

```
❯ git-stage-batch discard --line 3
```

!!! warning "Destructive Operation"
    This permanently removes the specified lines from your working tree.

Line-level discard allows surgical removal of debug code, experimental changes, or unwanted modifications while keeping the rest of the hunk.

---

## Fixup Suggestions

### `suggest-fixup`

Suggest which commit the selected hunk should be fixed up to.

```
❯ git-stage-batch suggest-fixup [BOUNDARY]
```

Finds commits that previously modified the lines in the selected hunk and suggests them as fixup targets. Iteratively shows candidates starting from most recent, progressing backwards with each invocation.

**Arguments:**
- `BOUNDARY`: Lower bound for commit search (default: `@{upstream}`)

**Options:**
- `--reset`: Start over from the most recent candidate
- `--abort`: Clear state and exit
- `--last`: Re-show the last candidate without advancing
- `--porcelain`: Output in machine-readable JSON format

**Example workflow:**
```bash
# Make changes to existing code
❯ git-stage-batch start

# Find which commit to fixup (searches back to upstream by default)
❯ git-stage-batch suggest-fixup
Candidate 1: a1b2c3d Fix authentication logic

# Not the right commit, try next
❯ git-stage-batch suggest-fixup
Candidate 2: e4f5g6h Add user validation

# This is the one! Create fixup commit
❯ git commit --fixup=e4f5g6h

# Or specify a different boundary for the search
❯ git-stage-batch suggest-fixup main
Candidate 1: a1b2c3d Fix authentication logic
```

The command uses `git log -L` to find commits that touched the affected lines, making it easy to create fixup commits for amendment during interactive rebase.

**Porcelain output:**
```bash
❯ git-stage-batch suggest-fixup --porcelain
```

Outputs JSON with stable fields for script integration:
```json
{
  "candidate": {
    "hash": "a1b2c3d",
    "full_hash": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0",
    "subject": "Fix authentication logic",
    "author": "John Doe",
    "date": "2026-03-01T10:30:00-05:00",
    "relative_date": "2 weeks ago"
  },
  "iteration": 1,
  "boundary": "@{upstream}"
}
```

**Automated fixup example:**
```bash
# Get fixup candidate programmatically
CANDIDATE=$(git-stage-batch suggest-fixup --porcelain | jq -r '.candidate.hash')

# Create fixup commit automatically
if [ -n "$CANDIDATE" ]; then
  git commit --fixup=$CANDIDATE
fi
```

---

### Line-level fixup suggestions

Suggest fixup target for specific lines only.

```
❯ git-stage-batch suggest-fixup [BOUNDARY] --line LINE_IDS
```

**Example:**
```
❯ git-stage-batch suggest-fixup --line 1,3
❯ git-stage-batch suggest-fixup main --line 1,3
```

Useful when a hunk contains changes to multiple unrelated areas. You can get separate fixup suggestions for different line ranges within the same hunk.

---

---

## Batch Operations

### `sift`

Reconcile a batch against the current tip by removing portions whose effect is already present.

```
❯ git-stage-batch sift --from OLD_BATCH --to NEW_BATCH
```

**Required arguments:**
- `--from BATCH`: Source batch to sift
- `--to BATCH`: Destination batch (may equal `--from` for in-place sift)

**Purpose:**

After ad hoc history surgery, some parts of a batch may already have landed in history while other parts are still unapplied. `sift` removes the already-present portions and writes the remaining unapplied portion to the destination batch.

**Examples:**

```bash
# Sift to a new batch
❯ git-stage-batch sift --from feature-cleanups --to feature-cleanups-pruned

# In-place sift
❯ git-stage-batch sift --from feature-cleanups --to feature-cleanups
```

**Output:**

Shows summary of:
- Source and destination batch names
- Number of files processed
- Number of files removed (already present at tip)
- Number of files retained (still needed)

**Behavior:**

- Does not modify working tree or staging area
- Preserves source batch when `--from != --to`
- Performs atomic in-place rewrite when `--from == --to`
- Creates destination batch if needed (using source baseline)
- Fails if destination exists (except for in-place mode)

---

## Workflow Example

```bash
# Make some changes to multiple files
echo "feature 1" >> file1.txt
echo "feature 2" >> file2.txt
echo "debug code" >> file1.txt

# Start staging session
❯ git-stage-batch start

# Include first hunk (feature 1)
❯ git-stage-batch include
❯ git commit -m "Add feature 1"

# Discard debug code
❯ git-stage-batch discard

# Include feature 2
❯ git-stage-batch include
❯ git commit -m "Add feature 2"

# Check if anything remains
❯ git-stage-batch status
```

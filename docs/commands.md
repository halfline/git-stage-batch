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

### `show`

Reprint the cached "selected" hunk.

```
❯ git-stage-batch show
```

Exit codes:
- `0` if hunk exists
- `1` if no hunk

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
Session active
Processed: 3 hunks
Remaining: 2 hunks
Current file: auth.py
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
- Removes session state

!!! warning "Undo Commits"
    This will undo any commits made during the session. Make sure you want to discard all work before running abort.

---

## File-Level Operations

### `include --file`

Stage all hunks from the selected file.

```
❯ git-stage-batch include --file
```

Advances to the next file after staging all hunks.

---

### `skip --file`

Skip all hunks from the selected file.

```
❯ git-stage-batch skip --file
```

All hunks from the file are marked as skipped and can be revisited with `again`.

---

### `discard --file`

Discard the entire selected file from the working tree.

```
❯ git-stage-batch discard --file
```

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

---

*More features and operations documented as they are implemented.*

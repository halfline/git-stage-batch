# Commands Reference

Complete reference of all available commands.

## Core Operations

### `start`

Find and display the first unprocessed hunk; cache as "current".

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

Reprint the cached "current" hunk.

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
  "current": {
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

End the current session and remove all state.

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

Stage all hunks from the current file.

```
❯ git-stage-batch include --file
```

Advances to the next file after staging all hunks.

---

### `skip --file`

Skip all hunks from the current file.

```
❯ git-stage-batch skip --file
```

All hunks from the file are marked as skipped and can be revisited with `again`.

---

### `discard --file`

Discard the entire current file from the working tree.

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
- Adds the current file to `.gitignore`
- Marks it as blocked in session state
- Skips all its hunks automatically

When run without a current hunk, you can specify the file path:

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

Stage only specific lines from the current hunk.

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

# Commands Reference

Complete reference of all available commands and their options.

## Core Operations

### `start`

Find and display the first unprocessed hunk; cache as "current".

```bash
git-stage-batch start
```

**Options:**
- `-U N` or `--unified N`: Number of context lines in diff output (default: 3)

```bash
git-stage-batch start -U5  # Show 5 lines of context
```

Resets state if a session is already in progress.

**Example output:**
```
auth.py :: @@ -10,5 +10,5 @@
[#1] - old_function()
[#2] + new_function()
      context_line()
```

---

### `show` (alias: `sh`)

Reprint the cached "current" hunk with line IDs.

```bash
git-stage-batch show
```

**Porcelain mode:**
```bash
git-stage-batch show --porcelain
```

Exit codes:
- `0` if hunk exists
- `1` if no hunk

---

### `include` (alias: `i`)

Stage the cached hunk (entire hunk) to the index; advance to next.

```bash
git-stage-batch include
```

Or use the bare command when session is active:
```bash
git-stage-batch
```

---

### `skip` (alias: `s`)

Mark the cached hunk as skipped; advance to next.

```bash
git-stage-batch skip
```

Skipped hunks can be revisited with `again`.

---

### `discard` (alias: `d`)

Reverse-apply the cached hunk to the working tree; advance to next.

```bash
git-stage-batch discard
```

!!! warning "Destructive Operation"
    This permanently removes changes from your working tree. Use with caution.

---

### `status` (alias: `st`)

Show session progress: iteration number, current location, and progress metrics.

```bash
git-stage-batch status
```

**Example output:**
```
Session: iteration 1 (in progress)

Current hunk:
  auth.py:10
  [#1-2]

Progress this iteration:
  Included:  2 hunks
  Skipped:   1 hunks
  Discarded: 0 hunks
  Remaining: ~3 hunks

Skipped hunks:
  config.py:20 [#1]
```

**Porcelain mode:**
```bash
git-stage-batch status --porcelain
```

Returns JSON:
```json
{
  "session": {
    "iteration": 1,
    "in_progress": true
  },
  "current": {
    "file": "auth.py",
    "line": 10,
    "ids": [1, 2]
  },
  "progress": {
    "included": 2,
    "skipped": 1,
    "discarded": 0,
    "remaining": 3
  },
  "skipped_hunks": [
    {
      "hash": "abc123...",
      "file": "config.py",
      "line": 20,
      "ids": [1]
    }
  ]
}
```

## Line-Level Operations

### `include-line IDS` (alias: `il`)

Stage ONLY the listed changed line IDs (+/-) to the index.

```bash
git-stage-batch include-line 1,3,5-7
```

Line IDs are shown as `[#N]` in the hunk output.

**Supports:**
- Individual IDs: `1,3,5`
- Ranges: `1-5`
- Mixed: `1,3,5-7,10`

---

### `skip-line IDS` (alias: `sl`)

Mark ONLY the listed changed line IDs as skipped.

```bash
git-stage-batch skip-line 2,4
```

---

### `discard-line IDS` (alias: `dl`)

Remove ONLY the listed changed line IDs from working tree.

```bash
git-stage-batch discard-line 8-10
```

!!! warning "Destructive Operation"
    This permanently removes specific lines from your working tree.

## File-Level Operations

### `include-file` (alias: `if`)

Stage the entire file containing the current hunk.

```bash
git-stage-batch include-file
```

All remaining hunks in the file are staged and marked as processed.

---

### `skip-file` (alias: `sf`)

Skip all hunks in the file containing the current hunk.

```bash
git-stage-batch skip-file
```

---

### `block-file [PATH]` (alias: `b`)

Permanently exclude a file via .gitignore.

```bash
# Block current hunk's file
git-stage-batch block-file

# Block specific file
git-stage-batch block-file path/to/file.txt
```

Adds the file to `.gitignore` with a marker comment and to the internal blocked list.

---

### `unblock-file PATH` (alias: `ub`)

Remove a file from permanent exclusion.

```bash
git-stage-batch unblock-file path/to/file.txt
```

Removes the file from `.gitignore` (if marked by git-stage-batch) and from the blocked list.

## Session Management

### `again` (alias: `a`)

Clear state and immediately start a fresh pass through all hunks.

```bash
git-stage-batch again
```

Reviews all previously skipped and unprocessed hunks.

**Typical workflow:**
```bash
# First pass - include some hunks, skip others
git-stage-batch start
git-stage-batch i
git-stage-batch s
git-stage-batch i

# Commit first batch
git commit -m "First feature"

# Second pass - review skipped hunks
git-stage-batch again
```

---

### `stop`

Clear all state (blocklist and cached hunk).

```bash
git-stage-batch stop
```

Removes all tracking of processed/skipped hunks. Use this to start completely fresh or when abandoning a staging session.

## Special Behavior

### No Command (Bare Invocation)

When a session is active, running `git-stage-batch` with no command defaults to `include`:

```bash
# With active session
git-stage-batch
# Equivalent to: git-stage-batch include
```

Without an active session:
```
No batch staging session in progress.
Run 'git-stage-batch start' to begin.
```

## Global Options

### `--version`

Show version information.

```bash
git-stage-batch --version
```

### `--interactive`

Enter interactive mode (process hunks one by one with prompts).

```bash
git-stage-batch --interactive
```

[Learn more about interactive mode →](interactive.md)

## Exit Codes

- `0` - Success
- `1` - Error (invalid arguments, no hunk available, etc.)
- `2` - No pending hunks (from `start` command)

## Line ID Syntax

Line IDs support:
- **Individual IDs:** `1,3,5`
- **Ranges:** `1-5` expands to `1,2,3,4,5`
- **Mixed:** `1,3,5-7` expands to `1,3,5,6,7`

Whitespace is ignored: `1, 3, 5-7` works the same as `1,3,5-7`.

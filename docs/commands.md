# Commands Reference

Complete reference of all available commands and their options.

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

```
❯ git-stage-batch show
```

**Porcelain mode:**
```
❯ git-stage-batch show --porcelain
```

Exit codes:
- `0` if hunk exists
- `1` if no hunk

---

### `include` (alias: `i`)

Stage the cached hunk (entire hunk) to the index; advance to next.

```
❯ git-stage-batch include
```

Or use the bare command when session is active:
```
❯ git-stage-batch
```

---

### `skip` (alias: `s`)

Mark the cached hunk as skipped; advance to next.

```
❯ git-stage-batch skip
```

Skipped hunks can be revisited with `again`.

---

### `discard` (alias: `d`)

Reverse-apply the cached hunk to the working tree; advance to next.

```
❯ git-stage-batch discard
```

!!! warning "Destructive Operation"
    This permanently removes changes from your working tree. Use with caution.

---

### `status` (alias: `st`)

Show session progress: iteration number, current location, and progress metrics.

```
❯ git-stage-batch status
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
```
❯ git-stage-batch status --porcelain
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

<div class="section-separator"></div>

## Line-Level Operations

### `include --line IDS` (alias: `il`)

Stage ONLY the listed changed line IDs (+/-) to the index.

```
❯ git-stage-batch include --line 1,3,5-7
❯ git-stage-batch il 1,3,5-7  # Short alias
```

Line IDs are shown as `[#N]` in the hunk output.

**Supports:**
- Individual IDs: `1,3,5`
- Ranges: `1-5`
- Mixed: `1,3,5-7,10`

---

### `skip --line IDS` (alias: `sl`)

Mark ONLY the listed changed line IDs as skipped.

```
❯ git-stage-batch skip --line 2,4
❯ git-stage-batch sl 2,4  # Short alias
```

---

### `discard --line IDS` (alias: `dl`)

Remove ONLY the listed changed line IDs from working tree.

```
❯ git-stage-batch discard --line 8-10
❯ git-stage-batch dl 8-10  # Short alias
```

!!! warning "Destructive Operation"
    This permanently removes specific lines from your working tree.

<div class="section-separator"></div>

## File-Level Operations

### `include --file` (alias: `if`)

Stage the entire file containing the current hunk.

```
❯ git-stage-batch include --file
❯ git-stage-batch if  # Short alias
```

All remaining hunks in the file are staged and marked as processed.

---

### `skip --file` (alias: `sf`)

Skip all hunks in the file containing the current hunk.

```
❯ git-stage-batch skip --file
❯ git-stage-batch sf  # Short alias
```

---

### `block-file [PATH]` (alias: `b`)

Permanently exclude a file via .gitignore.

```
# Block current hunk's file
❯ git-stage-batch block-file

# Block specific file
❯ git-stage-batch block-file path/to/file.txt
```

Adds the file to `.gitignore` with a marker comment and to the internal blocked list.

---

### `unblock-file PATH` (alias: `ub`)

Remove a file from permanent exclusion.

```
❯ git-stage-batch unblock-file path/to/file.txt
```

Removes the file from `.gitignore` (if marked by git-stage-batch) and from the blocked list.

<div class="section-separator"></div>

## Session Management

### `again` (alias: `a`)

Clear state and immediately start a fresh pass through all hunks.

```
❯ git-stage-batch again
```

Reviews all previously skipped and unprocessed hunks.

**Typical workflow:**
```
# First pass - include some hunks, skip others
git-stage-batch start
git-stage-batch i
git-stage-batch s
git-stage-batch i

# Commit first batch
❯ git commit -m "First feature"

# Second pass - review skipped hunks
git-stage-batch again
```

---

### `stop`

Clear all state (blocklist and cached hunk).

```
❯ git-stage-batch stop
```

Removes all tracking of processed/skipped hunks. Use this to start completely fresh or when abandoning a staging session.

<div class="section-separator"></div>

## Fixup Suggestions

### `suggest-fixup` (alias: `x`)

Iteratively suggest which commits the current hunk should be fixed up to, starting with the most recent and progressing backwards through history.

```
❯ git-stage-batch suggest-fixup [BOUNDARY]
❯ git-stage-batch x  # Short alias
```

**Arguments:**
- `BOUNDARY`: Git ref to use as lower bound for commit search (default: `@{upstream}` on first call, or continues from previous call)

**Flags:**
- `--reset`: Reset iteration and start over from the most recent commit
- `--abort`: Clear iteration state and exit (doesn't show any candidates)
- `--last`: Re-show the last candidate without advancing
- `--line IDS`: Analyze only specific line IDs (e.g., `1,3,5-7`)

**How it works:**

Uses `git log -L` to find commits in the range `BOUNDARY..HEAD` that modified the lines being changed. Each invocation shows the next older commit, allowing you to iterate through all candidates until you find the right one.

**Typical workflow:**

```
❯ git-stage-batch start
auth.py :: @@ -10,5 +10,5 @@
[#1] - old_hash_function()
[#2] + new_hash_function()
      validate_user()

# First call - specify boundary
❯ git-stage-batch suggest-fixup origin/main
Candidate 1: a1b2c3d auth: Implement new hashing

diff --git a/auth.py b/auth.py
...
Run: git commit --fixup=a1b2c3d

# Not the right commit? Call again (no boundary needed)
❯ git-stage-batch suggest-fixup
Candidate 2: b2c3d4e auth: Add password validation

diff --git a/auth.py b/auth.py
...
Run: git commit --fixup=b2c3d4e

# That's the one! But want to review the diff again?
❯ git-stage-batch suggest-fixup --last
Candidate 2: b2c3d4e auth: Add password validation
...

# Continue iterating
❯ git-stage-batch suggest-fixup
Candidate 3: c3d4e5f auth: Initial implementation
...

# No more candidates
❯ git-stage-batch suggest-fixup
No more candidates found.
```

**State management:**

- **First call**: Specify the boundary (e.g., `origin/main`)
- **Subsequent calls**: Omit the boundary to continue with the same one
- **Changing boundary**: Providing a different boundary auto-resets (like `--reset`)
- **State auto-resets**: When you switch hunks, the iteration starts over

**Advanced usage:**

```
# Line-specific analysis
❯ git-stage-batch suggest-fixup --line 1-3
Candidate 1: ...

# Reset and start over
❯ git-stage-batch suggest-fixup --reset

# Different boundary
❯ git-stage-batch suggest-fixup main  # Auto-resets

# Clear state without showing candidates
❯ git-stage-batch suggest-fixup --abort
```

**Use case:**

Perfect for creating fixup commits during feature branch development. When you notice bugs or improvements in recently-committed code, use this to find which commit to fixup. The iterative approach is especially useful when multiple commits modified the same lines - you can review each candidate until you find the right one.

After creating fixup commits, use `git rebase -i --autosquash` to automatically squash them into the correct commits.

## Special Behavior

### No Command (Bare Invocation)

When a session is active, running `git-stage-batch` with no command defaults to `include`:

```
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

```
❯ git-stage-batch --version
```

### `--interactive`

Enter interactive mode (process hunks one by one with prompts).

```
❯ git-stage-batch --interactive
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

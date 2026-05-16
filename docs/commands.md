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
- `--auto-advance`: Select the next hunk after later actions (default)
- `--no-auto-advance`: Leave no hunk selected after later actions

```
❯ git-stage-batch start -U5  # Show 5 lines of context
❯ git-stage-batch start --no-auto-advance
```

Resets state if a session is already in progress.

---

### `show [--file [PATH] | --files PATTERN...]`

Display the cached "selected" hunk, one file review, or a matched-files list.

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

**Show a review page from a file:**
```bash
❯ git-stage-batch show --file src/config.py --page 2
❯ git-stage-batch show --file src/config.py --page 3-4
❯ git-stage-batch show --file src/config.py --page all
❯ git-stage-batch show --file src/config.py --pages 1,3,5
```

**Show files matched by Git-style patterns:**
```bash
❯ git-stage-batch show --files "src/**/*.py" "!src/vendor/**"
```

**Show files in a batch:**
```bash
❯ git-stage-batch show --from cleanup-ui
❯ git-stage-batch show --from cleanup-ui --file src/config.py --page 2
❯ git-stage-batch show --from cleanup-ui --file src/config.py --line 3-5 --as "replacement"
```

When `--file` is used, `show` displays a structured file review with global line IDs, page orientation, and exact follow-up commands. By default, large file reviews are bounded to the first relevant page. Use `--page all` or `--pages all` to review the whole file.

When only part of a file review has been shown, unqualified actions such as `include`, `skip`, and `discard` refuse. Use one of the shown pathless `--line` selections for a complete change, show the page range that covers the complete change, or use `--file PATH` for the whole file.

When `--files` resolves to multiple files, `show` prints a matched-files list with per-file change counts, changed-line counts, review page counts, and exact `show --file PATH` commands. This list is navigational: it does not select a hidden file for later bare actions.

For multi-file batches, `show --from BATCH` uses the same matched-files list and repeats `--from BATCH` in the suggested open commands.

When an `apply --from BATCH` or `include --from BATCH` operation cannot safely
choose between multiple structural placements, the mutating command refuses
without changing the working tree or index and points to operation-specific
candidate previews. Candidate selectors use `BATCH:apply`, `BATCH:apply:N`,
`BATCH:include`, or `BATCH:include:N`. `BATCH:apply` and `BATCH:include`
show a compact candidate overview with local context and exact commands.
Append `:N` to show the full diff for one numbered candidate. Bare numeric
selectors such as
`BATCH:2` are invalid because apply and include can have different candidate
spaces.

```bash
❯ git-stage-batch show --from cleanup-ui:apply --file src/config.py
❯ git-stage-batch show --from cleanup-ui:apply:2 --file src/config.py
❯ git-stage-batch apply --from cleanup-ui:apply:2 --file src/config.py

❯ git-stage-batch show --from cleanup-ui:include --file src/config.py
❯ git-stage-batch show --from cleanup-ui:include:2 --file src/config.py
❯ git-stage-batch include --from cleanup-ui:include:2 --file src/config.py
```

Candidate execution requires a matching prior preview for the same file and
selector. A candidate overview counts as review for the candidates it shows,
so users can run a listed apply or include command directly from that summary.
Re-preview after editing the target file or changing the index.

Submodule pointer changes are shown as atomic entries. They support whole-entry
actions, but not `--line`.

**Options:**
- `--file [PATH]`: Display entire file instead of single hunk
  - Without PATH: uses selected hunk's file
  - With PATH: displays specified file
- `--files PATTERN...`: Resolve one or more Git-style patterns to files
  - Patterns follow Git's ignore matcher semantics, including `*`, `**`, `?`, character classes, and ordered `!` exclusions
  - Resolution is performed against the current changed-file set
  - `--file` and `--files` are mutually exclusive
- `--page PAGES`, `--pages PAGES`: Show file-review pages, such as `2`, `3-4`, `1,3,5-7`, or `all`
  - Requires `--file`, or `--files` resolving to exactly one changed file
  - Cannot be combined with `--line`, multiple resolved `--files` matches, or `--porcelain`
- `--porcelain`: Exit silently with status code only (no output)
- `--from BATCH`: Show changes from a batch instead of live file-vs-HEAD changes
- `--as TEXT`, `--as-stdin`: With `--from BATCH --line IDS`, preview the same replacement batch view used by `include --from BATCH --line IDS --as ...` without mutating anything

When `--files` resolves to one file, `show` opens that single file review directly. When it resolves to multiple files, open one listed file with `show --file PATH` before using pathless `--line` actions.

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

Stage the cached hunk (entire hunk) to the index; advance to next unless
automatic selection is disabled.

```
❯ git-stage-batch include
```

---

When a session is active, the bare command shows the selected hunk:
```
❯ git-stage-batch
```

### `skip`

Mark the cached hunk as skipped; advance to next unless automatic selection is
disabled.

```
❯ git-stage-batch skip
```

Skipped hunks can be revisited with `again`.

---

### `discard`

Reverse-apply the cached hunk to the working tree; advance to next unless
automatic selection is disabled.

```
❯ git-stage-batch discard
```

!!! warning "Destructive Operation"
    This permanently removes changes from your working tree. Use with caution.

---

### Automatic Hunk Selection

By default, `include`, `skip`, `discard`, `include --to`, and `discard --to`
select and display the next hunk after they finish. Add
`--no-auto-advance` to one action when you want the command to stop with no
hunk selected:

```bash
❯ git-stage-batch include --no-auto-advance
❯ git-stage-batch show
```

After `--no-auto-advance`, another bare action refuses until `show` selects
the next hunk. Use `--auto-advance` to opt back in for one command.

`start` and `again` accept the same flags to set the session default for
later actions that do not specify either flag.

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
- `--for-prompt[=FORMAT]`: Print a prompt segment only when a session is active

**Porcelain output:**
```bash
❯ git-stage-batch status --porcelain
```

Outputs JSON with stable fields for script integration:
```json
{
  "session": {
    "active": true,
    "iteration": 1,
    "status": "in_progress",
    "in_progress": true
  },
  "selected_change": {
    "kind": "hunk",
    "file": "auth.py",
    "line": 42,
    "ids": [1, 2, 3]
  },
  "file_review": null,
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

**Prompt output:**
```bash
PS1=$PS1'\r$(__git_ps1 "\n╎\e[32m%s$(git-stage-batch status --for-prompt=\|{status}\ {processed}/{total})\e[0m")\n'
```

When no session is active, `--for-prompt` prints nothing, so any spacing or
brackets included in `FORMAT` are hidden too. Without `FORMAT`, it prints
`STAGING`. In prompt output, `{status}` is the operation name `STAGING`;
`{progress_status}` exposes the underlying `in_progress` or `complete` state.
Format fields include `{status}`, `{status_label}`, `{progress_status}`,
`{progress_label}`, `{iteration}`, `{processed}`, `{total}`, `{included}`,
`{skipped}`, `{discarded}`, `{remaining}`, `{selected_file}`,
`{selected_line}`, `{selected_ids}`, and `{selected_kind}`. `{processed}` is
`included + skipped + discarded`; `{total}` is `{processed} + remaining`.

---

## Session Management

### `again`

Clear the blocklist and restart iteration through all hunks.

```
❯ git-stage-batch again
```

Useful for making another pass after committing some changes.

**Options:**
- `--auto-advance`: Select the next hunk after later actions
- `--no-auto-advance`: Leave no hunk selected after later actions

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
- `--files PATTERN...`: Stage all hunks from files matched by Git-style patterns

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

**Pattern-based staging:**
```bash
❯ git-stage-batch include --files "src/**/*.py" "!src/vendor/**"
```

---

### `skip --file [PATH]`

Skip all hunks from a file.

**Skip selected hunk's file:**
```
❯ git-stage-batch skip --file
```

**Skip specific file by path:**
```
❯ git-stage-batch skip --file src/debug.py
```

All hunks from the file are marked as skipped and can be revisited with `again`.

**Skip files by pattern:**
```bash
❯ git-stage-batch skip --files "docs/**/*.md" "scripts/*.sh"
```

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
- `--files PATTERN...`: Discard all matched files as complete units

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

For simple replacement regions, selecting the matching deleted and added
lines stages the semantic replacement row. For example, in a hunk like:

```
[#1] - a
[#2] - b
[#3] + A
[#4] + B
```

`include --line 1,3` stages `a` -> `A` while leaving `b` unchanged.
If git-stage-batch cannot determine a clear semantic replacement, it falls
back to the regular line-level staging behavior.

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

### Replacement text with `--as`

`include --line ... --as TEXT` stages replacement text for the selected line
region instead of staging the working-tree text directly.
`include --file PATH --as TEXT` stages `TEXT` as the full index content for
that file while leaving the working tree unchanged.
`discard --file PATH --as TEXT` replaces the working-tree content for that
file-scoped path with `TEXT` without staging it.
`discard --to BATCH --line ... --as TEXT` saves replacement text to the batch
and removes the original selected lines from the working tree.

For line-scoped replacement workflows, `--as` now trims exact unchanged lines
that overlap the preserved file context immediately before or after the
selected span. Pass `--no-edge-overlap` to keep those edge-overlap lines
literally.

If the replacement text should come from a file or another command exactly,
use `--as-stdin` instead of shell command substitution. For example:

```bash
❯ git-stage-batch include --file path.txt --as-stdin < replacement.txt
❯ some-command | git-stage-batch include --line 1-3 --as-stdin
❯ git-stage-batch discard --file path.txt --as-stdin < replacement.txt
❯ some-command | git-stage-batch discard --to batch --line 1-3 --as-stdin
❯ git-stage-batch include --line 1-3 --as 'keep1\nstaged\nkeep4' --no-edge-overlap
```

Unlike `--as "$(cat replacement.txt)"`, `--as-stdin` preserves trailing
newlines exactly.

For saved batches, `show --from BATCH --file PATH --line IDS --as TEXT` previews
the replacement batch view without staging or writing it. The corresponding
mutating command is `include --from BATCH --file PATH --line IDS --as TEXT`.

These replacement workflows require one contiguous selected line-ID span.
Selections such as `1-4` or `2,3,4` are accepted because they resolve to one
continuous gutter-ID range. Selections such as `1-2,5-6` are rejected because
they pick multiple disjoint ranges.

In ordinary hunk views, that usually means replacing one displayed changed
region. File-scoped views are more nuanced: they can concatenate multiple real
hunks into one display and insert omitted gap markers between them. In that
mode, one contiguous gutter-ID span may cross those omitted gaps and replace
the full underlying file span from the first selected changed line to the last
selected changed line.

For example, if a file-scoped view shows three changed regions with IDs
`1-2`, `3-4`, and `5-6`, then this is allowed:

```bash
❯ git-stage-batch include --line 1-6 --as '...'
```

But this still requires separate commands because the selected IDs are not one
contiguous span:

```bash
❯ git-stage-batch include --line 1-2 --as '...'
❯ git-stage-batch include --line 5-6 --as '...'
```

The same rule applies to `discard --to BATCH --line ... --as TEXT`.

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

## Assistant Assets

### `install-assets [{claude-agents|claude-skills|codex-skills}] [--filter PATTERN...] [--force]`

Install bundled assistant assets into the current repository.

```bash
❯ git-stage-batch install-assets
❯ git-stage-batch install-assets claude-skills
```

This writes bundled assistant assets into the repository root so the target
assistant can discover them automatically.

- `claude-agents` installs into `.claude/agents/`
- `claude-skills` installs into `.claude/skills/`
  - required bundled Claude agents for those skills are installed too
- `codex-skills` installs into `.agents/skills/` and `.codex/config.toml`

**Install matching assets only:**
```bash
❯ git-stage-batch install-assets --filter 'commit-*'
❯ git-stage-batch install-assets claude-agents --filter 'commit-*'
❯ git-stage-batch install-assets claude-skills --filter 'commit-*'
❯ git-stage-batch install-assets codex-skills --filter 'commit-*' 'squash-*'
```

**Options:**
- `GROUP`: Optionally restrict installation to one bundled asset group
- `--filter PATTERN...`: Install only bundled assets whose entry names match one or more gitignore-style patterns
  - When omitted, installs every bundled asset in the selected group, or in every group if no group is provided
- `--force`: Overwrite an existing installed asset
  - For `codex-skills`, this also replaces the bundled repo-local Codex config

Bundled assets currently include the Claude agent
`commit-message-drafter` plus the Claude skills
`commit-staged-changes` and `commit-unstaged-changes`, and the Codex skills
`commit-staged-changes` and `commit-unstaged-changes`.

Installing `codex-skills` also writes the shared internal drafter brief at
`.agents/internal/commit-message-drafter.md`.

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

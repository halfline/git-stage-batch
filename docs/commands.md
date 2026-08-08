# Commands Reference

Complete reference of all available commands.

## Core Operations

### `check-unstaged`

Check whether the current index is suitable for an unstaged-only workflow.

```
❯ git-stage-batch check-unstaged
```

Exits successfully when the index is clean, or when every staged change is a
start-time file-level intent that `start` can normalize into workflow content:
regular text deletions and renames. These deletion and rename records may be
mixed. Exits with code 2 when other staged changes are present.

---

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

Untracked, non-ignored paths are discovered with a NUL-safe repository query
and registered as intent-to-add entries in one bulk index update. Startup
persists the complete auto-added path manifest once instead of rewriting it
for every file, so work scales with the number and bytes of candidate paths.
If a path disappears during discovery, startup refreshes the candidate set
once. Git publishes the index through its lockfile transaction, and a failed
bulk update rolls back the planned session manifest.

Live session diffs render renames as atomic `old -> new` choices. A selected
rename can be included, skipped, or discarded with the rest of the workflow.
At session start, staged renames and regular text deletions are temporarily
normalized into that same live workflow; if a normalized start-time change is
left untouched, `stop` or `abort` restores the original staged change.

Deleted text files are handled in two steps. First, the deleted lines are
shown as normal line-level changes, so you can include, skip, or discard only
part of the content deletion. If the staged/index version becomes an empty
file while the working-tree path is still absent, the path removal is shown as
a separate text-file deletion change. Including that second change removes the
path from the index; discarding it restores the empty file.

---

### `show [--file [PATH] | --files PATTERN...]`

Display the cached "selected" hunk, one file review, or a matched-files list.

**Show selected hunk:**
```
❯ git-stage-batch show
```

**Peek at the next hunk without selecting it:**
```
❯ git-stage-batch show --no-advance
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

Executable-bit changes (`100644` ↔ `100755`) are also shown as atomic entries.
When content and mode both change, content hunks are presented first and the
mode remains as a separate later action. Mode actions support whole-file
include, discard, skip, undo, redo, abort, and batch operations, but not
`--line` or paged line review. Repositories configured with
`core.fileMode=false` intentionally do not report executable-bit changes.

Regular-file, symlink, and submodule type transitions are never treated as
executable-bit changes. Unsupported type transitions are refused explicitly.

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
- `--no-advance`: Preview without selecting the shown change for later actions. This does not use the session's `start --no-auto-advance` default.
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
Prompt rendering is lock-free and read-only so shell startup never waits for an
in-progress staging operation. A prompt may briefly reflect either side of a
concurrent update, but it does not modify or clean up session state.
Format fields include `{status}`, `{status_label}`, `{progress_status}`,
`{progress_label}`, `{iteration}`, `{processed}`, `{total}`, `{included}`,
`{skipped}`, `{discarded}`, `{remaining}`, `{selected_file}`,
`{selected_line}`, `{selected_ids}`, and `{selected_kind}`. `{processed}` is
`included + skipped + discarded`; `{total}` is `{processed} + remaining`.

---

## Session Management

Only one linked worktree in a repository may own an active staging session at
a time. Session scratch data remains local to its worktree, while batch and
undo refs are shared by the repository. Mutating commands in another linked
worktree therefore stop with an error until the owning worktree runs `stop` or
`abort`. Read-only commands such as `status`, `show`, and `list` remain
available.

Ownership is released automatically by a successful `stop` or `abort`. If a
worktree's session marker has already been removed, the next mutating command
can reclaim the stale ownership record safely. An invalid ownership record is
not removed automatically; its error identifies the file to inspect after
confirming that no linked worktree still has an active session.

A session can start before the repository has its first commit. In that case,
Git's empty tree is used only as the staging comparison baseline while the
unborn symbolic branch and initial index are recorded explicitly. `stop`
preserves a first commit created during the session. `abort` removes that
session-created branch tip, restores the original index, and leaves original
first-commit files in the worktree. History-dependent commands such as
`fixup suggest`, `fixup create`, and the compatible `suggest-fixup` spelling
remain unavailable until the first commit exists.

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

Refuses by default if a path, index entry, or batch ref in the operation's
checkpoint scope has changed. Unrelated dirty and staged files are not retained
by the checkpoint, do not cause conflicts, and are left unchanged by undo.

---

### `redo`

Redo the most recently undone session operation.

```
❯ git-stage-batch redo
```

**Options:**
- `--force`: Overwrite changes made after the undo

Refuses by default if scoped state has changed since the undo. Unrelated
worktree and index changes remain untouched.

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

Discard all unstaged changes in one file.

**Discard selected hunk's file:**
```
❯ git-stage-batch discard --file
```

**Discard specific file by path:**
```
❯ git-stage-batch discard --file src/debug.py
```

Restores the working-tree file from the index, preserving any staged content. A new untracked file is removed because it has no indexed version to restore. When a path is provided, you can discard changes from any file regardless of which file the selected hunk is from.

**Use cases:**
- `--file` (no path): Discard the entire file of the selected hunk
- `--file PATH`: Discard the specified file, even if it's not the selected file
- `--files PATTERN...`: Discard all matched files as complete units

!!! warning "Destructive Operation"
    This permanently removes the file's unstaged changes. To intentionally remove a tracked file, use `git rm -- PATH` instead.

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
❯ git-stage-batch block-file --local-only .claude/
```

Use `--local-only` to write the ignore entry to `.git/info/exclude` instead
of `.gitignore`, which is useful for personal assistant assets or other
machine-local files.

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

## Fixup Workflows

### `fixup suggest` / `suggest-fixup`

Inspect exact lineage and mechanical placement evidence for the selected hunk,
then suggest a commit to fix up.

```
❯ git-stage-batch fixup suggest [BOUNDARY]
# Compatible spelling:
❯ git-stage-batch suggest-fixup [BOUNDARY]
```

The command materializes the selected hunk as an exact patch without changing
the real index. The patch is based on the frozen `HEAD` tree; an index-relative
hunk is rejected when that file's index entry differs from `HEAD`. It searches
each disjoint changed source range independently
and also commutes the patch backward through the target range. Candidate
iteration includes commits found by exact source-line history and any
mechanical placement barrier, newest first. The output keeps those two forms
of evidence separate: a placement barrier is useful, but is not presented as
proof of semantic ownership.

Pure additions to an existing tracked file use the adjacent old-file lines as
lineage anchors. An insertion into an empty tracked file can therefore have
placement evidence without lineage evidence. Whole-file additions remain
unsupported.

**Arguments:**
- `BOUNDARY`: Commit excluded from the search. By default, use the fork point
  (or merge base) between `HEAD` and its configured upstream.

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
❯ git-stage-batch fixup suggest
Selected unit 91c3e8ac213f in src/auth.py [agreed]
Lineage: exact source lines resolve to a1b2c3d4e5f.
Placement: patch first stops at a1b2c3d4e5f.
Decision: lineage and placement agree.

Candidate 1 of 2: a1b2c3d4e5f Fix authentication logic
Candidate evidence: lineage-history, placement-barrier

# Not the right commit, try next
❯ git-stage-batch fixup suggest
Candidate 2 of 2: e4f5a6b7c8d Add user validation
Candidate evidence: lineage-history

# This is the one! Create fixup commit
❯ git commit --fixup=e4f5a6b7c8d

# Or specify a different boundary for the search
❯ git-stage-batch fixup suggest main
Candidate 1 of 2: a1b2c3d4e5f Fix authentication logic
```

`fixup suggest` is read-only with respect to commits, refs, the working tree,
and the index. It does not create a commit. Use the displayed `git commit
--fixup=...` command for one reviewed target, or use `fixup create` to analyze
the staged index and create conservative grouped fixups.

**Porcelain output:**
```bash
❯ git-stage-batch fixup suggest --porcelain
```

The versioned record uses full object IDs only and includes the frozen range,
exact unit, lineage, placement, candidate sources, and iteration position:
```json
{
  "schema_version": 1,
  "operation": "fixup-suggest",
  "object_format": "sha1",
  "range": {
    "base": "1111111111111111111111111111111111111111",
    "head": "2222222222222222222222222222222222222222"
  },
  "unit": {
    "id": "3333333333333333333333333333333333333333333333333333333333333333",
    "path": "src/auth.py",
    "status": "agreed",
    "reason": "lineage-and-placement-agree",
    "lineage": {
      "queried_ranges": [{"start": 18, "end": 18}]
    },
    "placement": {
      "status": "barrier",
      "barrier": "2222222222222222222222222222222222222222"
    }
  },
  "candidate": {
    "id": "2222222222222222222222222222222222222222",
    "subject": "Fix authentication logic",
    "author_name": "John Doe",
    "author_email": "john@example.com",
    "authored_at": "2026-03-01T10:30:00-05:00"
  },
  "candidate_sources": ["lineage-history", "placement-barrier"],
  "iteration": {"index": 1, "total": 2},
  "result": "candidate"
}
```

When no evidence produces a candidate, porcelain prints the same complete
record with `"candidate": null`, a `result` reason such as `no-candidates` or
`exhausted`, and exits nonzero.

**Automated fixup example:**
```bash
# Get a full candidate ID and create a fixup only on a successful result
if CANDIDATE=$(git-stage-batch fixup suggest --porcelain | jq -er '.candidate.id'); then
  git commit --fixup="$CANDIDATE"
fi
```

---

### Line-level fixup suggestions

Suggest fixup target for specific lines only.

```
❯ git-stage-batch fixup suggest [BOUNDARY] --line LINE_IDS
```

**Example:**
```
❯ git-stage-batch fixup suggest --line 1,3
❯ git-stage-batch fixup suggest main --line 1,3
```

Useful when a hunk contains changes to multiple unrelated areas. Display-ID
ranges remain range-backed, and disjoint selected source lines are queried as
disjoint ranges rather than being widened to one min/max span. The exact
selected subset, not the complete hunk, is used for placement analysis.

---

### `fixup create`

Analyze the exact staged index and create one ordinary `fixup!` commit per
eligible target:

```bash
❯ git-stage-batch fixup create [BOUNDARY] [--dry-run] [--partial] [--porcelain]
❯ git-stage-batch fixup create --plan FILE [--dry-run] [--partial] [--porcelain]
```

The command combines exact-line history with tree-replay commutation. It
creates a fixup when both signals agree, or when line history identifies one
target and the patch can commute through the entire target range. It reports
but does not automatically commit:

- conflicting or ambiguous attribution;
- mechanical placement without semantic line history;
- changes with no target evidence; and
- currently unsupported whole-file additions/deletions, renames, binary
  changes, file modes, and gitlinks.

By default, any such remaining unit prevents all mutation. Use `--partial` to
create the eligible fixups and leave every other unit staged:

```bash
# Review exact assignments without changing HEAD, the index, or the worktree.
❯ git-stage-batch fixup create --dry-run

# Create eligible fixups while preserving unresolved staged work.
❯ git-stage-batch fixup create --partial
```

`fixup create` never stages unstaged changes, runs autosquash, rewrites an
existing commit, or publishes anything. It preserves a private recovery ref
for the original `HEAD` and prints that ref after successful creation.
The generated `fixup!` title normally uses the readable target subject. It
falls back to the full target object ID only when subject matching would make
`git rebase --autosquash` attach the fixup to another commit, including a
later commit that repeats an earlier subject.
Each generated commit also carries a random verification marker in its body.
The command uses that marker to distinguish its own commit from a ref moved by
a hook; autosquash discards the fixup body when the fixup is integrated.
Commit hooks inherit the isolated `GIT_INDEX_FILE` used to materialize the
proposed fixup tree. Hooks therefore inspect and stage against that temporary
index, not the user's real staged index. After each hook returns, the command
checks the verification marker, parent, subject, and complete tree; any
unexpected hook change stops creation and rolls `HEAD` back when it is still
safe to do so. Hooks used with this command must not assume that
`GIT_INDEX_FILE` names the user's ordinary index.

Successful creation deliberately retains its recovery ref below
`refs/git-stage-batch/fixup/backups/`; the command does not prune those refs.
The retained ref keeps the original tip reachable until recovery is no longer
needed. Repositories with many fixup runs can inspect the accumulated refs and
delete a reviewed terminal backup with an expected-old-value check:

```bash
❯ git for-each-ref --format='%(refname) %(objectname)' \
    refs/git-stage-batch/fixup/backups/
❯ git update-ref -d 'EXACT_RECOVERY_REF' EXPECTED_ORIGINAL_HEAD
```

Use the exact ref and object ID printed by the successful operation. Deleting
the ref removes the command's recovery anchor and may allow the original
commits to be garbage-collected, so retain it while rollback or comparison is
still useful.

The default boundary is the fork point, or merge base, between `HEAD` and its
configured upstream. An explicit boundary is the excluded base of the target
range. The range must be non-empty and linear.

**Porcelain output** is versioned JSON containing full object IDs, source tree
fingerprints, every change unit and evidence source, target groups, created
commits, and the recovery ref. A dry-run document is also a reusable plan:

```bash
❯ git-stage-batch fixup create --dry-run --porcelain
```

To review or supply semantic assignments without weakening the mechanical
checks, save that output, edit only its `assignments` array, and replay it:

```bash
❯ git-stage-batch fixup create main --dry-run --porcelain >fixup-plan.json
# Review assignments. Use full unit and commit IDs in every edited record.
❯ git-stage-batch fixup create --plan fixup-plan.json --dry-run
❯ git-stage-batch fixup create --plan fixup-plan.json
```

Each assignment has this shape:

```json
{
  "unit_id": "<full stable unit ID>",
  "target": "<full commit object ID>",
  "basis": "automatic"
}
```

Generated assignments use `"basis": "automatic"`. Change the basis to
`"explicit"` when a reviewed semantic decision adds an unresolved unit or
overrides its suggested target. Explicit review can supply meaning that the
lineage evidence lacks; it cannot move a patch through a mechanical barrier
or an `UNKNOWN` placement. Remove an assignment to leave that unit staged;
normal creation then requires `--partial` when any units remain.

Plan input accepts only the current strict JSON schema and only dry-run output.
Before creating refs or commits, the command regenerates and exactly compares
the object format, `HEAD`, index tree, head tree, ordered range, stable unit
IDs, paths, locations, and all lineage and placement evidence. It rejects
omitted or forged unit facts and duplicate, abbreviated, or out-of-range
assignments. It also relocates each assigned group to its target, replays the
complete range, and requires the integrated result to reproduce the assigned
staged patch.
The `groups` and `summary` fields are generated reports rather than plan
inputs; they are recalculated from the reviewed assignments. Passing both a
positional boundary and `--plan` is an error because the plan already freezes
its excluded base.

---

## History Refinement

### `rewrite scan`

Capture an immutable linear range and emit a reusable KEEP plan template:

```bash
❯ git-stage-batch rewrite scan [BOUNDARY] --output rewrite-plan.json
❯ git-stage-batch rewrite scan [BOUNDARY] --porcelain >rewrite-plan.json
```

The excluded boundary defaults to the fork point or merge base with the
configured upstream. The JSON snapshot records full commit, parent, and tree
IDs; byte-faithful messages with declared encodings and raw-content digests;
author and committer metadata; signature-header digests without signature
payloads; stable patch-unit identities; and compact per-unit dependency
evidence. Exact parent/new tree pairs bind the patches without storing one
Python or JSON object per changed line.

For each unit, the dependency graph records its original flat position, the
earliest position reached by real adjacent patch swaps, and the first
`BLOCKED` or `UNKNOWN` barrier. Same-path candidate swaps apply both units in
the opposite order with isolated Git indexes and accept the crossing only when
the resulting tree is identical. Consecutive different-path units are proved
as one exact block replay to avoid quadratic Git process growth. One blocked
sibling therefore does not suppress an independent sibling. Unsupported
renames, file-type transitions, and atomic
non-text changes remain `UNKNOWN`; analysis resumes from the next exact commit
tree but retains an `UNKNOWN` edge back across the unsupported segment.

Scan does not create commits, refs, checkpoints, or persistent objects. Its
speculative tree objects live only in a temporary object quarantine. Dirty
local state, active operations, saved batches, or remotely published range
commits appear as safety blockers rather than preventing an audit. Merges,
replace objects, and legacy grafts are rejected because they change the
supported topology or commit identity semantics.

### `rewrite validate`

Validate edited semantic input against a freshly regenerated snapshot:

```bash
# Validate an all-EXACT plan.
❯ git-stage-batch rewrite validate rewrite-plan.json

# Validate a plan containing RESOLVED outputs against its completed workspace.
❯ git-stage-batch rewrite validate rewrite-plan.json \
    --workspace rewrite-resolution
```

The executor schema accepts ordered `KEEP`, `REWORD`, `SPLIT`, `INTEGRATE`,
and `REORDER` outputs with independent `EXACT` or `RESOLVED` materialization.
For a plan whose outputs are all `EXACT`, omit `--workspace`; supplying a
gratuitous workspace is rejected rather than ignored. A plan containing any
`RESOLVED` output requires `--workspace DIR`, where `DIR` is the `COMPLETE`
private workspace produced by `rewrite resolve` for that exact plan. Missing,
incomplete, or differently bound workspaces fail validation.
`KEEP`, `REWORD`, and `REORDER` consume every unit of one source commit.
`REORDER` marks a whole source moved earlier and preserves its exact message
and encoding. `SPLIT` consumes a non-empty ordered subset of one source; every
split source must produce at least two outputs, and every piece preserves the
original author while supplying its own encodable message.

`INTEGRATE` consumes the complete target source followed by units from one or
more later repair sources. A multi-concern repair may partition its ordinary
units across several targets without first creating temporary commits. Every
output lists sources and units in source order. An ordinary source unit is
consumed exactly once globally. When one mechanical unit contains semantic
portions owned by several outputs, `plan.partitioned_units` lists every
zero-based RESOLVED output where that unit occurs. `KEEP` also preserves the
message and encoding; `REWORD`, `SPLIT`, and `INTEGRATE` explicitly supply an
encodable message. Rationale text is informational and never substitutes for
tree or patch conservation.

All snapshot fields are immutable. Validation rejects stale object IDs,
forged metadata, omitted or undeclared duplicate units, unmarked reorder
operations, abbreviated IDs, duplicate JSON keys, and unknown fields. Moving
an EXACT unit earlier is permitted only as far as its recorded adjacent-swap
proof. A chain of `BLOCKED` predecessors may move farther only when the plan
keeps the complete chain ordered inside one EXACT output; exact full-plan
replay must then prove that compound movement. A blocker assigned to another
output and every `UNKNOWN` crossing remain rejected. RESOLVED does not claim
that a patch commutes: it requires the explicit workspace workflow below to
materialize and audit the requested snapshots. Dependency evidence limits
exact replay, while complete replay and the frozen final tree remain required
for either materialization.

Completed-workspace validation is read-only. It authenticates the immutable
workspace binding, receipts, result artifacts, and `complete.json`, then
replays both exact and resolved outputs in a fresh Git object quarantine and
rechecks the frozen final tree. It does not repair or advance the workspace,
and no candidate objects or refs persist. Successful output reports the
authenticated completion SHA-256 digest. Porcelain output always includes
`summary.resolved_outputs`; its top-level `resolution` is `null` for an
all-EXACT plan or an object containing `workspace`, `complete_sha256`, and
`resolved_outputs` for completed-workspace validation.

The input safety block is not trusted: live index, worktree, operation,
publication, and upstream facts are collected again and returned in the
validation report. All candidate trees are materialized in a temporary object
quarantine and leave no objects or refs behind.

### `rewrite resolve`

Create or advance a private workspace for outputs that require explicit
snapshot materialization:

```bash
❯ git-stage-batch rewrite resolve rewrite-plan.json \
    --workspace rewrite-resolution
❯ git-stage-batch rewrite resolve rewrite-plan.json \
    --workspace rewrite-resolution --accept
```

Resolution is deliberately separate from semantic ordering. The plan must
already name each output's causal owner and mark only the mechanically
non-exact outputs `RESOLVED`. The command regenerates and compares the frozen
snapshot, validates unit conservation and ordering, then replays from the base
tree in a fresh object quarantine. It stops at the first unresolved output and
exports an immutable request containing its actual parent tree, exact source
units, authorized Git paths, and streamed `CURRENT_PARENT`, `SOURCE_BEFORE`,
and `SOURCE_AFTER` references.

The porcelain result names the exact request, editable `result.json`, and
opaque result-artifact directory. Edit only the result metadata and artifacts;
Git paths are never used as filesystem artifact names. JSON object-member
order is insignificant, but every field and path entry is required. For a
present result, write the desired bytes to its opaque artifact, retain
`state: "PRESENT"`, and choose an authorized `100644` or `100755` mode. Use
`state: "ABSENT"` with a null mode and remove its result artifact for an
authorized deletion; an authorized addition starts absent and becomes present
only after its result artifact is created. Keep every workspace metadata and
artifact file private with mode `0600`; newly created result artifacts with
broader permissions are refused.
`--accept` accepts exactly one output, verifies every authorized
path and transition, replays the deterministic downstream prefix, then writes
a digest-bound receipt. It either exports the next request or, after frozen
final-tree equality succeeds, writes `complete.json`. A result cannot add,
delete, change mode, or change content unless its selected source units
authorize that kind of transition, and every authorized path must actually
change.

Workspaces and artifacts are private, reject links and unexpected entries,
require root and subdirectory mode `0700`, and use descriptor-pinned bounded
I/O. Candidate blobs and trees remain in a temporary Git object quarantine.
The command never creates commits or refs, changes the index or worktree, or
publishes the completed workspace by itself. Re-running without `--accept`
safely reports the current request without accepting its seeded result.
The first invocation requires that the workspace path does not exist;
subsequent invocations reopen only the workspace bound to the same plan.
The completed external workspace is currently an input only to
`rewrite validate`. `rewrite apply` does not yet accept `--workspace`, so
this slice does not use the workspace to build or publish replacement commits.

### `rewrite apply`

Build, verify, and atomically update the checked-out branch:

```bash
❯ git-stage-batch rewrite apply rewrite-plan.json
❯ git-stage-batch rewrite apply rewrite-plan.json --porcelain
```

Apply creates a private recovery ref, records a durable `PREPARED` checkpoint,
then builds deterministic unsigned commit objects behind an operation-owned
output ref. It preserves the planned author, target source committer, declared
encoding, exact KEEP/REORDER message bytes, and every output tree. Split pieces
inherit the original source author and committer. Invalidated source signature
headers are omitted and recorded by header name and digest; payloads never
enter the plan or audit record.

The checked-out branch remains at its original tip until the entire output
chain has been mechanically replayed, independently verified, and shown to
have the frozen final tree. Apply then performs one compare-and-swap branch
update. It never runs interactive rebase, invokes commit hooks, edits the
worktree, contacts a remote, or pushes or force-pushes.

Any remote-tracking ref containing a source commit blocks apply by default.
After separately verifying that a ref is an authorized force-push review head,
name that exact full ref explicitly:

```bash
❯ git-stage-batch rewrite apply rewrite-plan.json \
    --allow-published-ref refs/remotes/origin/my-review
```

Repeat the option for every containing remote ref. The exception authorizes
only the local rewrite; it never authorizes a later push.

### `rewrite status`

Inspect the durable state machine used by the rewrite executor:

```bash
❯ git-stage-batch rewrite status
❯ git-stage-batch rewrite status --porcelain
```

Before any operation exists, status reports no active operation. For an active
checkpoint it reports the closed phase, exact next action, completed and
planned output counts, pending publication, recovery and output refs, last
verified tree, and live resume blockers. After completion or abort it reports
the latest terminal operation without occupying the active slot. The private
output ref keeps a partially built linear chain reachable. Status verifies
both owned refs, persisted plan and verification digests, source/plan
identity, output objects, branch compare-and-swap expectation, index, and
worktree before declaring continuation safe. Porcelain status also reports the
persisted plan's count for each operation kind. A specialized workflow can
therefore prove that an active checkpoint belongs to its allowed operation
subset without reading private state files.

Operation records live below `git-stage-batch/rewrite/` in the repository's
common Git directory (`.git/git-stage-batch/rewrite/` in an ordinary
checkout). They therefore remain available if the linked worktree that began
an operation is removed.

### `rewrite continue`

```bash
❯ git-stage-batch rewrite continue
```

Continue revalidates the checkpoint and executes only its recorded next
action. Each output uses a pending commit/tree checkpoint around the output-ref
compare-and-swap, so interruption before or after ref publication converges on
the same deterministic object. The final branch CAS is similarly reconciled
if it completed before the terminal state write.

### `rewrite abort`

```bash
❯ git-stage-batch rewrite abort
```

Abort first persists restore intent. It retracts an operation-owned pending
output ref and restores the original branch only if the live tip is still the
original value or the fully verified output value. Concurrent or manual
foreign movement is never overwritten; the recovery ref and a compare-and-swap
manual command remain available.

Completed and aborted operations deliberately retain their state plus any
recovery and output refs below `refs/git-stage-batch/rewrite/`; the command
does not prune them automatically. Those refs preserve the original and
replacement histories for later status, verification, and recovery. A
repository with many terminal operations can inspect the accumulated refs and,
after those capabilities are no longer needed, remove each reviewed ref with
an expected-old-value check:

```bash
❯ git for-each-ref --format='%(refname) %(objectname)' \
    refs/git-stage-batch/rewrite/
❯ git update-ref -d 'EXACT_OPERATION_ORIGINAL_REF' EXPECTED_ORIGINAL_HEAD
❯ git update-ref -d 'EXACT_OPERATION_OUTPUT_REF' EXPECTED_OUTPUT_HEAD
```

Delete only refs for a terminal operation and only when their exact expected
objects match. A partially built operation might not have an output ref.
Removing either anchor prevents later verification or automatic recovery for
that operation and may allow the corresponding commits to be
garbage-collected.

### `rewrite verify`

```bash
❯ git-stage-batch rewrite verify
❯ git-stage-batch rewrite verify --porcelain
```

Verify works on the active operation or latest complete operation. It
regenerates the frozen source facts independently of current `HEAD`, replays
every output tree, rehashes each normalized unsigned commit, checks parents,
authors, committers, messages, encodings, and signature removal, and compares
the regenerated audit record with its durable digest.
Verification may run while another worktree owns a staging session. It still
takes the repository session lock so its related reads cannot interleave with
`rewrite apply`, `rewrite continue`, or `rewrite abort` mutations.

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
❯ git-stage-batch install-assets claude-skills --filter refine-commit-messages
❯ git-stage-batch install-assets claude-skills --filter refine-history
❯ git-stage-batch install-assets claude-skills --filter decompose-and-commit-unstaged-changes
❯ git-stage-batch install-assets claude-skills --filter publish-unpushed-commits
❯ git-stage-batch install-assets codex-skills --filter 'commit-*' publish-unpushed-commits
```

**Options:**
- `GROUP`: Optionally restrict installation to one bundled asset group
- `--filter PATTERN...`: Install only bundled assets whose entry names match one or more gitignore-style patterns
  - When omitted, installs every bundled asset in the selected group, or in every group if no group is provided
- `--force`: Overwrite an existing installed asset
  - For `codex-skills`, this also replaces the bundled repo-local Codex config

Bundled assets currently include the Claude agent
`commit-message-drafter`, Claude decomposition agents, the Claude skills
`commit-staged-changes`, `commit-unstaged-changes`,
`decompose-and-commit-unstaged-changes`, `publish-unpushed-commits`,
`refine-commit-messages`, and `refine-history`, plus the Codex versions of
those six skills.

Selecting `decompose-and-commit-unstaged-changes` also installs its
`refine-history` and `refine-commit-messages` dependencies automatically.
Selecting `publish-unpushed-commits` installs the same two refinement
dependencies.
Selecting `refine-history` also installs `refine-commit-messages`.

`refine-commit-messages BASE_SHA` audits a linear series and rewords
noncompliant messages by default. It expresses only KEEP and REWORD outputs in
a rewrite plan and proves that every tree, patch boundary, author, committer,
and series position remains unchanged. Rewritten cryptographic signatures are
removed and reported because they cannot remain valid. Its explicit
`audit BASE_SHA` mode validates proposed replacements without updating refs or
commits.

`refine-history BASE_SHA` additionally splits broad commits, integrates late
repair units, and reorders only proven-independent sources before delegating
its message pass. Both skills use `rewrite status`, operation-kind summaries,
`continue`, `abort`, and `verify` instead of assistant-owned checkpoint or
interactive-rebase helpers. Mutating modes accept clean local draft history or
an explicitly verified force-push review head.

`publish-unpushed-commits` maps a clean unpublished range onto reviewable
GitHub pull requests or GitLab merge requests. It publishes ready for review by
default, supports an explicit `draft` mode, and uses provider-native stacks for
eligible same-repository dependent groups. Singletons, unavailable stacks, and
cross-fork publication use ordinary review requests. Its `audit` mode plans
without mutation, its `resume` mode continues strict recovery state, and no
mode merges.

For eligible same-repository groups, the skill probes GitHub's Stacked Pull
Requests REST API and creates the relationship directly; no GitHub CLI
extension is required. When the target repository does not offer Stacked Pull
Requests, publication continues through ordinary pull requests.

On GitLab 19.1 and newer, a same-project target-branch chain is recognized as a
stack without a separate linking mutation. The skill does not adopt the
experimental `glab stack` commit-and-branch workflow because refinement and
recovery state remain owned by the skill. GitLab publication requires an
authenticated `glab` CLI for the selected root-hosted GitLab URL. It uses
host-pinned API calls and numeric project identities, including for fork merge
requests, without changing local Git remotes.

Installing `codex-skills` also writes the shared internal drafter brief at
`.agents/internal/commit-message-drafter.md`.

---

## Diagnostics

### `journal`

Locate, summarize, or remove private diagnostic journal data.

```bash
git-stage-batch journal
git-stage-batch journal --path
git-stage-batch journal --purge
```

Journaling is disabled by default. Enable content-free operation metadata for
a reproduction with `GIT_STAGE_BATCH_JOURNAL=metadata-only`, or add bounded
call stacks with `verbose`. The `content-debug` level also records raw paths,
Git command output, and short content previews, so use it only for a limited
reproduction and purge it afterward.

The default command prints the configured level, private per-user path, file
count, entry count, and total size. `--porcelain` returns the same summary as
stable JSON. `--path` prints only the current repository's location. `--purge`
removes its active and rotated files; combine it with `--all` to remove journal
data for every repository.

---

## Batch Operations

### `validate`

Validate persisted metadata for every batch without modifying refs or files.

```bash
git-stage-batch validate
git-stage-batch validate --porcelain
```

The report identifies malformed or unsupported schemas, missing Git objects,
content-ref mismatches, and unversioned metadata eligible for migration.

---

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

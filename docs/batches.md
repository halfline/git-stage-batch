# Batch Operations

!!! info "Power User Feature"
    Batches are an advanced feature for complex workflows. Most users will not need them.
    The core commands (start, include, skip, discard) handle the majority of use cases.

Batches are named collections of accumulated changes that can be staged or discarded later as a unit. They persist across sessions and are stored as git commits under `refs/git-stage-batch/batches/<name>`.

Each batch captures not just the changes themselves, but also the working tree state at the time changes were saved (the **batch source**). This allows batch operations to intelligently merge or discard changes even when your code has evolved since the batch was created.

**When to use batches:**
- Accumulating related changes across multiple hunks for review together
- Deferring changes without losing them while working on other commits
- Grouping changes by type (e.g., debugging, refactoring) for separate handling

**When to avoid batches:**
- Simple linear workflows (just use skip and again for another pass)
- One-off staging decisions (include/skip/discard are simpler)

---

## How Batches Work

### Storage Model

When you save content to a batch (via `include --to` or `discard --to`), the tool captures:

1. **Batch source commit**: A snapshot of the working tree state at save time
2. **Ownership claims**: Which specific lines or line ranges are batch-owned
3. **Absence claims**: Which sequences were deleted by the batch (if any)

This information is stored in:
- A Git commit under `refs/git-stage-batch/batches/<name>` containing the realized batch content
- A state commit under `refs/git-stage-batch/state/<name>` tracking the batch source commit and ownership structure

Legacy batches under `refs/batches/<name>` are read as migration inputs. Once a batch is written by the current version, the legacy ref and file-backed metadata for that batch are removed.

Batch names must be non-empty single path components no longer than 250 UTF-8
bytes. In addition to spaces, slashes, backslashes, and colons, Git reserves
characters and sequences such as `~`, `^`, `?`, `*`, `[`, `..`, and `@{`, as
well as trailing dots and `.lock` suffixes. The name is checked with Git's own
ref-format rules before any batch or session state is written. Unicode names
are supported when they satisfy those rules.

If an older version left file-backed metadata with an invalid name, batch
discovery reports the entry instead of ignoring it. The diagnostic identifies
the invalid name and its metadata under Git's `git-stage-batch/batches`
directory. If a matching legacy `refs/batches/<old-name>` ref exists, choose a
valid unused name, rename that ref with `git update-ref`, and move the metadata
directory to the same new name. If no matching ref exists, the directory is
orphaned metadata from a failed creation and does not contain a recoverable
batch commit; inspect it if needed, then move it outside the state directory or
remove it. Run `git-stage-batch list` again to verify the repair.

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

### Submodule Pointers

Submodule pointers are treated as atomic changes. They can be saved
with `include --to BATCH`, shown with `show --from BATCH`, staged with
`include --from BATCH`, applied with `apply --from BATCH`, discarded with
`discard --from BATCH`, and removed from a batch with `reset --from BATCH`.

Because a submodule pointer has no line content in the superproject, `--line`
is not supported for those entries.

### Executable Modes

Executable-bit changes are stored as atomic batch metadata with no line-content
claims. They can be saved with `include --to` or `discard --to`, reviewed with
`show --from`, staged or applied with `include --from` and `apply --from`,
restored with `discard --from`, removed or moved with `reset --from`, and
filtered with `sift`. Line selection is not supported for mode actions.

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

Displays a matched-files list for multi-file batches. Open one listed file with
`show --from batch-name --file PATH` to review its page-aware batch diff and
use its line IDs.

**Line-level filtering:**
```
❯ git-stage-batch show --from batch-name --line 1,3,5-7
```

Filter the display to show only specific line IDs from the batch.

**Pattern-based filtering:**
```bash
❯ git-stage-batch show --from batch-name --files "src/**/*.py" "!src/vendor/**"
```

When `--files` resolves to multiple files, `show --from` prints a navigational
file list and does not leave a hidden selected batch file behind.

---

## `include --from BATCH`

Stage the changes from a batch to the index and write them to the working tree.

**Stage entire batch:**
```
❯ git-stage-batch include --from batch-name
```

Applies the batch's accumulated changes to the index and working tree, staging
them for commit.

**Line-level staging:**
```
❯ git-stage-batch include --from batch-name --line 1-5
```

Stage only specific lines from the batch, allowing partial application of batch
changes to the index and working tree.

**File-level staging (selected file):**
```
❯ git-stage-batch include --from batch-name --file
```

Stage changes from the batch for the selected hunk's file only. Use this during
a staging session when you want to pull in batch changes for the file you're
reviewing, without affecting other files in the batch.

**File-level staging (specific file):**
```
❯ git-stage-batch include --from batch-name --file src/config.py
```

Stage changes from the batch for `src/config.py` only, without needing a
selected hunk. Useful for applying specific files from multi-file batches
outside of an active staging session.

**Pattern-based staging:**
```bash
❯ git-stage-batch include --from batch-name --files "src/**/*.py" "!src/vendor/**"
```

**Example - Selective file application:**
```bash
# Create batch with changes from multiple files
❯ git-stage-batch new refactor
❯ git-stage-batch discard --to refactor --file auth.py
❯ git-stage-batch discard --to refactor --file config.py
❯ git-stage-batch discard --to refactor --file utils.py

# Later, apply only config.py changes
❯ git-stage-batch include --from refactor --file config.py
# Only config.py is staged, auth.py and utils.py remain in batch
```

!!! warning "Merge-Based Application"
    `include --from BATCH` uses structural merge to intelligently apply batch changes
    to your current working tree, even if the code has evolved since the batch was created.

    The merge succeeds when:
    - Batch-claimed lines can be unambiguously located in the current file structure
    - Changes have context (surrounding unchanged lines) for alignment

    Failures occur when:
    - The file structure has changed so drastically that batch lines cannot be located
    - Claimed lines lack sufficient context for structural alignment
    - The batch attempts to delete content that no longer exists at expected positions

    On failure, run `show --from BATCH` to review the changes, or use `--line` or
    `--file` to apply only compatible parts.

---

## `discard --from BATCH`

Remove batch changes from the working tree.

**Discard entire batch:**
```
❯ git-stage-batch discard --from batch-name
```

Removes the batch's changes from your working tree by applying the reverse of the batch's diff.

**Line-level discarding:**
```
❯ git-stage-batch discard --from batch-name --line 2,4
```

Discard only specific lines from the batch, allowing surgical removal of batch changes.

**File-level discarding (selected file):**
```
❯ git-stage-batch discard --from batch-name --file
```

Remove batch changes from the working tree for the selected hunk's file only. Use this during a staging session when you want to discard batch changes for the file you're reviewing, without affecting other files in the batch.

**File-level discarding (specific file):**
```
❯ git-stage-batch discard --from batch-name --file src/experimental.py
```

Remove batch changes for `src/experimental.py` only, without needing a selected hunk. Useful for discarding specific files from multi-file batches.

**Pattern-based discarding:**
```bash
❯ git-stage-batch discard --from batch-name --files "src/**/*.py" "!src/vendor/**"
```

!!! warning "Destructive Operation"
    This permanently removes changes from your working tree.

!!! warning "Constraint-Based Reversal"
    `discard --from BATCH` uses structural analysis to reverse batch changes by:
    - Removing batch-added content (insertions)
    - Restoring batch-modified lines to their baseline state
    - Re-inserting batch-deleted sequences at their original boundaries

    The operation succeeds when:
    - Batch-owned content can be unambiguously identified in the current file
    - Modified regions have clear line-by-line correspondence with baseline, OR
    - Modified regions are fully batch-owned (allowing atomic restoration)

    Failures occur when:
    - Partial ownership of regions that cannot be restored line-by-line
    - File structure has changed so drastically that batch content cannot be located
    - Deleted sequences cannot be re-inserted at original anchored boundaries

    The batch itself persists - only the working tree is modified. Use `--file` to
    filter to a specific file, or `--line` to discard only specific lines.

---

## `apply --from BATCH`

Apply batch changes to the working tree without staging them.

**Apply entire batch:**
```
❯ git-stage-batch apply --from batch-name
```

Applies the batch's accumulated changes to your working tree, leaving the index
untouched. This is different from `include --from`, which writes the selected
batch changes to both the index and working tree.

**Use cases:**
- Temporarily applying batched changes to test them before committing
- Restoring changes that were saved with `discard --to`
- Previewing batch changes in your working tree before staging

**Line-level application:**
```
❯ git-stage-batch apply --from batch-name --line 1-3
```

Apply only specific lines from the batch to the working tree.

**File-level application (selected file):**
```
❯ git-stage-batch apply --from batch-name --file
```

Apply batch changes to the working tree for the selected hunk's file only. Use this during a staging session when you want to preview batch changes for the file you're reviewing, without affecting other files in the batch.

**File-level application (specific file):**
```
❯ git-stage-batch apply --from batch-name --file src/debug.py
```

Apply batch changes for `src/debug.py` only to the working tree, without needing a selected hunk. Useful for testing specific files from multi-file batches.

**Pattern-based application:**
```bash
❯ git-stage-batch apply --from batch-name --files "src/**/*.py" "!src/vendor/**"
```

!!! warning "Merge-Based Application"
    `apply --from BATCH` uses the same structural merge as `include --from BATCH`,
    intelligently applying batch changes even if the working tree has evolved.

    See the warning under `include --from BATCH` for details on when merge succeeds
    or fails.

    On failure, run `show --from BATCH` to review the changes, or use `--file` to
    filter to a specific file, or `--line` to apply only specific lines.

!!! info "Working Tree Only"
    Unlike `include --from`, this command modifies only the working tree and
    leaves the index (staging area) untouched. Use this when you want to preview
    or test changes before staging them.

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

**Example - Selective file preview:**
```bash
# Batch has changes to auth.py, config.py, utils.py
❯ git-stage-batch apply --from refactor --file auth.py
# Only auth.py changes are in working tree, others remain in batch

# Test auth.py changes...

# Restore and try a different file
❯ git restore auth.py
❯ git-stage-batch apply --from refactor --file config.py
```

---

## `reset --from BATCH`

Remove claims from a batch without changing the working tree.

**Reset entire batch:**
```
❯ git-stage-batch reset --from batch-name
```

Clears all files from the batch.

**Reset selected file:**
```
❯ git-stage-batch reset --from batch-name --file src/debug.py
```

Removes only `src/debug.py` from the batch. If `--file` is used without a path, the selected hunk's file is used.

**Reset files by pattern:**
```bash
❯ git-stage-batch reset --from batch-name --files "src/**/*.py" "!src/vendor/**"
```

**Reset selected lines from a file:**
```
❯ git-stage-batch reset --from batch-name --file src/debug.py --line 1,3-5
```

Removes only those line claims from the batch. Line reset is resolved from the batch's stored source commit, not from the current working tree contents.

**Split selected claims into another batch:**
```
❯ git-stage-batch reset --from batch-name --to other-batch --file src/debug.py --line 1,3-5
```

Moves the selected claims into `other-batch` and removes them from `batch-name`. If `other-batch` does not exist, it is created with the source batch's baseline so the split is independent of the current working tree or current `HEAD`.

---

## `include --to BATCH`

Include the selected hunk in a batch for later staging.

```
❯ git-stage-batch include --to batch-name
```

This captures a snapshot of the current working tree state (the **batch source**) along with ownership information for the selected lines, then marks the hunk as processed. The changes remain in your working tree and can be staged later using `include --from BATCH`.

The batch source allows later operations to intelligently merge or discard changes even if your code has evolved since the batch was created.

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

**Line-level saving:**
```
❯ git-stage-batch include --to batch-name --line 1,3
```

Save only specific lines to the batch, allowing fine-grained accumulation of changes.

**File-level saving:**
```
❯ git-stage-batch include --to batch-name --file
```

Save the entire selected file to the batch instead of just the selected hunk. Useful when you want to defer an entire file's changes as a unit.

---

## `discard --to BATCH`

Save the selected hunk to a batch, then discard it from the working tree.

```
❯ git-stage-batch discard --to batch-name
```

This captures a snapshot of the current working tree state (the **batch source**) along with ownership information for the selected lines, then removes the changes from your working tree. The batch acts as a backup allowing later recovery via `apply --from BATCH` or `include --from BATCH`.

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

Save all changes in the selected file to the batch, then remove those changes
from the working tree. A tracked file returns to its indexed baseline; a newly
added file is removed because it has no indexed version to restore.

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

---

## Advanced Workflow: Decomposing and Recomposing History

When you have a messy working tree with multiple logical changes intertwined, you can use batches to decompose the changes into layers, create clean checkpoints, then recompose them as a series of well-organized commits.

This workflow has two phases: **decompose outside-in** and **recompose inside-out**.

### Terminology

**Layer batch** — a logical commit layer captured while peeling the working tree.

- Captured with `discard --to <layer-batch>`: saves the selected change to the batch and removes it from the working tree.
- Replayed during recomposition with `apply --from <layer-batch>`.

**Bridge repair batch** — a temporary repair that makes an intermediate layer state coherent.

- Captured with `include --to <repair-batch>`: saves the repair to a batch but leaves it in the working tree so the intermediate state can be tested or reviewed.
- Replayed after the layer it repairs with `apply --from <repair-batch>`.
- Removed before the next real layer with `discard --from <repair-batch>`.
- A bridge repair is intentionally temporary: it may appear in one intermediate commit and then be removed by the next commit.

Suggested naming convention:

```text
layer-1-database
repair-after-layer-1
layer-2-auth
repair-after-layer-2
layer-3-api
```

A `repair-after-layer-N` batch means: "temporary code needed after layer N has been committed, but before layer N+1 has been applied."

### Why capture every repair?

Batch application uses **structural merge**, not raw patch replay. The merge engine locates batch-claimed lines relative to the current file structure. If a repair edit is left anonymous in the working tree — not captured in any batch — then later `apply --from` or `discard --from` operations may fail or produce unexpected results because the file no longer looks like what the batch expects.

Capturing every intentional edit (whether a real layer change or a temporary bridge) keeps the replay sequence explicit: `apply --from` adds, `discard --from` removes, and there are no hidden state changes between them.

### Phase 1: Decompose outside-in

Start with the final messy working tree. Peel the outermost (most-dependent) layer first using `discard --to`. If the remaining intermediate state needs a repair to be coherent, make that repair in the working tree and capture it with `include --to repair-*`. Remove the bridge before peeling the next inner layer so it does not pollute that layer's batch.

Use `annotate` to add descriptions to batches. The `--note` flag is not available for `discard --to` or `include --to`.

```bash
# Starting state: final messy working tree containing layer 1 + layer 2 + layer 3.
# Layer 3 depends on layer 2; layer 2 depends on layer 1.

❯ git-stage-batch start

# Peel the outermost layer first.
❯ git-stage-batch discard --to layer-3-api
❯ git-stage-batch annotate layer-3-api "Layer 3: API endpoint"

# The remaining layer-1 + layer-2 state needs a small temporary repair
# to build or run without layer 3. Make that repair in the working tree.
❯ $EDITOR path/to/file

❯ git-stage-batch again
❯ git-stage-batch include --to repair-after-layer-2
❯ git-stage-batch annotate repair-after-layer-2 "Temporary bridge after layer 2"

# Remove the bridge before peeling the next inner layer so it does not
# become part of the layer-2 batch.
❯ git-stage-batch discard --from repair-after-layer-2

❯ git-stage-batch again

# Peel the next layer.
❯ git-stage-batch discard --to layer-2-auth
❯ git-stage-batch annotate layer-2-auth "Layer 2: auth refactor"

# The remaining layer-1 state needs a small temporary repair.
❯ $EDITOR path/to/file

❯ git-stage-batch again
❯ git-stage-batch include --to repair-after-layer-1
❯ git-stage-batch annotate repair-after-layer-1 "Temporary bridge after layer 1"

# Remove the bridge before peeling the foundation layer.
❯ git-stage-batch discard --from repair-after-layer-1

❯ git-stage-batch again

# Peel the foundation layer.
❯ git-stage-batch discard --to layer-1-database
❯ git-stage-batch annotate layer-1-database "Layer 1: database foundation"
```

!!! note "Missed pieces vs. bridge repairs"
    If a "repair" is just leftover code from an upper layer — a call to a function that
    was discarded, or an import that is no longer used — it is probably not a bridge
    repair. It is a missed piece of the upper layer and should be captured into that
    upper layer with `discard --to`.

    A true bridge repair is temporary compatibility code or cleanup that is correct
    for an intermediate commit but intentionally disappears when the next layer is
    applied.

### Phase 2: Recompose inside-out

Start from the original base commit. Apply each layer batch and its corresponding bridge repair to the working tree, stage with `git-stage-batch include --files`, and commit. Before the next real layer, remove the previous bridge repair with `discard --from`.

Using `apply --from` (working-tree-only) followed by `git-stage-batch include --files` is preferred over `include --from` (which writes to both index and working tree simultaneously) because recomposition alternates `apply --from` and `discard --from`, and keeping the index separate lets you review the final working-tree state before staging.

```bash
# Start from the original base commit / pristine tree.
# Recompose inside-out.

# Commit 1: foundation layer plus temporary bridge needed after layer 1.
❯ git-stage-batch apply --from layer-1-database
❯ git-stage-batch apply --from repair-after-layer-1

❯ git diff          # review working tree
❯ git-stage-batch include --files "**"
❯ git commit -m "database: add foundation"

# Commit 2: remove the layer-1 bridge, then apply the real layer-2 change.
❯ git-stage-batch discard --from repair-after-layer-1
❯ git-stage-batch apply --from layer-2-auth
❯ git-stage-batch apply --from repair-after-layer-2

❯ git diff
❯ git-stage-batch include --files "**"
❯ git commit -m "auth: build on database foundation"

# Commit 3: remove the layer-2 bridge, then apply the real layer-3 change.
❯ git-stage-batch discard --from repair-after-layer-2
❯ git-stage-batch apply --from layer-3-api

❯ git diff
❯ git-stage-batch include --files "**"
❯ git commit -m "api: add endpoint"

# Clean up saved batches when finished.
❯ git-stage-batch drop layer-1-database
❯ git-stage-batch drop repair-after-layer-1
❯ git-stage-batch drop layer-2-auth
❯ git-stage-batch drop repair-after-layer-2
❯ git-stage-batch drop layer-3-api

# Result: clean, logical commit history instead of one messy commit
```

The second commit intentionally contains both "remove the previous temporary repair" and "apply the next real layer." That is the point of the bridge: it keeps commit 1 coherent without pretending the repair is part of the final layer stack.

### Key insights

- Use `discard --to` for real layer changes that should be peeled away from the working tree.
- Use `include --to` for bridge repairs that should be saved to a batch but remain in the working tree.
- A bridge repair is temporary. Apply it after the layer it repairs, then remove it with `discard --from` before applying the next real layer.
- Prefer `apply --from` during recomposition, followed by `git-stage-batch include --files "**"`, so the index is staged from the final reviewed working-tree state for each commit.
- Do not leave repair edits uncaptured. Uncaptured repairs can cause later structural replay to fail or force manual reconstruction.
- If the repair is actually leftover upper-layer code, capture it into the upper-layer batch instead of creating a repair batch.
- The decomposition order is outside-in; the recomposition order is inside-out.
- The goal is a sequence of coherent, reviewable commits, even when some intermediate commits need temporary bridge code that later disappears.

---

## Frequently Asked Questions

### How are batches different from Git stashes?

A stash saves the entire state of your working tree so you can return to it later. A batch saves a **logical change** so you can organize it into a clean commit later.

Stashes are for temporarily setting work aside. Batches are for structuring and organizing work before committing it.

With a stash, you capture everything:

```bash
git stash
```

With a batch, you capture only the parts you choose:

```bash
git-stage-batch include --to parser
git-stage-batch include --to cli
git-stage-batch include --to docs
```

Later, you can turn each batch into a commit.

---

### Why not just use `git stash`?

Stashes are snapshots of your workspace. They are not designed to organize code changes into meaningful commits.

If your working tree contains multiple logical changes, a stash will bundle them all together. Batches let you separate them as you go.

For example:

```
working tree:
  parser work
  CLI changes
  documentation updates
```

With stashes, those changes are stored together.

With batches, they can be separated:

```
parser
cli
docs
```

Each batch can later become its own commit.

---

### Can I replace stashes with batches?

No. They solve different problems.

Use stashes when you need to quickly save your working state:

```bash
git stash
git pull
git stash pop
```

Use batches when you're organizing a messy working tree into clean commits.

---

### How are batches different from commits?

A commit is permanent project history. A batch is a temporary container for changes you are still organizing.

You should think of a batch as a **draft commit**.

Example workflow:

```bash
git-stage-batch include --to parser
git-stage-batch include --to parser
git-stage-batch include --to parser

git-stage-batch include --from parser
git commit -m "Add parser implementation"
```

The batch helps assemble the commit, but it is not part of the repository history itself.

---

### Why not just commit earlier?

Sometimes your working tree contains changes that belong to different commits but are mixed together.

For example:

```
working tree:
  parser feature
  CLI integration
  documentation
  refactor
```

You could commit everything at once, but that produces messy history.

Batches let you reorganize changes into logical commits before publishing them.

---

### Are batches like temporary branches?

Not really.

Branches organize commits. Batches organize **uncommitted changes**.

A branch looks like this:

```
commit → commit → commit
```

A batch looks more like this:

```
selected hunks → staged later → commit
```

They operate at different levels of the workflow.

---

### Do batches modify my Git history?

Not by themselves.

Batches are stored separately from your commit history. The normal workflow uses
them to prepare commits without rewriting existing history.

Assistant decomposition workflows can use batches while rebuilding a local
draft series, and may polish commits they just created before the series is
shared. That is different from modifying protected branch history.

Once a batch is included and committed, the batch itself can be dropped.

---

### When should I use batches?

Batches are useful when your working tree contains multiple logical changes and you want to turn them into clean commits.

Typical cases include:

* splitting a large diff into logical commits
* organizing refactors before submitting a pull request
* reconstructing history for a patch series
* preparing changes before rebasing or squashing

---

### Are batches meant to be long-lived?

No. Batches are usually short-lived.

They exist while you are organizing a set of commits and are typically dropped once the commits have been created.

---

### Do batches replace `git add -p`?

No. Batches build on the same idea.

`git add -p` lets you stage parts of a change.
Batches let you **defer and group those parts** so they can become separate commits later.

---

### Why use batches instead of staging everything immediately?

Because sometimes you do not yet know which commit a change belongs in.

Batches let you postpone that decision while still organizing the changes.

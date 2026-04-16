# Git Stage Batch: Architecture White Paper

## Table of Contents

1. [Introduction](#introduction)
2. [Core Concept: What Are Batches?](#core-concept-what-are-batches)
3. [The Three-Commit Model](#the-three-commit-model)
4. [Constraint-Based Ownership](#constraint-based-ownership)
5. [Conservative Structural Alignment](#conservative-structural-alignment)
6. [Display vs Merge Logic](#display-vs-merge-logic)
7. [Ownership Units and Semantic Selection](#ownership-units-and-semantic-selection)
8. [Stale Batch Source Detection and Repair](#stale-batch-source-detection-and-repair)
9. [Storage Architecture](#storage-architecture)
10. [Attribution-Based Filtering](#attribution-based-filtering)
11. [Sifting: Reconciling Batches Against Current Tip](#sifting-reconciling-batches-against-current-tip)
12. [Change Propagation](#change-propagation)
13. [Complete Example Walkthrough](#complete-example-walkthrough)

---

## Introduction

Git-stage-batch extends Git's staging area with a line-level batching system. Instead of staging all-or-nothing hunks, users can partition changes into multiple named batches, each representing a logically distinct modification (e.g., "feature-a", "fix-b", "refactoring").

This white paper explains the internal architecture that makes batches possible, with particular focus on:

* How batches track ownership of specific lines across evolving file content
* The constraint-based model that prevents duplication and maintains consistency
* The structural alignment algorithms that make merging reliable
* The attribution system that filters already-batched content as working tree evolves

**Target Audience:** Developers working on git-stage-batch internals, contributors debugging batch-related issues, or anyone seeking to understand the design decisions behind the implementation.

### Terminology: Ownership vs Attribution

This document distinguishes between two related but fundamentally different concepts:

* **Ownership**: What a batch claims, expressed as constraints in batch source coordinate space

  * Defined by `BatchOwnership`
  * Used for merge, discard, and semantic selection

* **Attribution**: Which parts of the *current working tree* correspond to which batches

  * Derived from file comparison (baseline ↔ working tree)
  * Used for filtering already-batched content from display

Ownership is **stored and declarative**.
Attribution is **derived and ephemeral**.

This distinction matters: attribution does not modify ownership, and ownership does not directly encode working tree state.

---

## Core Concept: What Are Batches?

### Problem Statement

Traditional Git staging has two states per file:

* **Working tree**: Current file content with all your changes
* **Index (staging area)**: Content prepared for next commit

This binary model is limiting:

```
Working Tree:  line1 + feature-a-change + fix-b-change + refactoring
                          ↓ (all or nothing)
Index:         line1 + feature-a-change + fix-b-change + refactoring
```

You can't easily say "I want feature-a-change in commit #1 and fix-b-change in commit #2."

### Solution: Named Batches

Batches introduce **multiple logical staging areas**, each with line-level precision:

```
Working Tree:  line1 + feature-a-change + fix-b-change + refactoring

Batch "feature-a":     [feature-a-change]
Batch "fix":           [fix-b-change]
Batch "cleanup":       [refactoring]
```

Each batch **claims ownership** of specific lines and **suppresses** other content, allowing you to:

1. Organize changes by logical purpose
2. Review each batch independently
3. Apply batches to working tree in any order
4. Move lines between batches freely

The system hides already-batched content from display through **attribution-based filtering** by comparing file snapshots to determine which working tree changes belong to which batches. This means you can continue editing the working tree and the system will adapt, showing only unbatched changes.

---

## The Three-Commit Model

Every batch operates within a **three-commit model**:

```
                    ┌─────────────────┐
                    │  Baseline       │  ← Snapshot from HEAD
                    │  (commit)       │     (committed history)
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Batch Source   │  ← Snapshot from working tree
                    │  (commit)       │     (uncommitted changes)
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Batch Commit   │  ← Realized batch content
                    │  (refs/batches) │     (baseline + claimed changes)
                    └─────────────────┘
```

**Key Distinction:**

* **Baseline**: Captured from **HEAD** (committed history)
* **Batch Source**: Captured from **working tree** (uncommitted changes)
* **Batch Commit**: Computed from baseline + batch source ownership

### 1. Baseline Commit

**Purpose:** Establishes the "before" state against which all changes are measured.

**When Set:** Captured from **HEAD** when a batch is created (via `git-stage-batch new` or auto-creation during `include --to` / `discard --to`).

**Important:** The baseline comes from the committed history (HEAD), not the working tree.

**Example:**

```bash
# User creates initial commit
git commit -m "Initial: has line1, line2, line3"
# This becomes the baseline

git-stage-batch start
git-stage-batch new "feature-a"
# Batch "feature-a" baseline = HEAD = that initial commit
```

**Contents:**

```
file.txt:
  line1
  line2
  line3
```

### 2. Batch Source Commit

**Purpose:** Captures the **working tree state** at the moment changes are saved to a batch. This is the **reference coordinate system** for all line-level operations.

**When Created:** Automatically when content is first added to a batch via `include --to` or `discard --to`.

**Important:** The batch source comes from the **working tree**, not HEAD. It captures your uncommitted changes.

**Why Needed:** The working tree evolves continuously. To track "which lines belong to this batch," we need a stable snapshot. The batch source commit provides this stability.

**Example:**

```bash
# Working tree now has modifications
cat file.txt
# line1
# line2-modified
# line3
# line4-new

git-stage-batch include --to feature-a --line 1,2
# Batch source commit created: working tree → new commit
# This commit becomes the reference for line numbers
```

**Contents of batch source commit:**

```
file.txt:
  line1           ← line 1 in batch source space
  line2-modified  ← line 2 in batch source space
  line3           ← line 3 in batch source space
  line4-new       ← line 4 in batch source space
```

**Coordinate System:** All batch ownership is expressed in batch source line numbers:

* `claimed_lines: ["2", "4"]` means "lines 2 and 4 of the batch source commit"
* Not "lines 2 and 4 of working tree" (which may have changed since)

### 3. Batch Commit (Realized Content)

**Purpose:** The actual commit stored in `refs/batches/<name>` that represents what the batch would look like if applied to baseline.

**How Built:** Baseline content realized under the batch's presence and absence constraints, using batch source as the coordinate reference.

Conceptually:

```
Batch Commit = apply_constraints(Baseline, BatchOwnership, BatchSource)
             = baseline content realized so that claimed source lines are present
               and anchored forbidden sequences do not survive
```

**Example:**
Given:

* Baseline has: `line1, line2, line3`
* Batch source has: `line1, line2-modified, line3, line4-new`
* Batch claims: lines 2, 4 (in batch source coordinates)

The realized batch commit contains:

```
file.txt:
  line1          ← from baseline (unclaimed)
  line2-modified ← from batch source line 2 (claimed)
  line3          ← from baseline (unclaimed)
  line4-new      ← from batch source line 4 (claimed)
```

**Storage:** This commit is stored as a Git commit object with:

* Tree: Contains the realized file content
* Parents: [baseline, batch-source, ...other batch sources]
* Reference: `refs/batches/<batch-name>`

---

## Constraint-Based Ownership

### The Core Insight

Traditional diff-based systems think of changes as "operations to replay":

```
- line2
+ line2-modified
```

This leads to problems: replaying operations is not inherently idempotent. Reapplying the same diff can duplicate changes or produce invalid structure.

**git-stage-batch uses constraints instead of operations:**

1. **Presence Constraints** (claimed lines): "Line 2 of batch source is required in result"
2. **Absence Constraints** (deletions): "Content 'line2\n' is forbidden in result"

### Replacement Is Not a Primitive

A “replacement” is represented as the combination of:

* a presence constraint (the new content must exist)
* an absence constraint (the old content must not exist at its anchored location)

This decomposition is intentional:

* it preserves idempotence
* it avoids introducing a special-case operation type
* it keeps all behavior expressible in terms of constraints

### Ownership Model

```python
@dataclass
class DeletionClaim:
    """A structurally anchored absence constraint.

    Represents: this specific byte sequence, at this aligned location
    in batch source space, must not survive at its structurally aligned
    location in the realized result.
    """
    anchor_line: int | None       # Structural anchor: after which source line
                                  # (None = start of file, before line 1)
    content_lines: list[bytes]    # The forbidden sequence (stored for matching)

@dataclass
class BatchOwnership:
    """Batch ownership in batch source coordinate space."""
    claimed_lines: list[str]      # e.g., ["2-4", "8"] (presence constraints)
    deletions: list[DeletionClaim]  # Absence constraints (each is structurally independent)
```

**Example:** User modifies line 2:

```diff
  line1
- line2
+ line2-modified
  line3
```

When saved to batch, this becomes:

```python
BatchOwnership(
    claimed_lines=["2"],  # Line 2 (line2-modified) must exist
    deletions=[
        DeletionClaim(
            anchor_line=1,  # After source line 1 (after "line1")
            content_lines=[b"line2\n"]  # This sequence at this position must not exist
        )
    ]
)
```

### Why Constraints Matter

**Designed for Idempotence:**

```python
# Applying batch multiple times should yield same result
result1 = merge_batch(source, ownership, working)
result2 = merge_batch(source, ownership, result1)
# result1 == result2 when implementation correctly applies constraints
```

**No Duplication:**

* Presence constraint: "line2-modified must exist" → check if present, add if missing
* Absence constraint: "line2 at this anchored location must not exist" → suppress at aligned position
* Presence constraints do not delete or consume unrelated working-tree lines. If
  stale content must be removed, that removal is represented by an explicit
  absence constraint.
* Can't accidentally create duplicates through structural awareness

**Constraint Satisfaction Order:**

Merge applies constraints as a small satisfaction process, not as a raw patch:

1. Apply presence constraints, building realized entries with source provenance
2. Apply absence constraints at their anchored boundaries
3. Re-check that every claimed line still survives
4. If an absence constraint removed the only realized occurrence of a claimed
   line, apply presence and absence once more

The final re-check matters when a stale working-tree block happens to contain
text that matches a claimed target line. That stale block must not be allowed to
"satisfy" the presence constraint if an absence constraint then removes it.

**Structural Integrity:**

Deletions are not mere content blobs - they are **structurally anchored constraints**:

```python
# Two deletion runs = two separate claims.
deletions = [
    DeletionClaim(
        anchor_line=1,                      # After source line 1
        content_lines=[b"del1\n", b"del2\n"] # This sequence at this location
    ),
    DeletionClaim(
        anchor_line=5,                      # After source line 5
        content_lines=[b"del3\n"]           # This sequence at this location
    )
]
```

The anchor is not just a hint - it's part of the constraint's identity. Two deletion claims with the same content but different anchors are **semantically distinct** (they forbid the same bytes at different structural positions).

### Constraint Conflicts

**Cross-Batch Conflicts:**

Constraints can conflict when multiple batches claim ownership of the same content:

* **Presence vs Absence**: Batch A claims a line must exist, Batch B forbids that line at the same position
* **Competing Presence**: Batch A and Batch B both claim different content at the same structural position
* **Overlapping Ownership**: Batches claim overlapping but non-identical line ranges

**Current Behavior:**

The model does not currently specify automatic conflict resolution. When constraints conflict:

* Batches are typically applied independently (first wins, or explicit ordering)
* Users are expected to avoid creating conflicting batches
* Future versions may detect and surface conflicts explicitly rather than resolving them implicitly

**Design Intent:**

Conflicting constraints should be **detected and reported** rather than silently resolved. The user created the conflict (by putting incompatible changes in different batches), so the user should decide how to resolve it - not the tool.

---

## Conservative Structural Alignment

### The Problem

When merging batch content into the working tree, line numbers are **unstable**:

```
Batch Source:        Working Tree:
line1 ← claimed     line1
line2               [NEW] extra-line
line3 ← claimed     line3
line4               [DELETED: line4]
                    line5-modified
```

How do we know:

* Line 3 in batch source = line 3 in working tree? (skipping extra-line)
* Where to insert claimed line 1?
* Where to insert claimed line 3?

### Solution: Conservative Line Matching

We use the `match_lines()` function from `batch.match` module to compute conservative structural alignment:

```python
from git_stage_batch.batch.match import match_lines

mapping = match_lines(source_lines, working_lines)
# Returns LineMapping with bidirectional source ↔ target correspondence
```

The matcher computes conservative structural correspondence that:

* Maps exact equal prefixes and suffixes
* Uses unique lines that occur exactly once in both segments as anchors
* Recursively aligns between trustworthy anchors
* Leaves ambiguous regions unmapped (conservative, not maximal matching)

**Key Design Choice:** Rather than maximizing matches via generic LCS, the algorithm prioritizes reliability by only mapping lines where structural evidence is clear, leaving uncertain regions unmapped.

**Example:**

```
Source: [A, B, C, D]
Working: [A, X, C, D]

Mapping result:
  source[1] → working[1]  (A matches)
  source[2] → unmapped    (B replaced by X)
  source[3] → working[3]  (C matches)
  source[4] → working[4]  (D matches)
```

**Design Principle: Prefer Unmapped Over Incorrect**

When structural correspondence is ambiguous, the matcher intentionally leaves regions unmapped rather than guessing.

This conservative approach ensures:

* correctness over completeness
* no accidental line conflation
* predictable behavior in the presence of repeated or reordered content

All higher-level operations (merge, discard, attribution) are designed to tolerate unmapped regions safely.

### Line Mapping

From LCS alignment, we build a bidirectional mapping:

```python
@dataclass
class LineMapping:
    source_to_target: dict[int, int]  # source line → working line
    target_to_source: dict[int, int]  # working line → source line
```

**Example:**

```
Batch Source:         Working Tree:      Mapping:
1: line1             1: line1           source[1] → working[1]
2: line2             2: extra           (no mapping for line 2)
3: line3             3: line3           source[3] → working[3]
4: line4             (deleted)          (no mapping for line 4)
```

### Using Mapping for Merge

**Algorithm:** `_apply_presence_constraints(source_lines, working_lines, claimed_line_set)`

1. Build line mapping from source to working
2. For each claimed source line:

   * Check if `mapping.get_target_line_from_source_line(claimed_line)` exists
   * If exists: line already present, keep working tree version
   * If missing: insert from batch source at structurally appropriate position
3. Preserve all working tree extras (lines not in source)

**Key Insight:** We walk through **batch source order**, using the mapping to:

* Identify which lines already exist in working tree
* Determine insertion positions for missing lines
* Preserve structural coherence

**Example:**

```python
claimed_line_set = {1, 3}  # Claim lines 1 and 3 from batch source

# Walk source order
for source_line in [1, 2, 3, 4]:
    working_line = mapping.get_target_line_from_source_line(source_line)

    if working_line is not None:
        # Source line exists in working tree
        if source_line in claimed_line_set:
            # Add claimed version from source
            result.append(source_lines[source_line - 1])
        else:
            # Add working tree version
            result.append(working_lines[working_line - 1])
    else:
        # Source line missing from working tree
        if source_line in claimed_line_set:
            # Insert it
            result.append(source_lines[source_line - 1])
```

---

## Display vs Merge Logic

**Important:** Diff output (`git diff`) is used for display and user interaction only.

All semantic reasoning (ownership, attribution, merging, and filtering) is derived from:

* file content comparisons
* structural alignment
* constraint evaluation

Diff hunks are projections of this underlying model, not the source of truth.

### Three Different Purposes

1. **Display Logic:** Show user what's in the batch (for review/selection)
2. **Merge Logic:** Apply batch constraints to working tree (add batch changes)
3. **Discard Logic:** Reverse batch constraints from working tree (remove batch changes)

These serve different needs and handle deletions differently.

### Display: Show Everything

**Purpose:** User needs to see what the batch contains, including deletions.

**Implementation:** `build_display_lines_from_batch_source()`

```python
display_lines = [
    {"id": 1, "type": "claimed", "source_line": 1, "content": "line1\n"},
    {"id": 2, "type": "deletion", "deletion_index": 0, "content": "line2\n"},
    {"id": 3, "type": "claimed", "source_line": 2, "content": "line2-modified\n"},
]
```

**Why Show Deletions:** Users need to understand the full scope of changes:

```
# Auto-created
file.txt :: @@ -1,1 +1,3 @@
[#1] + line1
[#2] - line2              ← Deletion shown for context
[#3] + line2-modified
[#4] + line3
```

**Line ID Assignment:** Sequential ephemeral IDs (1, 2, 3...) for display and selection.

### Merge: Apply Constraints

**Purpose:** Modify working tree to satisfy batch constraints (add batch changes).

**Conceptual Model:** Build the result under constraints - claimed lines must be present, forbidden sequences must not survive at their anchored positions.

**Implementation:** `merge_batch()` in `src/git_stage_batch/batch/merge.py`

```python
def merge_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes
) -> bytes:
    # Works with bytes throughout - no lossy encoding
    
    # Pass 1: Ensure all claimed lines are present
    # Build result with structural alignment, incorporating claimed source lines
    result = _apply_presence_constraints(source_lines, working_lines, claimed_set)

    # Pass 2: Ensure forbidden sequences don't survive
    # Suppress sequences at their structurally aligned positions
    result = _apply_absence_constraints(result, deletion_claims)

    return b"".join(entry.content for entry in result)
```

**Idempotence Guarantee:** Applying the same batch multiple times yields the same result. This is enforced by constraint evaluation rather than operation replay.

**Key Properties:**

* **Bytes-based:** Works with raw bytes, no lossy UTF-8 decoding
* **Conservative matching:** Uses LCS-based `match_lines()` for structural alignment
* **Anchored deletions:** Absence constraints are enforced at exact structural positions, not globally
* **Idempotent:** Applying the same batch multiple times yields the same result
* **Preserves extras:** Working tree content not in batch source is preserved

### Discard: Reverse Constraints

**Purpose:** Remove batch changes from working tree (inverse of merge).

**Conceptual Model:** Reverse the batch's presence and absence constraints:

* **Reverse presence:** Remove or restore batch-claimed content to baseline
* **Restore absence:** Re-insert batch-deleted sequences at their original boundaries

**Implementation:** `discard_batch()` in `src/git_stage_batch/batch/merge.py`

```python
def discard_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes,
    baseline_content: bytes
) -> bytes:
    # Works with bytes throughout - no lossy encoding
    
    # Step 1: Build baseline restoration correspondence
    # Classify regions: EQUAL, INSERT, REPLACE_LINE_BY_LINE, REPLACE_BY_HUNK
    correspondence = _build_baseline_correspondence(baseline_lines, source_lines)

    # Step 2: Build realized entries with source provenance
    realized_entries = _build_realized_entries_for_discard(
        source_lines, working_lines, working_to_source
    )

    # Step 3: Reverse presence constraints
    # - EQUAL/REPLACE_LINE_BY_LINE: restore individual baseline lines
    # - INSERT: remove (batch-added content)
    # - REPLACE_BY_HUNK: verify full ownership, restore entire baseline block
    result = _reverse_presence_constraints(
        realized_entries, claimed_line_set, source_lines, baseline_lines, correspondence
    )

    # Step 4: Restore absence constraints
    # Re-insert batch-deleted sequences at their original anchored boundaries
    result = _restore_absence_constraints(result, deletion_claims)

    return b"".join(entry.content for entry in result)
```

**Region Classification:**

Discard uses structural analysis to classify how each region should be restored:

* **EQUAL**: Unchanged lines → restore line-by-line (trivial)
* **INSERT**: Batch-added content → remove entirely
* **REPLACE_LINE_BY_LINE**: Same-size changes where `match_lines()` proves unambiguous 1:1 correspondence → restore line-by-line
* **REPLACE_BY_HUNK**: Different-size changes OR conservative matching cannot prove 1:1 correspondence → requires full ownership, restore as atomic unit

**Critical:** Line-by-line restoration requires proven structural correspondence, not merely equal-sized regions. If conservative matching leaves lines unmapped (e.g., duplicate content, reordered blocks), the region is classified as REPLACE_BY_HUNK to prevent incorrect partial restoration.

**Partial Ownership Handling:**

For REPLACE_BY_HUNK regions:

* If batch owns **all lines** in region → restore entire baseline block
* If batch owns **some lines** in region → raise `MergeError` (cannot safely restore partial)
* If batch owns **no lines** in region → keep as-is

This prevents "half-hunk restoration" corruption where only part of a structurally inseparable change is reversed.

**Key Properties:**

* **Bytes-based:** Same bytes-level correctness as merge
* **Structural inverse:** The inverse of `merge_batch()` under the same ownership model, not a separate patching system
* **Conservative restoration:** Uses `match_lines()` to verify 1:1 correspondence for line-by-line regions
* **Ambiguity intolerant:** Raises errors rather than guessing when structure is unclear
* **Anchored restoration:** Deleted sequences re-inserted at exact original boundaries

### Current Selection Behavior for Deletions

When user selects lines by ID for operations like `apply --from batch --line 5`:

```python
# User selected line 5 (a deletion)
selected_ids = {5}

filtered_ownership = filter_batch_by_display_ids(ownership, source, selected_ids)
# Result: claimed_lines=[], deletions=[]
```

**Architectural Capability vs Current UI Policy:**

The constraint model **fully supports**:

* **Replacement-style changes**: Deletion coupled with claimed presence line (e.g., "suppress old_impl, add new_impl")
* **Pure removal changes**: Deletion-only ownership (e.g., "suppress debug_log block")

Both `merge_batch()` and `discard_batch()` correctly handle deletion-only ownership:

* **Merge**: Suppresses forbidden sequences at their anchored positions
* **Discard**: Re-inserts deleted sequences at their original boundaries

However, the **current UI behavior** excludes deletions from line-level selection because:

* Line-level selection (`--line 5`) focuses on content to include, not suppressions to apply
* For replacement changes, the deletion is contextual metadata for the claimed line
* For pure removals, users select the entire hunk/file scope, not individual deletion display lines

This is a **product choice**, not an architectural limitation. The underlying merge/discard implementation fully supports deletion-only selections. The UI could be extended to distinguish "select this deletion claim" from "select this claimed content line" if desired.

### Line ID Filtering

```python
def filter_batch_by_display_ids(ownership, source, selected_ids):
    display_lines = build_display_lines_from_batch_source(source, ownership)

    filtered_claimed_set = set()
    for line in display_lines:
        if line["id"] in selected_ids and line["type"] == "claimed":
            filtered_claimed_set.add(line["source_line"])

    # Deletions excluded: they're metadata, not selectable content
    return BatchOwnership(
        claimed_lines=format_ranges(filtered_claimed_set),
        deletions=[]
    )
```

---

## Ownership Units and Semantic Selection

Ownership units operate entirely in **batch source coordinate space** and are independent of working tree structure.

They represent:

* how ownership constraints group semantically for user interaction
* not how changes appear in the working tree

This is distinct from attribution units, which operate in working tree space.

### The Problem: Coupled Changes

Line-level ownership alone doesn't capture the semantic relationships between changes. Consider:

```python
ownership = BatchOwnership(
    claimed_lines=["2"],
    deletions=[DeletionClaim(anchor_line=1, content_lines=[b"old_line\n"])]
)
```

Is this:

* A **replacement** (delete old_line, add new line at position 2) that should be treated atomically?
* Or **independent** changes (deletion + separate addition) that can be selected separately?

The ownership model describes **what** is claimed, but not **how** those claims group semantically.

### Solution: Ownership Units

**Ownership units** are semantic groupings of ownership claims based on **display adjacency**:

```python
@dataclass
class OwnershipUnit:
    kind: OwnershipUnitKind           # REPLACEMENT, DELETION_ONLY, PRESENCE_ONLY
    claimed_source_lines: set[int]    # Source lines owned by this unit
    deletion_claims: list[DeletionClaim]  # Deletion claims in this unit
    display_line_ids: set[int]        # Display IDs that map to this unit
    is_atomic: bool                   # If True, partial selection not allowed
    atomic_reason: str | None         # Explanation for atomicity
```

**Three Unit Types:**

1. **REPLACEMENT** (atomic): Deletion block adjacent to claimed line(s) in display

   * User sees as "replace X with Y"
   * Must select all or nothing (prevents orphaned deletions)

2. **DELETION_ONLY** (atomic): Deletion block with no adjacent claimed lines

   * Pure removal operation
   * Must select entire deletion block together

3. **PRESENCE_ONLY** (non-atomic): Claimed line(s) with no adjacent deletions

   * Pure addition operation
   * Can be selected independently

### Display Adjacency Grouping

**Key Principle:** Grouping is based on **reconstructed display order**, not source-line proximity.

**Display Reconstruction Order:**

1. Start-of-file deletions (anchor_line=None) shown first
2. Then for each source position in order:

   * Claimed line at that position (if any)
   * Deletions anchored at that position (if any)

**Grouping Rules:**

```python
def build_ownership_units_from_display(ownership, batch_source_content):
    """Groups display lines into semantic units based on adjacency.
    
    Rules:
    - Deletion block immediately followed by claimed line → REPLACEMENT
    - Claimed line immediately followed by deletion block → REPLACEMENT  
    - Deletion block with no adjacent claimed line → DELETION_ONLY
    - Claimed line with no adjacent deletion → PRESENCE_ONLY
    
    Claimed lines are processed individually (not as blocks) to preserve
    fine-grained reset capability. When a deletion block is followed by
    multiple claimed lines, only the first couples to form REPLACEMENT.
    """
```

**Example:**

```
Source content: line1, line2, line3, line4

Ownership:
- Delete line 1 (anchor=None)
- Add line 2 (claimed)
- Delete line 4 (anchor=3)

Display reconstruction:
[#1] - line 1          ← start-of-file deletion
[#2] + new line 2      ← claimed at position 2
[#3] - line 4          ← deletion anchored after line 3

Units created:
1. REPLACEMENT (atomic):
   - display_line_ids: {1, 2}
   - deletion_claims: [DeletionClaim(anchor=None, ...)]
   - claimed_source_lines: {2}
   
2. DELETION_ONLY (atomic):
   - display_line_ids: {3}
   - deletion_claims: [DeletionClaim(anchor=3, ...)]
   - claimed_source_lines: {}
```

**Critical Design Choice:** Source-line proximity is **not considered**. Only display adjacency matters.

```
Bad (old ±2 heuristic):  "couple if abs(anchor - claimed) <= 2"
Good (display adjacency): "couple if consecutive in display reconstruction"
```

This ensures grouping reflects what the user **actually sees**, not arbitrary numeric thresholds.

### Semantic Selection Operations

Ownership units enable operations like `reset --line ID` to work semantically:

```python
def select_ownership_units_by_display_ids(units, selected_ids):
    """Select units that match display IDs, enforcing atomic constraints.
    
    Raises MergeError if:
    - User partially selects an atomic unit (REPLACEMENT or DELETION_ONLY)
    
    Allows:
    - Full selection of any unit
    - Partial selection of PRESENCE_ONLY units (non-atomic)
    """
```

**Example:**

```bash
# Display shows:
[#1] - old_impl
[#2] + new_impl  
[#3] + extra_feature

# User tries: reset --line 1
# Error: Cannot partially select atomic unit
#        Unit is REPLACEMENT containing lines [1, 2]
#        Must select both or neither

# User tries: reset --line 1,2
# Success: Full REPLACEMENT unit selected

# User tries: reset --line 3
# Success: PRESENCE_ONLY unit, independently selectable
```

**Implementation:** `src/git_stage_batch/batch/ownership.py`

* `build_ownership_units_from_display()`: Constructs units from ownership
* `select_ownership_units_by_display_ids()`: Filters units by display IDs
* `filter_ownership_units_by_display_ids()`: Partitions into kept/removed
* `rebuild_ownership_from_units()`: Reconstructs ownership from units

**Commands Using Semantic Selection:**

* `reset --from BATCH --line IDs`: Remove lines from batch (enforces atomicity)
* `apply --from BATCH --line IDs`: Apply subset of batch (future: could enforce atomicity)
* `discard --from BATCH --line IDs`: Remove subset of batch from working tree

---

## Stale Batch Source Detection and Repair

### The Problem: Batch Source Drift

**Scenario:**

```
1. User includes lines to batch (batch source created from working tree)
2. User edits working tree (working tree diverges from batch source)
3. User includes more lines (selected lines reference stale batch source)
```

**Issue:** New selections have `source_line=None` because diff parser annotates against working tree, but batch source still references old snapshot.

**Without repair:**

```python
# Batch source: line1, line2, line3
# Working tree: line1, line2-edited, line3, line4

# User selects line 2 from diff (source_line=None)
# Cannot add to batch: no valid source line reference
```

### Solution: Automatic Batch Source Advancement

**When detected:** Lines with `source_line=None` indicate stale batch source.

**Repair process:**

1. Create new batch source commit from the current working tree plus any already-owned presence lines that are absent from the working tree
2. Remap existing ownership from old source space to new source space
3. Update session batch-source cache
4. Re-annotate selected lines against new source

**Implementation:** `src/git_stage_batch/batch/source_refresh.py`

```python
@dataclass
class RefreshedBatchSelection:
    """Selection state after ensuring batch source is current.
    
    If source was stale: advanced, ownership remapped, lines re-annotated
    If not stale: original state unchanged
    """
    batch_source_commit: str | None
    ownership: BatchOwnership | None
    selected_lines: list
    source_was_advanced: bool

def ensure_batch_source_current_for_selection(
    batch_name: str,
    file_path: str,
    current_batch_source_commit: str | None,
    existing_ownership: BatchOwnership | None,
    selected_lines: list,
) -> RefreshedBatchSelection:
    """Single authoritative path for stale-source repair.
    
    Process:
    1. Detect stale source (source_line=None in selected lines)
    2. If stale:
       - Create new batch source from working tree content while preserving already-owned presence lines
       - Remap ownership to new source space
       - Update session cache
       - Re-annotate lines for new source
    3. Return structured result
    """
```

**Invariant:** An advanced batch source is a coordinate snapshot that must contain:

* the current working tree content needed for the new selection
* all already-owned presence lines needed to keep existing ownership constraints expressible

For `include --to`, this often equals the current working tree snapshot. For `discard --to`, it may not: earlier discard operations intentionally remove already-owned lines from the working tree, but the batch still needs those lines in its batch source so `claimed_lines` remain valid.

Reannotation therefore maps selected working-tree lines against the actual advanced source:

```python
mapping = match_lines(advanced_source_lines, working_tree_lines)
working_tree_line_N → mapping.get_source_line_from_target_line(N)
```

**High-Level Orchestration:**

````python
@dataclass  
class PreparedBatchUpdate:
    """Complete ownership update ready to persist."""
    batch_source_commit: str | None
    ownership_before: BatchOwnership | None
    ownership_after: BatchOwnership

def prepare_batch_ownership_update_for_selection(
    batch_name: str,
    file_path: str,
    current_batch_source_commit: str | None,
    existing_ownership: BatchOwnership | None,
    selected_lines: list,
) -> PreparedBatchUpdate:
    """High-level helper for include/discard-to-batch operations.
    
    Coordinates:
    1. Ensure batch source is current (may advance + remap)
    2. Translate selected lines to ownership
    3. Merge with existing ownership
    4. Return prepared update
    
    Commands use this 4-line pattern instead of manual coordination:
    ```
    update = prepare_batch_ownership_update_for_selection(...)
    ownership = update.ownership_after
    save_to_batch(ownership)
    ```
    """
````

**Session Cache Management:**

Session cache (`.git/git-stage-batch/session-batch-sources.json`) tracks batch source per file:

```json
{
  "src/main.py": "abc123...",
  "src/utils.py": "def456..."
}
```

Cache update happens **inside** `ensure_batch_source_current_for_selection()`, not in calling code. This keeps the update tied to source advancement.

**Commands Using Centralized Helpers:**

All 8 command paths (include/discard × file/lines/hunk/entire-file) use `prepare_batch_ownership_update_for_selection()`:

* `include --to BATCH --line IDs`
* `include --to BATCH --file`
* `discard --to BATCH --line IDs`
* `discard --to BATCH --file`

**Why Centralization Matters:**

Before centralization:

* 40+ lines of duplicate stale-source logic in each command
* Easy to forget session cache update
* Inconsistent reannotation strategies

After centralization:

* 4 lines per command: call helper, extract ownership, save
* Session cache update impossible to forget (inside helper)
* Single reannotation strategy, guaranteed correct

---

## Storage Architecture

### Logical Model vs Serialization

The batch system has two distinct layers:

1. **Logical Model**: The constraint-based architecture (presence/absence constraints, structural alignment)
2. **Current Serialization**: How these constraints are persisted to disk

It helps to distinguish these because the serialization format may evolve while the logical model remains stable.

### Logical Model

**Conceptually**, a batch consists of:

* **Baseline commit**: The "before" reference point
* **Batch source commit(s)**: Stable coordinate system for ownership
* **Ownership constraints** per file:

  * **Presence constraints**: Set of source line numbers that must exist in result
  * **Absence constraints**: List of structurally anchored forbidden sequences

    * Each deletion claim is independent with its own anchor
    * The anchor is structural (part of the constraint's identity)
    * The forbidden content is matched at its aligned location

**Key Properties:**

* Each `DeletionClaim` is a separate entity (not collapsed by position or content)
* Anchors are semantic (define where the constraint applies)
* Content bytes are stored for matching but represent a constraint, not replay data

### Current Serialization Format

**Directory Structure:**

```
.git/git-stage-batch/
├── batches/
│   ├── feature-a/
│   │   └── metadata.json      ← Batch ownership data
│   ├── fix/
│   │   └── metadata.json
│   └── cleanup/
│       └── metadata.json
├── session-batch-sources.json  ← Batch source commits for session
└── [other session state files]
```

**Batch Metadata Format:**

**File:** `.git/git-stage-batch/batches/<batch-name>/metadata.json`

```json
{
  "note": "Feature A implementation",
  "created_at": "2026-04-12T10:30:00Z",
  "baseline": "abc123...",
  "files": {
    "src/main.py": {
      "batch_source_commit": "def456...",
      "claimed_lines": ["2-5", "10"],
      "deletions": [
        {
          "after_source_line": 1,
          "blob": "789abc..."
        }
      ],
      "mode": "100644"
    }
  }
}
```

**Serialization Details:**

* **`baseline`**: SHA of baseline commit (shared across all files in batch)
* **`batch_source_commit`**: SHA of batch source for this file
* **`claimed_lines`**: Ranges of lines claimed from batch source (presence constraints)
* **`deletions`**: Array of deletion claims (absence constraints)

  * **`after_source_line`**: Structural anchor (null = start of file, before line 1)
  * **`blob`**: Git blob SHA containing the forbidden sequence

    * This is a **serialization optimization** (deduplication via content-addressing)
    * Semantically, it represents an absence constraint, not content to replay
* **`mode`**: Git file mode (100644 = regular file, 100755 = executable)

**Important:** The use of blob SHAs for deletions is a storage mechanism (content-addressed deduplication). The logical meaning is still "this sequence at this anchored location must not exist," not "replay this blob."

**Why store content if deletions are constraints?**

It may seem contradictory to say "deletions are not content" and then store them as blobs. The distinction is:

* **Logical model**: The deletion represents an absence constraint
* **Serialization**: The content bytes are needed to identify what sequence is forbidden

The blob is not an operation payload. It is a compact way to persist the identity of the forbidden sequence.

### Session Batch Sources

**File:** `.git/git-stage-batch/session-batch-sources.json`

Tracks the current batch source commit for each file during an active session:

```json
{
  "src/main.py": "def456...",
  "src/utils.py": "ghi789..."
}
```

**Purpose:** When user adds more content to a batch later in the session, the system needs to know which batch source commit to use as reference.

**Lifecycle:**

* Created/updated by `git-stage-batch start`, `include --to`, `discard --to`
* Cleared by `git-stage-batch stop` / `abort`
* Updated automatically when stale batch source is repaired

---

## Attribution-Based Filtering

Attribution-based filtering determines which parts of the *current working tree* are already owned by batches.

This is computed using a **file-centric model**:

1. Compare baseline ↔ working tree to derive semantic change units
2. Attribute those units to zero or more batches using batch ownership constraints
3. Project attributed units onto diff output for display filtering

This replaces earlier diff-centric approaches. The system no longer infers ownership from diff hunks directly; instead, diff is treated as a presentation layer over a file-derived attribution model.

### The Challenge: Hiding Already-Batched Content

When iterating through working tree changes, the system needs to hide content that's already been saved to batches. Otherwise users would see the same changes repeatedly:

```
Working tree: line1-modified, line2-added, line3-modified

Batches:
  feature-a: [line1-modified]
  fix:       [line3-modified]

Next hunk shown to user should only contain: line2-added
```

### File-Centric Attribution Model

The system uses a file-centric, blame-like model:

* Build a canonical comparison between baseline and working tree
* Enumerate semantic attribution units from that comparison
* Supplement with batch-owned obligations that may not currently be visible
* Attribute each unit to zero or more batches
* Project those attributed units onto displayed diff hunks

The system no longer assumes that the current diff output fully describes every ownership-relevant unit.

### Attribution Units vs Ownership Units

Attribution uses its own unit model in **working tree space**:

* **Attribution units** answer: “Which current working tree units are already owned by which batches?”
* **Ownership units** answer: “How should batch-source claims group semantically for selection and atomicity?”

These are intentionally different layers.

### Why Attribution Is Derived

Attribution is not stored in metadata because it depends on the current working tree. As the working tree evolves, attribution must be recomputed.

This allows the system to:

* continue hiding already-batched changes after unrelated edits
* preserve working tree extras that no batch owns
* remain correct even when multiple batches touch the same file

### Filtering Process

Conceptually:

1. Build `FileAttribution` for the file
2. Project attributed units onto the displayed hunk
3. Hide only diff fragments whose units are owned by one or more batches

This means filtering is:

* **file-centric** in its reasoning
* **diff-centric** only in presentation

### Further Implementation Notes

The attribution implementation lives in `src/git_stage_batch/batch/attribution.py` and is exercised through hunk iteration in `src/git_stage_batch/data/hunk_tracking.py`.

---

## Sifting: Reconciling Batches Against Current Tip

### The Problem: Batch Clarity After Partial Landing

**Scenario:**

After ad hoc history surgery, some portions of a batch may have landed in history while others remain unapplied:

```
Original batch intent:
  - Change A (line 10 → line 10-modified)
  - Change B (line 20 → line 20-modified)
  - Change C (line 30 → line 30-modified)

After manual commits/rebases:
  - Change A: already in history ✓
  - Change B: still needs to be applied
  - Change C: already in working tree ✓
```

**The Actual Problem (Not Duplication):**

The batch still claims ownership of all three changes. When you `show` the batch or try to understand its contents, you see:

```
[#1] Change A (already done)
[#2] Change B (still needs work)
[#3] Change C (already done)
```

**Why This Matters:**

* **Unclear what work remains**: You can't easily tell which changes still need to be applied
* **Harder to review**: The batch contains a mix of done and undone work
* **Mental overhead**: You have to manually track which portions are still relevant

**Note on Idempotence:** Applying the full batch wouldn't break anything - the constraint-based merge model is idempotent, so already-present changes won't duplicate. But you still want a **clean representation** of the remaining work.

**Solution:** `sift` removes already-present portions from a batch, creating a new batch containing only the remaining delta. The result is a clearer, more focused batch that accurately represents what's still needed.

### Semantic Model

Sift answers the question:

> What portions of the source batch's target are **not yet present** in the current working tree?

This is not a simple diff. It requires understanding:

* What the source batch **wants to achieve** (its target content)
* What the working tree **already has** (current state)
* The **structural delta** between them (what's still needed)

**Terminology Note:** "Target content" refers to what the sifted batch wants to achieve. We reserve "realized content" for the intermediate result when realizing the original source batch against its baseline.

### Conceptual Model: Derivation, Not Filtering

Sift is spiritually equivalent to replaying `apply --from` followed by `discard --to` on the working tree. It is not implemented that way, however. Instead, it re-derives the minimal ownership constraints needed to transform the current working tree into the batch's target content.

This is significant because it means groupings of lines from the original batch may not map 1:1 into the sifted batch, and may instead be split, re-anchored, or reclassified based on their structural relationship to the current working tree. This added flexibility allows sift to work in cases where an apply/discard workflow would not.

**Why Derivation Matters:**

* Original batch may have grouped changes that are now at different structural positions
* Working tree may have intermediate edits that require re-anchoring deletion claims
* Conservative structural matching may classify regions differently in the new context
* The sifted ownership is expressed in target-content coordinate space, not source batch space
* Sift only treats a line as matched when the structural alignment is reciprocal:
  working → target and target → working must agree on the same pair of line
  numbers. This keeps derived deletion anchors compatible with the later
  `merge_batch()` direction.

### Algorithm Overview

```python
def sift(source_batch, dest_batch, file_path):
    # 1. Realize the source batch to get target content
    target_content = realize_batch(source_batch, file_path)
    
    # 2. Get current working tree state
    working_content = read_working_tree(file_path)
    
    # 3. Derive fresh ownership from working → target delta
    ownership = derive_ownership_from_delta(
        working_content,
        target_content
    )
    
    # 4. Validate semantic correctness
    validate_that_merge_produces_target(
        ownership,
        target_content,
        working_content
    )
    
    # 5. Persist sifted batch with special semantics
    persist_sifted_batch(dest_batch, target_content, ownership)
```

### Step 1: Realize the Source Batch to Get Target Content

First, we realize the source batch against its baseline to determine the target content:

```python
baseline_content = read_git_blob(f"{baseline}:{file_path}")
batch_source_content = read_git_blob(f"{batch_source}:{file_path}")
ownership = BatchOwnership.from_metadata_dict(source_metadata)

realized_content = _build_realized_content(
    baseline_content,
    batch_source_content,
    ownership
)
```

**Example:**

```
Baseline:     line1, line2, line3
Batch source: line1, lineX, line2-mod, line3
Ownership:    claimed=[2,3], deletions=[line2 after line1]

Target content: line1, lineX, line2-mod, line3
                (realized by applying batch ownership to baseline)
```

### Step 2: Compare Working Tree to Target Content

The working tree may differ from the target content:

```
Target content:  line1, lineX, line2-mod, line3
Working tree:    line1, lineX, line3, lineY

Delta:
  - lineX is present ✓
  - line2-mod is absent (still needs to be added)
  - line3 is present ✓
  - lineY is extra (working tree addition, not in batch)
```

### Step 3: Derive Fresh Ownership from Delta

Sift uses **project-native comparison logic** to derive ownership representing the working → target transformation:

```python
from git_stage_batch.batch.comparison import derive_semantic_change_runs

semantic_runs = derive_semantic_change_runs(
    source_lines=working_lines,  # What we have now
    target_lines=target_lines     # What we want to achieve
)
```

The line comparison is intentionally conservative. A candidate match is accepted
only if the reverse comparison maps the target line back to the same working
line. Non-reciprocal matches are treated as unmatched on both sides, producing
presence and/or absence constraints instead of an anchor that merge may not be
able to realize later.

The comparison produces three types of semantic change units:

* **PRESENCE**: Pure addition in target (no coupled deletion from source)
* **DELETION**: Pure deletion from source (no coupled addition in target)
* **REPLACEMENT**: Deletion from source paired with addition in target

**Conservative Pairing Strategy:**

The pairing algorithm groups runs by their **structural anchors** (the last matched line before the run):

```python
# Group source and target runs by anchor
for anchor in common_anchors:
    source_candidates = source_runs_by_anchor[anchor]
    target_candidates = target_runs_by_anchor[anchor]
    
    # Only pair if exactly 1-to-1 (conservative)
    if len(source_candidates) == 1 and len(target_candidates) == 1:
        emit_replacement(source_run, target_run, anchor)
    else:
        # Ambiguous - emit as separate deletion + presence
        emit_deletion(source_run, anchor)
        emit_presence(target_run)
```

This **avoids incorrect pairing** with repeated or similar lines by requiring unambiguous 1-to-1 structural correspondence.

**Translation to Ownership:**

```python
claimed_lines: list[int] = []
deletion_claims: list[DeletionClaim] = []

for run in semantic_runs:
    if run.kind == PRESENCE:
        claimed_lines.extend(run.target_run)
    
    elif run.kind == DELETION:
        deletion_claims.append(DeletionClaim(
            anchor_line=run.target_anchor,
            content_lines=[working_lines[i-1] for i in run.source_run]
        ))
    
    elif run.kind == REPLACEMENT:
        # Both deletion and claimed lines
        claimed_lines.extend(run.target_run)
        deletion_claims.append(DeletionClaim(
            anchor_line=run.target_anchor,
            content_lines=[working_lines[i-1] for i in run.source_run]
        ))
```

**Coordinate Space:** The derived ownership is in **target-content coordinate space** (claimed lines reference target line numbers, deletions contain working-tree content).

### Step 4: Semantic Validation

Sift validates that the derived ownership correctly represents the intended transformation using **three levels**:

**Level A: Bounds Check**

```python
for claimed_line in ownership.claimed_lines:
    assert 1 <= claimed_line <= len(target_lines)

for deletion in ownership.deletions:
    assert deletion.anchor_line is None or \
           1 <= deletion.anchor_line <= len(target_lines)
```

**Level B: Deletion Structure**

```python
for deletion in ownership.deletions:
    assert len(deletion.content_lines) > 0
```

**Level C: Semantic Correctness**

The validation proves the invariant:

```python
from git_stage_batch.batch.merge import merge_batch

reconstructed = merge_batch(
    batch_source_content=target_content,  # Sifted batch source (the target)
    ownership=ownership,                  # Derived ownership
    working_content=working_content       # Current working tree
)

assert reconstructed == target_content
```

This validates that applying the sifted batch to the current working tree produces the exact target content the original batch wanted to achieve.

**Why This Matters:** Validation uses `merge_batch` against the **working tree** (merge-time semantics), proving the persistent representation will behave correctly when the batch is later applied.

### Step 5: Persistence Model

**Critical Design Choice:** Sifted batches use intentionally different persistence semantics than ordinary batches.

**Ordinary Batch Persistence:**

```
Batch source = snapshot from working tree when content was saved
Batch commit = baseline + ownership applied to batch source
Ownership    = in batch-source coordinate space
```

**Sifted Batch Persistence (Intentional Exception):**

```
Batch source = target content (what batch wants to achieve)
Batch commit = target content (stored directly)
Ownership    = in target-content coordinate space
```

**Why Different?**

For sifted text batches specifically, the destination batch source **is the target content itself**, not a historical working tree snapshot. This is necessary because:

1. The target content may not exist anywhere in history
2. The ownership describes how to **merge this target with the current working tree**, not how to apply it to a specific baseline
3. **For sifted batches only**: both the batch source commit and batch commit contain the same content (the target), which differs from the ordinary three-commit model where the batch commit is derived from baseline + ownership

This is a deliberate exception to the general batch storage model, used only for sifted text batches. The ordinary baseline-centered model remains unchanged for all other batches.

**Implementation:**

```python
def add_sifted_text_file_to_batch(
    batch_name: str,
    file_path: str,
    target_content: bytes,  # The realized target
    ownership: BatchOwnership,
    file_mode: str = "100644"
) -> None:
    # Create synthetic batch source containing target content
    batch_source_commit = create_synthetic_batch_source_commit(
        baseline_commit,
        file_path,
        target_content  # Store target directly
    )
    
    # Batch commit also contains target directly
    target_blob_sha = create_git_blob([target_content])
    
    # Persist metadata with ownership in target-content space
    metadata["files"][file_path] = {
        "batch_source_commit": batch_source_commit,
        **ownership.to_metadata_dict(),
        "mode": file_mode
    }
    
    # Update batch commit ref
    _update_batch_commit(batch_name, file_path, target_blob_sha, file_mode)
```

**Validation Still Works:**

Even though persistence is different, the merge-time semantics remain correct:

```python
# When the sifted batch is later applied to working tree W:
result = merge_batch(
    batch_source_content=target_content,  # From sifted batch
    ownership=sifted_ownership,           # Derived ownership
    working_content=W                     # Any working tree
)
# Result will correctly merge target with W
```

The validation in step 4 proved this invariant holds for the **current** working tree. The constraint-based model is designed to support later merges into structurally compatible working trees, though validation only proves correctness for the working tree state at sift time.

### Mode: Copy vs In-Place

Sift supports two modes:

**Copy Mode** (`--from source --to dest` where source ≠ dest):

```bash
git-stage-batch sift --from feature-cleanups --to feature-cleanups-pruned
```

* Creates new destination batch
* Source batch preserved unchanged
* Useful for keeping original batch as backup

**In-Place Mode** (`--from source --to source`):

```bash
git-stage-batch sift --from feature-cleanups --to feature-cleanups
```

* Updates source batch in place
* Uses atomic replacement via temporary batch
* Rollback on validation failure

**Atomic In-Place Implementation:**

```python
def _perform_atomic_in_place_sift(batch_name, source_metadata, ...):
    temp_batch_name = f"{batch_name}-sift-temp"
    
    try:
        # 1. Build temp batch with sifted content
        create_batch(temp_batch_name, "Temporary sift")
        # ... compute and add sifted files ...
        
        # 2. Validate temp batch
        validate_batch_metadata(temp_batch_name)
        
        # 3. Atomic replacement via directory rename
        source_dir = get_batch_directory_path(batch_name)
        temp_dir = get_batch_directory_path(temp_batch_name)
        backup_dir = batches_dir / f"{batch_name}-sift-backup"
        
        shutil.move(source_dir, backup_dir)  # 1. Move original to backup
        try:
            shutil.move(temp_dir, source_dir)  # 2. Move temp to original location
            shutil.rmtree(backup_dir)          # 3. Delete backup
            # Update git refs...
        except Exception:
            # Rollback on failure
            shutil.move(backup_dir, source_dir)
            raise
    finally:
        # Clean up temp batch
        if batch_exists(temp_batch_name):
            delete_batch(temp_batch_name)
```

### Binary Files

For binary files, sift uses **byte-for-byte comparison**:

```python
def _compute_sifted_binary_file(source_batch, file_path, file_meta, repo_root):
    # Read batch source (original binary content)
    batch_source_content = read_git_blob(...)
    
    # Read current working tree
    working_content = read_working_tree(file_path)
    
    # Byte-for-byte comparison
    if working_content == batch_source_content:
        return None  # Already present, omit from sifted batch
    else:
        return {"type": "binary", "change": file_meta}
```

Binary files have no line-level ownership - they're atomic units that are either present or not.

### Example Walkthrough

**Initial State:**

```
Baseline (HEAD):  line1, line2, line3, line4

User creates batch "feature-x":
  - Modifies line2 → line2-mod
  - Adds lineX after line2
  - Deletes line4

Batch "feature-x" target content:
  line1, line2-mod, lineX, line3
```

**User Makes Manual Changes:**

```bash
# User manually applies part of the batch's intent
echo "line1" > file.txt
echo "line2-mod" >> file.txt  # This change from batch is present
echo "line3" >> file.txt
echo "lineY" >> file.txt      # Extra working tree content

# Working tree now:
line1, line2-mod, line3, lineY
```

**Sift Batch:**

```bash
git-stage-batch sift --from feature-x --to feature-x-remaining
```

**Process:**

1. **Target content** (from realizing feature-x): `line1, line2-mod, lineX, line3`
2. **Working tree**: `line1, line2-mod, line3, lineY`
3. **Comparison**:
   * Source (working): `[line1, line2-mod, line3, lineY]`
   * Target: `[line1, line2-mod, lineX, line3]`
   * Alignment: line1↔line1, line2-mod↔line2-mod, line3↔line3
   * Unmatched in source: `lineY`
   * Unmatched in target: `lineX`

4. **Semantic runs**:
   * DELETION: lineY (working line 4, anchor=line3)
   * PRESENCE: lineX (realized line 3, no coupled deletion)

5. **Derived ownership** (in target-content space):
   ```python
   claimed_lines=[3]  # lineX at position 3 in target content
   deletions=[
       DeletionClaim(
           anchor_line=3,  # After target line 3 (line3)
           content_lines=[b"lineY\n"]
       )
   ]
   ```
   
   **Understanding the Deletion Claim:** The deletion claim represents an **absence constraint in target-content coordinate space**. It says: "After target line 3 (which is `line3`), the sequence `lineY\n` must not exist." When the sifted batch is later merged with the working tree, this constraint will suppress `lineY` at its structurally aligned position, ensuring the result matches the target content.

6. **Validation**:
   ```python
   merge_batch(
       batch_source=target_content,    # line1, line2-mod, lineX, line3
       ownership=ownership,
       working=working_content          # line1, line2-mod, line3, lineY
   )
   # Result: line1, line2-mod, lineX, line3 ✓ matches target content
   ```

7. **Persist "feature-x-remaining"**:
   * Batch source commit contains: `line1, line2-mod, lineX, line3` (the target)
   * Batch commit contains: `line1, line2-mod, lineX, line3` (same - sift-specific)
   * Ownership: claims lineX, forbids lineY at position after line3

**Result:** The sifted batch `feature-x-remaining` contains the target content and ownership that represents only the portion still needed (lineX and removal of lineY).

### Design Principles

**1. Derivation, Not Filtering**

Sift does not filter the source batch's ownership in batch-source coordinate space. Instead, it **derives fresh ownership** by comparing working tree to realized target.

This handles coordinate space shifts automatically.

**2. Conservative Pairing**

The structural anchor grouping ensures pairing is only done when unambiguous. Ambiguous cases (repeated lines, multiple candidates with same anchor) are left as separate deletion + presence runs.

This prevents incorrect coupling assumptions.

**3. Validation as Invariant Proof**

Using `merge_batch` for validation proves the sifted representation is **semantically correct**, not just structurally well-formed.

This catches mistakes that bounds-checking would miss.

**4. Intentional Persistence Model**

The different persistence semantics for sifted batches are **intentional and documented** in the module docstring. The batch source IS the target, not a historical snapshot.

This clarity prevents future confusion.

### Implementation

**Core Module:** `src/git_stage_batch/commands/sift.py`

**Key Functions:**

* `command_sift_batch()`: Entry point, coordinates copy vs in-place
* `_compute_sifted_text_file()`: Derives sifted ownership from working→realized delta
* `build_ownership_from_working_to_realized_delta()`: Uses semantic change runs
* `_validate_sifted_text_file_result()`: Three-level validation
* `add_sifted_text_file_to_batch()`: Persists with special semantics
* `_perform_atomic_in_place_sift()`: Atomic in-place updates

**Supporting Modules:**

* `src/git_stage_batch/batch/comparison.py`: Semantic change run derivation
  * `derive_semantic_change_runs()`: Conservative pairing with structural anchors
  * `SemanticChangeRun`: PRESENCE, DELETION, REPLACEMENT types

**Tests:** `tests/commands/test_sift.py`

* Basic behavior (removes already-present, empty when fully present)
* Conservative pairing (ambiguous cases, repeated lines, clustered edits)
* Validation strength (semantic correctness, boundary cases)
* Persistence model (stores realized target, not working tree)
* Copy vs in-place modes (atomic updates, rollback)

---

## Change Propagation

### Scenario 1: Adding Content to Batch

**User Action:**

```bash
# Working tree changes
echo "line2-modified" > file.txt

# Save to batch
git-stage-batch include --to feature-a --line 2
```

**Propagation:**

1. **Create/Reuse Batch Source:**

   * Check `session-batch-sources.json` for existing batch source
   * If missing: create new commit from working tree → `batch-source-commit`
   * Save to session cache

2. **Translate to Ownership:**

   ```python
   # Line 2 in working tree → line 2 in batch source (via annotation)
   ownership = BatchOwnership(
       claimed_lines=["2"],
       deletions=[DeletionClaim(...)]  # If line was modified
   )
   ```

3. **Build Realized Content:**

   ```python
   base_content = read_git_blob(f"{baseline}:file.txt")
   batch_source_content = read_git_blob(f"{batch_source}:file.txt")
   realized = build_realized_content(base_content, batch_source_content, ownership)
   ```

4. **Update Batch Commit:**

   * Create blob from realized content
   * Create new commit with tree containing blob
   * Update `refs/batches/feature-a`

5. **Update Metadata:**

   * Write ownership to `.git/git-stage-batch/batches/feature-a/metadata.json`

### Scenario 2: Applying Batch to Working Tree

**User Action:**

```bash
git restore .  # Revert working tree to baseline
git-stage-batch apply --from feature-a
```

**Propagation:**

1. **Read Batch Metadata:**

   ```python
   metadata = read_batch_metadata("feature-a")
   file_data = metadata["files"]["file.txt"]
   ownership = BatchOwnership.from_metadata_dict(file_data)
   ```

2. **Get Content:**

   ```python
   batch_source_content = read_git_blob(f"{batch_source}:file.txt")
   working_content = read_file("file.txt")  # Current working tree
   ```

3. **Merge:**

   ```python
   merged = merge_batch(batch_source_content, ownership, working_content)
   ```

4. **Write Working Tree:**

   ```python
   write_file("file.txt", merged)
   ```

**No metadata changes:** Applying a batch doesn't modify the batch itself.

### Scenario 3: Discarding Batch

**User Action:**

```bash
git-stage-batch discard --from feature-a
```

**Propagation:**

1. **Read Batch Metadata:** (same as apply)

2. **Get Baseline Content:**

   ```python
   baseline_content = read_git_blob(f"{baseline}:file.txt")
   ```

3. **Inverse Merge:**

   ```python
   # Remove batch content, restore baseline
   result = discard_batch(
       batch_source_content,
       ownership,
       working_content,
       baseline_content
   )
   ```

4. **Write Working Tree:**

   ```python
   write_file("file.txt", result)
   ```

### Scenario 4: Working Tree Diverges

**Situation:**

```
Batch source: line1, line2-modified, line3
Working tree: line1, line2-modified, line3, line4-extra (user added line4)
```

**What Happens During Merge:**

```python
# Structural alignment finds:
mapping = {
    1 → 1,  # line1 matches
    2 → 2,  # line2-modified matches
    3 → 3   # line3 matches
    # line4 has no mapping (working tree extra)
}

# Merge algorithm:
result = []
for source_line in [1, 2, 3]:
    working_line = mapping[source_line]
    if source_line in claimed_set:
        result.append(source_lines[source_line - 1])  # Batch version
    else:
        result.append(working_lines[working_line - 1])  # Working version

# Add working tree extras
result.append(working_lines[3])  # line4-extra preserved
```

**Result:** Batch changes applied, working tree extras preserved.

### Scenario 5: Attribution Filtering During Iteration

**Situation:**

```
Baseline:     line1, line2, line3
Working tree: line1-modified, line2-added, line3
Batch "feature-a" owns: line1-modified
```

**What Happens During Filtering:**

1. Compare baseline ↔ working tree directly
2. Derive attribution units from the file comparison
3. Attribute those units to batches using ownership constraints
4. Project attributed units onto displayed diff hunks
5. Hide only the fragments already owned by batches

**Result:** User sees only `line2-added` as unbatched work.

---

## Complete Example Walkthrough

### Initial State

```bash
# Create repo
git init
echo -e "line1\nline2\nline3" > file.txt
git add file.txt
git commit -m "Initial"  # Commit: abc123
```

**State:**

* Baseline: `abc123` containing `line1\nline2\nline3`
* Batch source: (none yet)
* Working tree: `line1\nline2\nline3`

### Step 1: Modify Working Tree

```bash
# Edit file
cat > file.txt << EOF
line1
line2-modified
line3
line4-new
EOF
```

**State:**

* Baseline: `abc123` (unchanged)
* Batch source: (none yet)
* Working tree: `line1\nline2-modified\nline3\nline4-new`

### Step 2: Start Session and Save to Batch

```bash
git-stage-batch start
git-stage-batch include --to feature-a --line 1,2
```

**Internal Process:**

1. **Baseline already established:** `abc123`

2. **Create batch source commit from working tree:**

   ```
   batch-source = def456
   contents: line1\nline2-modified\nline3\nline4-new
   ```

3. **Translate selected lines to ownership:**

   ```python
   ownership = BatchOwnership(
       claimed_lines=["1-2"],
       deletions=[
           DeletionClaim(anchor_line=1, content_lines=[b"line2\n"])
       ]
   )
   ```

4. **Build realized batch commit:**

   ```
   refs/batches/feature-a = ghi789
   realized content:
     line1
     line2-modified
     line3
   ```

5. **Persist metadata:**

   ```json
   {
     "baseline": "abc123",
     "files": {
       "file.txt": {
         "batch_source_commit": "def456",
         "claimed_lines": ["1-2"],
         "deletions": [{"after_source_line": 1, "blob": "..."}]
       }
     }
   }
   ```

### Step 3: Continue Editing Working Tree

```bash
echo "line5-extra" >> file.txt
```

**Working tree now:**

```
line1
line2-modified
line3
line4-new
line5-extra
```

### Step 4: Filtering Hides Already-Batched Content

When the system iterates hunks for display:

1. Build file attribution from baseline ↔ working tree
2. Attribute units corresponding to `line1` and `line2-modified` to `feature-a`
3. Project attributed units onto displayed diff hunks
4. Hide already-owned fragments

**What user sees:**

```
+ line4-new
+ line5-extra
```

**What user does not see:**

```
+ line1
- line2
+ line2-modified
```

because those fragments are already attributed to `feature-a`.

### Step 5: Apply Batch Elsewhere

```bash
git restore .
git-stage-batch apply --from feature-a
```

**Resulting working tree:**

```
line1
line2-modified
line3
```

The batch has been realized onto baseline.

### Step 6: Discard Batch

```bash
git-stage-batch discard --from feature-a
```

**Result:**

```
line1
line2
line3
```

The batch's presence and absence constraints were reversed, restoring baseline structure.

---

## Final Principles

The system is built on three pillars:

* **Constraints, not operations**
* **File structure, not diff hunks**
* **Conservative correctness over aggressive inference**

This ensures the system remains predictable, idempotent, and robust under continuous editing.

## Further Reading

**Core Constraint Model:**

* **`src/git_stage_batch/batch/ownership.py`**: Ownership model implementation (presence + absence constraints), ownership unit construction with display adjacency grouping, semantic selection operations
* **`src/git_stage_batch/batch/merge.py`**: Constraint-based merge and discard algorithms with region classification
* **`src/git_stage_batch/batch/match.py`**: Conservative LCS-based line mapping for structural alignment
* **`src/git_stage_batch/batch/storage.py`**: Batch storage and realized content building from ownership constraints

**Attribution and Filtering:**

* **`src/git_stage_batch/batch/attribution.py`**: File-centric attribution system, maps working tree changes to batch ownership, filters already-batched content
* **`src/git_stage_batch/data/hunk_tracking.py`**: Hunk iteration with attribution-based filtering (`fetch_next_change()`)
* **`tests/batch/test_ownership_hardening.py`**: Attribution edge cases (repeated lines, -U0 contexts, start-of-file deletions)

**Stale Source Management:**

* **`src/git_stage_batch/batch/source_refresh.py`**: Centralized stale-source detection and repair, batch source advancement, session cache management

**Sifting and Reconciliation:**

* **`src/git_stage_batch/commands/sift.py`**: Sift command implementation, semantic change derivation, specialized persistence for sifted batches
* **`src/git_stage_batch/batch/comparison.py`**: Shared comparison logic for deriving semantic change runs, conservative replacement pairing
* **`tests/commands/test_sift.py`**: Comprehensive sift tests (pairing, validation, persistence model)

**Display and Selection:**

* **`src/git_stage_batch/batch/display.py`**: Display line reconstruction from batch ownership
* **`tests/batch/test_ownership_unit_grouping.py`**: Tests demonstrating display adjacency grouping behavior

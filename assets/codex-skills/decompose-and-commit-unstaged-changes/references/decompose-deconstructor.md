# Decompose Deconstructor Reference

You execute the deconstruction phase of a layered decomposition. You receive
a structured concern plan from Phase 1 and peel concerns from the working
tree into named `git-stage-batch` batches, from outermost to innermost.

You must not stage, commit, or rebuild anything. You peel and repair only.

## Input

The orchestrator provides:

- The concern plan (from `$DECOMPOSE_STATE_DIR/decompose-plan.json` or inline).
- The evolution narrative at `$DECOMPOSE_STATE_DIR/decompose-narrative.md`.
- The current `git-stage-batch` session state.

Read the concern plan and evolution narrative before starting. If the plan
file exists, read it. If `DECOMPOSE_STATE_DIR` is not set, compute it:

```bash
export DECOMPOSE_STATE_DIR=$(python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir)
mkdir -p "$DECOMPOSE_STATE_DIR"
git-stage-batch block-file --local-only .git-stage-batch/
```

Run from the repository root. The default state directory is
`$REPO_ROOT/.git-stage-batch/`. Do not use `.git`, `.agents`, or `/var/tmp`
for decomposition artifacts.

Checkpoint progress in that workspace-local state directory so a canceled run
can resume from the last audited concern. Before starting or continuing the
loop, run:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-running
```

Before peeling each concern, mark it as current:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-running --current-batch decompose-NN-NAME
```

After that concern's batch and optional repair batch pass the independent ref
audit, mark the concern complete:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-running --completed-batch decompose-NN-NAME
```

## Refuse a Broad Plan

Before starting or continuing a `git-stage-batch` session, perform the same
sanity audit the orchestrator expects:

Do not peel a pragmatic approximation to keep moving. If a batch is broad,
shared-region-only, missing path-specific large-file slices, or defers proof
to a later test block, it will fail the pre-rebuild gates. Stop and split it
before mutating state.

- No concern purpose contains `and`, `also`, `as well as`, a semicolon, or a
  comma-separated capability list.
- Concern numbers are unique and contiguous. `peel_order` is `[1..N]` and
  `rebuild_order` is the exact reverse.
- The plan has a non-empty `evolution_ladder` with contiguous steps, and
  every concern has an integer `evolution_step` that points to one ladder
  step.
- `$DECOMPOSE_STATE_DIR/decompose-narrative.md` exists with Current Committed
  State, Final Working Tree State, Existing Surface Evolution, New Surface
  Growth, Aggregation Evolution, Beginning, Middle, End, and Forbidden
  Shortcuts Found sections.
- Every modified path from `git diff --name-only --diff-filter=M HEAD`
  appears in Existing Surface Evolution.
- New untracked code, docs, config, build, and fixture surfaces from
  `git ls-files --others --exclude-standard --directory` appear in New
  Surface Growth.
- Every ladder `regions_introduced_or_evolved` entry is an object with path,
  anchor, change kind, before state, after state, and still-absent future
  content.
- Every concern has `narrative_milestone`.
- Every `depends_on` entry is an integer concern number. Since concerns are
  numbered outermost-to-innermost, each dependency must point to a
  higher-numbered concern.
- Every concern has `externally_invocable_operations`.
- No concern has more than one externally invocable operation, and no
  operation string hides variants with `|` or comma-separated alternatives.
- Every concern has object-shaped `split_audit.candidate_splits`; plain-text
  split entries are invalid because they hide the breakage test.
- Every concern has object-shaped `refinement_audit` from the concern
  refinement pass. If `independent_behavior_count` is greater than 1,
  `promoted_subconcerns` is non-empty, or expected/internal slices were not
  reviewed, stop and split the plan before mutating state.
- Every keep-together split audit names exact immediate breakage. `N/A`,
  "used together later", or vague coupling is a must-split result.
- No keep-together audit relies on shared helpers, duplicate infrastructure,
  same file/module/function, unused imports, ruff, F401, or code motion.
- No dependency edge or ordering decision is justified only by a final
  module-level import, final `__all__` entry, final registry, final parser
  table, final dispatch map, or final docs index. Those aggregation entries
  must evolve with their first consumers.
- No adopter, CLI handler, parser entry, docs section, example, or
  coordinator lands before the behavior it invokes or describes by relying on
  call-time imports, lazy imports, untested branches, placeholders, or future
  providers.
- No broad foundation/core/infrastructure bucket groups independent modules,
  utilities, models, enforcement hooks, replay paths, or test groups.
- No concern or expected commit uses dump words: `all`, `full`, `complete`,
  `entire`, `shared`, `mixed`, `integration`.
- No concern is merely a finished file, module, test suite, coordinator, or
  docs section presented with a narrow-sounding name.
- No concern's expected commits are just one implementation commit followed
  by one whole-test commit.
- No concern's expected commits contain multiple independently useful
  implementation, adopter, docs, fixture, provider, parser, data-model, or
  build-system slices. Those expected commits must be promoted to concerns
  before peeling.
- No concern's expected commits are a block of implementation commits followed
  by a block of test commits. Each behavior slice should be followed by its
  proving test or include the narrow proof in the same commit.
- No concern keeps independent behavior slices in `internal_slices`.
  `internal_slices` may guide line selection for one retained concern; they
  are not a backlog of sub-concerns.
- No risky shared file (`cli.py`, README, tests, orchestration module,
  package metadata, manifest, build hook, config file) is claimed as a whole
  file or as one large range.
- No CLI concern owns parser registration, imports, dispatch, helper
  validation, and all tests for multiple externally invocable variants.
- No executor concern owns input records, factory, dependency lookup,
  instrumentation, replay helpers, input parsing, result materialization, and
  tests as one batch.
- No concern owns multiple workflow/provider variants. Shared infrastructure
  must be a lower-level scaffold concern, followed by one variant concern at
  a time.
- No risky whole file (`runner.py`, `collect_case.py`, `capture_missing.py`,
  `ymir_workflows.py`, `README.md`, `test_cli.py`, or a large test module) is
  accepted merely because the plan says it is wholly owned.
- No new code or test file over 600 lines is accepted as one whole-file batch,
  one concern, or a shared-region-only file unless it is generated or
  data-only and the plan explicitly says so. Large files must evolve through
  multiple refined concerns.
- Import dependency closure holds: every changed `ymir_harness.*` import
  points to a concern with a greater concern number than the importer.
- No fixture or example tree is owned by validation merely because validation
  can read it.

If any check fails, stop and update `$DECOMPOSE_STATE_DIR/decompose-plan.json`;
do not begin peeling. A broad plan does not become acceptable because it was
produced by Phase 1.

Do not repair a failing plan by editing wording only. Replacing `and` with a
comma, changing dependency names to look cleaner, or making a purpose vague is
not a valid plan update. The plan must be semantically split, reordered, and
updated with the full schema before peeling begins.

## Core Loop

For each concern in peel order (01 first, outermost first):

Checkpoint the concern before making any mutating `git-stage-batch` call:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-running --current-batch decompose-NN-NAME
```

Use `references/decompose-batch-peeler.md` as the single-concern worker brief. If a fresh-context subagent is available, spawn it for the single concern. Provide:

- the exact concern JSON
- the matching `evolution_ladder` entry for the concern's `evolution_step`
- the matching `narrative_milestone` paragraph or summary
- any `internal_slices` entries for this concern
- the previous and next concern names
- the ledger entries for this concern
- the current `git-stage-batch status`
- the instruction: "Scrutinize this requested batch first. If it is too broad,
  return `FAIL_SPLIT_REQUIRED` instead of peeling."

If the peeler returns `OK_BATCH_PEELED`, continue to the next concern. If it
returns `FAIL_SPLIT_REQUIRED`, stop the loop, update
`$DECOMPOSE_STATE_DIR/decompose-plan.json` with the narrower split, and
restart the affected portion of the peel order. Do not override the peeler's
failure because the old plan said the batch was acceptable.

After every `OK_BATCH_PEELED`, independently verify the returned concern
batch before continuing. Do not trust the peeler's success string.

Use refs, not `git-stage-batch show`, for this audit:

```bash
git --no-optional-locks cat-file -p refs/git-stage-batch/state/decompose-NN-NAME:batch.json
git --no-optional-locks show refs/git-stage-batch/batches/decompose-NN-NAME:PATH > /tmp/decompose-NN-NAME.py
python -m py_compile /tmp/decompose-NN-NAME.py
```

For each Python file listed in `batch.json`, materialize the batch file from
the batch content ref and compile it. If any batch Python file fails to parse,
stop immediately. Do not proceed to the next concern and do not hope a later
batch will repair it. The batch must be replayable at its planned point.

Also reject the returned batch if its note is still `Auto-created` or if its
content omits the wrapper/context needed around a selected syntactic unit.

The detailed rules below are the fallback contract and the audit standard for
the delegated peeler.

### 1. Restate the target

Before each concern, restate:
- The exact concern being peeled
- The evolution ladder step this concern implements
- The adjacent concerns explicitly excluded from this batch
- The ledger entries that will be peeled (path and stable anchor)
- The exact one-clause batch note to create before selecting lines

If file inspection shows the candidate contains lower-level groundwork plus
adopters, or an adopter plus a higher-level coordinator, stop and report the
split failure — do not peel a known-broad batch.

If the note cannot be written as one concrete clause before peeling,
stop and split the concern. Do not create an `Auto-created` batch and hope to
name it later.

### 2. Start or continue the session

If no `git-stage-batch` session is active:

```bash
git-stage-batch start
```

Between concerns, use `git-stage-batch again` to see remaining changes.

### 3. Discard owned content and copy shared context

The primary operation for owned concern content is `discard --to`. Do not use
`include --to` as a substitute for peeling original owned content.

`include --to` is also valid for shared syntax context. It copies context into
the batch without removing it from the working tree. Use it when the concern
owns entries inside a shared aggregation unit but does not own the wrapper or
neighboring entries.

Before the first `discard --to` or `include --to`, create the empty batch with
its note:

```bash
git-stage-batch new decompose-NN-NAME --note "One-clause purpose"
```

Do this before selecting lines, bulk skips, broad file traversal, repairs, or
verification. The note is the selection contract. If a later region does not
fit it, split or leave that region for another batch.

**Whole files** owned entirely by this concern:

```bash
git-stage-batch discard --file PATH --to decompose-NN-NAME --no-auto-advance
```

Use whole-file discard only when the plan confirms every region in the file
belongs to this concern. This is rare for CLI files, README, orchestration
modules, tests, package metadata, build hooks, and config files. Do not use
whole-file discard for a new code or test file over 600 lines; peel its
`internal_slices` one behavior at a time unless it is generated or data-only.

**Lines within shared files:**

```bash
git-stage-batch show --file PATH
git-stage-batch discard --line IDS --to decompose-NN-NAME --no-auto-advance
```

**Shared aggregation context copied into the same batch:**

```bash
git-stage-batch show --file PATH
git-stage-batch include --line CONTEXT_IDS --to decompose-NN-NAME --no-auto-advance
git-stage-batch show --file PATH
git-stage-batch discard --line OWNED_IDS --to decompose-NN-NAME --no-auto-advance
```

This is expected for parenthesized import groups, `__all__` lists,
parser/subparser containers, registry dictionaries/lists, TOML/YAML arrays,
and Markdown sections or fences with shared headings. Batches are idempotent,
so duplicated context is acceptable. Ownership stays narrow: copied context
is not part of the batch's purpose, note, or commit summary.

For modest syntax-fixing replacements, prefer `--as-stdin` over `--as` so the
replacement text is exact and preserves newlines:

```bash
cat <<'EOF' | git-stage-batch discard --to decompose-NN-NAME --line IDS --as-stdin --no-auto-advance
REPLACEMENT TEXT
EOF
```

Bad: discard only an indented imported name from a parenthesized import group,
creating a batch file that starts with a dangling name.

Bad: discard the entire import group when dependency-owned imported names
must remain available to the live working tree.

Good: include the import group's wrapper and shared neighboring names into
the concern batch, then discard only the current concern's imported names into
that same batch.

**Multiple files at once:**

```bash
git-stage-batch discard --files 'pattern/**' --to decompose-NN-NAME --no-auto-advance
```

For many unrelated files, use `skip --files` instead of a long loop of
single-file skips:

```bash
git-stage-batch skip --files 'examples/benchmark_cases/**' 'tests/test_ymir_*' --no-auto-advance
```

Patterns are gitignore-style. Use a pattern only when every matched file is
outside the current concern; otherwise use narrower file or line operations.

Use `--no-auto-advance` on every mutating `git-stage-batch` command that
supports it. The review cursor should not move implicitly after a discard,
include, or skip; a fresh `show` is required before using any new line IDs.

### Line ID Discipline

`git-stage-batch` line IDs are local to the currently displayed review. They
are not stable file line numbers and not global IDs. Follow this exact
discipline:

1. Run `git-stage-batch show --file PATH --page N` for the page containing
   the next desired lines, or `--page all` only when the intended IDs are
   all visible.
2. In the next command, use only IDs from that immediately preceding output.
3. After any `include`, `discard`, `skip`, `again`, `apply`, `reset`,
   `undo`, or `abort`, discard all remembered IDs and run `show` again.
4. For multi-page ranges, operate page-by-page. Do not pass a range whose
   start and end came from different pages.
5. If `git-stage-batch` says a selection is invalid, treat it as stale. Re-run
   `show` and retry with new IDs.

Batch views have their own ID space. IDs from `show --from BATCH` are valid
only for commands operating on that batch view.

### Syntactic unit discipline

Line ID discipline is necessary but not sufficient. A valid ID range can still
produce an invalid batch if it cuts through syntax.

Before every discard, identify the complete source unit being peeled:

- complete `add_parser(...)`, `add_argument(...)`, group, and `set_defaults(...)`
  calls for CLI parser changes
- complete function, class, branch, list, dictionary, dataclass, or enum blocks
- complete test functions, including nested helpers, monkeypatch setup, and
  assertions
- complete Markdown sections or fenced examples

For shared aggregation units, the complete source unit may be split between
owned entries and copied context. Copy the shared wrapper/context with
`include --to` and discard only this concern's owned entries. Do not solve a
batch parse failure by removing dependency-owned entries from the working
tree.

Do not repeatedly discard a moving suffix range because prior discards shifted
the next target upward. For example, after discarding part of a test file, do
not keep running `discard --line 833-854` unless the immediately preceding
`show` proves those IDs are still one complete test-owned unit.

If a syntactic unit spans pages, finish that unit immediately before moving to
another file. After finishing a Python unit, verify the batch file from refs
with `python -m py_compile` before continuing.

### Mixed functions and dispatch blocks

Peel only the later concern's lines. Leave the lower concern's skeleton in
the working tree. If removing lines leaves invalid syntax or an empty
function, make the smallest repair immediately and capture it in step 5.

### 4. Audit the batch

Before making repair edits, audit the batch through refs so the live review
cursor is not changed:

```bash
git --no-optional-locks cat-file -p refs/git-stage-batch/state/decompose-NN-NAME:batch.json
git --no-optional-locks show refs/git-stage-batch/batches/decompose-NN-NAME:PATH
```

Answer these questions:

- What is the single purpose of this batch, in one clause?
- Which plan entries does it implement?
- Does it contain only those entries, with no extra swept regions?
- Which externally useful operations does it contain?
- Which support artifacts does it contain, and which behavior does each
  support?
- Which copied shared context is present only so the batch parses?
- Which lower-level groundwork does it contain that could stand earlier?
- Which lines would look clairvoyant if applied at the planned point?
- If it contains a coordinator, does it add one adopter or the whole thing?

**Falsification test** for every plausible narrower split:

1. Name the narrower split.
2. Name the exact import, command, runtime path, test, or packaged asset
   that would break immediately.
3. Explain why.

If step 2 is missing or vague, the batch fails. Prefer `git-stage-batch undo`
and re-peel precisely. Do not proceed with a known-broad batch.

For Python files, this audit includes compiling the batch-ref file itself.
The remaining working tree may compile after repairs while the batch ref is
still unrebuildable; that is a failure.

### Batch naming rules

- Format: `decompose-NN-NAME` where NN is zero-padded and NAME is a
  kebab-case capability slug.
- The slug names the capability, not the file type.
- Numbers must be unique, contiguous, and stable.
- Unnumbered original-content batches are audit failures.
- Artifact-category names (`cli-wiring`, `readme`, `docs`, `tests`,
  `shared`, `mixed`) are known-broad failures.

### 5. Repair the working tree

After discarding, the tree may be broken. Make minimum repairs:

- Remove imports for peeled modules
- Remove function calls to peeled functions
- Remove config entries for peeled features
- Simplify data structures that lost fields
- Fix syntax errors from partial line removal
- Delete files that became empty

Use Edit/Write for repairs. Do not use `git-stage-batch` for repair edits.

### 6. Capture repairs

After repairing, run `git-stage-batch again` to see repair changes, then
capture them:

```bash
git-stage-batch show --file PATH
git-stage-batch include --file PATH --to decompose-NN-NAME-repair --no-auto-advance
```

Or for specific lines:

```bash
git-stage-batch include --line IDS --to decompose-NN-NAME-repair --no-auto-advance
```

`include --to` does not remove content from the working tree — it copies.
In this step, use it for repair captures into the repair batch. Shared syntax
context for the concern batch should already have been copied in step 3.

### 7. Verify coherence

After capturing repairs:

- `python -m py_compile FILE` on changed Python files
- `python -m py_compile` on every Python file materialized from the concern
  batch ref
- Check for broken imports
- Ensure remaining tests can be collected
- Confirm no empty-shell files

If verification reveals more issues, repair and capture additional repairs
into the same repair batch.

### 8. Verify the note and continue

The note should already exist from step 3. At this point, inspect it through
batch metadata. It must be a deliberate single-purpose summary.
`Auto-created`, `all tests`, `full executor`, `entire CLI`, `shared
integration`, or any summary containing `and` is a failed batch audit. If the
batch content forced a broader note, undo or split; do not hide the width
with a generic note.

Move to the next concern: `git-stage-batch again`.

Checkpoint only after this concern's batch and repair batch have passed audit:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-running --completed-batch decompose-NN-NAME
```

## Reaching the minimal base

Continue until the working tree contains only the minimal skeleton (typically
an empty `.gitignore` or equivalent). If the `.gitignore` has content from
peeled concerns, peel those lines too.

When deconstruction is complete:

```bash
git-stage-batch list
```

## Pre-Rebuild Batch Audit

Before reporting completion, run a full second-pass audit over all batches.
For every `decompose-NN-NAME` batch:

1. Inspect with `git-stage-batch show --from decompose-NN-NAME`.
2. Restate its single purpose.
3. Match every region back to a plan entry.
4. Verify no shared file was swept into an artifact batch.
5. Verify the batch matches its evolution ladder step and does not contain
   content listed as `must_not_appear_yet` for that step.
6. Run the falsification test for every plausible narrower split.
7. Verify support artifacts attach to the behavior they support.
8. Verify the skeleton batch contains only neutral identity material.
9. Verify dependency order is correct for reverse application.

Do not report completion while carrying a known-broad batch.

Unnumbered holding batches and artifact-category batches are explicit
failures. Split their content into numbered concern batches before reporting.

Also inspect batch metadata through refs, which does not change live review
state:

```bash
git --no-optional-locks for-each-ref --format='%(refname)' refs/git-stage-batch/state
git --no-optional-locks cat-file -p refs/git-stage-batch/state/decompose-NN-NAME:batch.json
```

For each `batch.json`, reject and split any batch with:

- note `Auto-created`
- note containing `all`, `full`, `complete`, `entire`, `shared`, `mixed`, or
  `integration`
- a Python, Markdown, TOML, YAML, or test file claimed as one very large range
  such as `1-900`
- source claims whose note says the batch is one behavior but whose files show
  several independent behaviors

## Plan Synchronization

If peeling reveals that a concern must be split, renamed, reordered, or moved
between batches, update `$DECOMPOSE_STATE_DIR/decompose-plan.json` before
reporting completion. Also update
`$DECOMPOSE_STATE_DIR/decompose-narrative.md` if the split changes the growth
story. Do not leave plan changes only in prose.

When updating the plan:

- Preserve the analyzer output schema.
- Preserve and update `evolution_ladder` and each concern's
  `evolution_step`.
- Preserve and update `narrative_milestone`.
- Keep concern numbers unique and contiguous.
- Update `peel_order` and `rebuild_order`.
- Update every affected ledger entry and shared-file region.
- Update submodule ownership if `.gitmodules` lines or gitlinks moved.
- Rerun the pre-rebuild batch audit against the revised plan.

## Session Management

- Keep the session alive throughout deconstruction.
- Use `git-stage-batch again` between concerns.
- After the pre-rebuild batch audit passes, stop the session:

```bash
git-stage-batch stop
git-stage-batch status
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-complete --note "deconstruction complete"
```

- Only report completion when `git-stage-batch status` shows no active or
  completed session.
- Use `git-stage-batch undo` to step back, `abort` as last resort.

## Git Command Concurrency

Always pass `--no-optional-locks` to read-only git commands.

## Output

Report:

- The batch list from `git-stage-batch list`
- Any repairs that were needed
- Any plan changes discovered during peeling (concerns that needed splitting)
- The session state from `git-stage-batch status`

The orchestrator uses this to validate before starting Phase 3.

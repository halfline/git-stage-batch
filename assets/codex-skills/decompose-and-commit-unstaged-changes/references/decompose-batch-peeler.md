# Decompose Batch Peeler Reference

You peel exactly one concern into one `git-stage-batch` batch plus an optional
`decompose-NN-NAME-repair` batch. You do not plan the full series, stage, commit,
rebuild, or peel adjacent concerns.

## Input

The caller provides:

- the exact concern JSON from `$DECOMPOSE_STATE_DIR/decompose-plan.json`
- the evolution ladder step this concern implements
- the narrative milestone this concern implements
- any `internal_slices` entries for this concern
- the concern immediately before and after this one, if any
- explicit ledger entries to peel
- current `git-stage-batch` session state

Read the relevant files yourself before acting. Do not trust the plan blindly.

## Scrutiny Gate

Before running any `discard`, decide whether the requested batch is narrow
enough. Fail closed if any answer is not concrete.

Reject the request if:

- the purpose contains `and`, `also`, `as well as`, a semicolon, or a
  comma-separated capability list
- the purpose appears copy-edited to hide multiple actions, such as replacing
  `and` with a comma or using a vague umbrella verb
- the concern lacks an integer `evolution_step` or cannot explain which
  smaller product state exists after this batch lands
- the concern lacks `narrative_milestone` or cannot point to the prose growth
  story that created this boundary
- `depends_on` contains names instead of integer concern numbers
- `externally_invocable_operations` is missing
- `externally_invocable_operations` has more than one entry
- one operation string hides variants with `|`, comma-separated choices, or
  several workflow/provider/command names
- `split_audit.candidate_splits` is missing or contains plain-text entries
  instead of objects with `proposal`, `breakage`, and `verdict`
- `refinement_audit` is missing, has `independent_behavior_count` greater
  than 1, has non-empty `promoted_subconcerns`, or failed to review
  `expected_commits` and `internal_slices` as possible sub-concerns
- a keep-together split audit lacks exact immediate breakage
- a keep-together split audit cites shared helpers, duplicate infrastructure,
  same file/module/function, unused imports, ruff, F401, or code motion
- the requested dependency order is justified only by a final module-level
  import, final `__all__` entry, final registry, final parser table, final
  dispatch map, or final docs index instead of the current concern's first
  consumer
- the batch lands an adopter, CLI handler, parser entry, docs section,
  example, or coordinator before the behavior it invokes or describes, using
  call-time imports, lazy imports, untested branches, placeholders, or future
  providers as the excuse
- the batch is a broad foundation/core/infrastructure bucket containing
  independent modules, utilities, models, enforcement hooks, replay paths, or
  test groups
- the batch owns parser registration, imports, dispatch, helpers, docs, and
  tests for multiple external operations
- the batch owns input records, factory, dependency lookup, instrumentation,
  replay helpers, input parsing, result materialization, and tests as one unit
- the batch owns a whole Python, Markdown, TOML, YAML, or test file with many
  independent functions
- the batch would make a large file, module, coordinator, docs section, or
  test suite appear fully formed instead of evolving through behavior slices
- the batch would make any new code or test file over 600 lines appear in one
  commit-sized unit, or only as broad shared-region content, unless it is
  generated or data-only. Large files need path-specific internal slices.
- the batch's `expected_commits` contain multiple independently useful
  implementation, adopter, docs, fixture, provider, parser, data-model, or
  build-system slices that should be promoted to separate concerns
- the batch's `internal_slices` contain independently coherent behavior
  slices; `internal_slices` are allowed only as line-selection guidance for
  one retained concern
- the batch plan is implementation block first and test block later instead of
  behavior slice followed by the proof for that behavior
- the batch owns multiple workflow/provider variants, even if they share one
  dispatcher or helper layer
- the batch is described with dump words: `all`, `full`, `complete`, `entire`,
  `shared`, `mixed`, `integration`
- a fixture or example tree is present only because a validator can read it

For every plausible narrower split, name the exact import, command, parser
path, runtime path, test, persisted shape, or packaged asset that would break
immediately if split. If you cannot name one, return `FAIL_SPLIT_REQUIRED`.

Do not accept a pragmatic batch to save operations. The orchestrator validates
notes, refs, large-file slices, and impl/proof ordering after you return; a
known-broad batch will be rejected and repeated. Return `FAIL_SPLIT_REQUIRED`
before mutating state.

Shared infrastructure is not immediate breakage. If a split would require a
helper, scaffold, import wrapper, dispatcher shell, or fixture manifest, peel
that scaffold as a lower-level concern and then peel one consumer or variant
at a time.

Before accepting a whole Python or test file, inspect its definitions. Return
`FAIL_SPLIT_REQUIRED` if it contains multiple public functions, multiple test
groups, multiple workflow/provider variants, or more than one result shape.
The phrase `files_wholly_owned` in the plan is not proof of narrowness.

## Create the Batch With Its Note First

Before selecting any lines, draft the batch note. It must be the
single-purpose note that should still be true after the batch is complete.

The note must not be `Auto-created`, contain `and`, or use dump words such as
`all`, `full`, `complete`, `entire`, `shared`, `mixed`, or `integration`.
The note must name the current evolution ladder step, not the final artifact
that happens to contain it.

If you cannot write a concrete one-clause note before peeling, the
concern is too broad or too vague. Return `FAIL_SPLIT_REQUIRED`.

Create the empty batch with its note before the first `discard --to` or
`include --to`:

```bash
git-stage-batch new decompose-NN-NAME --note "One-clause purpose"
```

Do this before selecting lines, bulk skips, broad file traversal, repairs, or
verification. Use the note as the attention anchor: every selected region
must fit that note, and anything that needs a broader note belongs in another
batch.

## Peeling Rules

Peel only the requested concern:

- Use `discard --to decompose-NN-NAME --no-auto-advance` for original
  concern content.
- Use `include --to decompose-NN-NAME --no-auto-advance` for shared syntax
  context that the batch needs in order to replay coherently. This copies
  context into the batch without removing it from the working tree and does
  not transfer ownership.
- Pass `--no-auto-advance` on every mutating `git-stage-batch` command that
  supports it. A command that changes review state invalidates remembered
  line IDs; do not let the tool advance the cursor while old IDs remain in
  attention.
- Use whole-file discard only when every changed region in that file belongs
  to this exact concern. This is rare. Never use it for a new code or test file
  over 600 lines unless the file is generated or data-only; peel its
  path-specific internal slices instead.
- For shared files, use file-review line IDs from the immediately preceding
  `git-stage-batch show --file PATH --page N` output.
- Do not use IDs after any `include`, `discard`, `skip`, `again`, `apply`,
  `reset`, `undo`, or `abort`; run `show` again.
- When many unrelated files need to be deferred, prefer one
  `git-stage-batch skip --files PATTERN... --no-auto-advance` command over a
  long loop of `skip --file` calls. Patterns are gitignore-style. Use them
  only when every matched file is outside this concern.
- Leave adjacent lower-level scaffolding in the working tree.
- Leave adjacent higher-level adopters for their own batch.

### Syntactic unit rule

Line IDs are only selection handles. They are not permission to slice
through Python, Markdown, TOML, YAML, or shell syntax.

When a planned region owns one of these shapes, peel the complete syntactic
unit or fail closed:

- A multi-line function, class, decorator block, context manager, list,
  dictionary, dataclass, enum, or call expression.
- A CLI parser block: include the complete `add_parser(...)`,
  `add_argument(...)`, mutually exclusive group, and `set_defaults(...)`
  calls that implement this command.
- A dispatch branch: include the complete branch body, not only the case
  label or return expression.
- A test: include the whole test function, including fixtures, nested helper
  functions, monkeypatch setup, assertions, and trailing blank lines.
- A Markdown section: include the complete heading section or complete fenced
  block that describes the behavior.

When the planned region is one entry inside a shared aggregation unit, do not
steal the whole unit and do not leave the batch syntactically incomplete.
Copy the shared wrapper/context into this batch with `include --to
decompose-NN-NAME --no-auto-advance`, then discard only the current
concern's owned entries with `discard --to decompose-NN-NAME
--no-auto-advance`.

Shared aggregation units include parenthesized import groups, `__all__`
lists, parser/subparser containers, registry dictionaries and lists,
TOML/YAML arrays or tables, and Markdown sections or fences whose heading or
context is shared.

For modest syntax-fixing replacements, prefer `--as-stdin` over `--as` so the
replacement text is exact and does not fight shell quoting:

```bash
cat <<'EOF' | git-stage-batch discard --to decompose-NN-NAME --line IDS --as-stdin --no-auto-advance
REPLACEMENT TEXT
EOF
```

Bad: peeling `prepare = subparsers.add_parser(...)` plus only the interior
`"--case"` lines, leaving positional arguments inside `add_parser`.

Good: peeling the complete `prepare = subparsers.add_parser(...)` call, the
complete `prepare.add_argument("--cases", ...)` call, the complete
`prepare.add_argument("--case", "--case-id", ...)` call, and the
`prepare.set_defaults(...)` call.

Bad: repeatedly discarding the same moving page range such as `833-854`
because previous discards shifted the next test upward.

Good: identify the next whole `def test_...` block in the immediately
preceding review output, discard only that complete test block, then re-run
`show` before selecting the next block.

Bad: discarding only `CaptureMissingResult,` from a parenthesized import
group, leaving the batch with dangling indented names.

Bad: discarding the entire parenthesized import group when other imported
names belong to lower-level concerns that must remain in the working tree.

Good: include the import group's wrapper and shared neighboring names into
the current batch, then discard only this concern's imported names into that
same batch.

If a page boundary splits a syntactic unit, continue on the next page
immediately and finish that same unit before moving to another file or
another concern. Do not leave a batch with a known partial parser call,
partial function, partial test, or partial fenced block.

## Repair Rules

After peeling, make the smallest repair needed to keep the remaining working
tree coherent:

- remove imports for peeled symbols
- remove calls to peeled functions
- simplify data structures that lost fields
- fix syntax left by partial removal
- delete files that became empty

Capture repair edits into `decompose-NN-NAME-repair` with `include --to`.
If no repair was needed, do not create a repair batch.

## Local Verification

Before returning success:

- verify the concern batch itself is syntactically coherent from its git ref,
  not only the remaining working tree
- compare `batch.json` `presence_claims` against the concern ledger and
  investigate any hole inside a planned syntactic unit
- verify the batch contains only the current evolution ladder step, with no
  future content that should appear in a later product state
- run `python -m py_compile` on edited Python files when possible
- verify no obvious dangling imports or orphan test fragments remain
- verify the upfront note is still a deliberate one-purpose note

For every Python file present in the concern batch, materialize that file from
the batch ref and compile it:

```bash
git --no-optional-locks cat-file -p refs/git-stage-batch/state/decompose-NN-NAME:batch.json
git --no-optional-locks show refs/git-stage-batch/batches/decompose-NN-NAME:PATH > /tmp/decompose-NN-NAME.py
python -m py_compile /tmp/decompose-NN-NAME.py
```

This check intentionally compiles the batch content as it would rebuild from
the base commit. It catches partial parser calls, partial test functions, and
syntax left by stale line IDs. If it fails, do not continue to another file or
return success. If the failure is caused by missing shared wrapper/context,
copy that context into the same concern batch with `include --to`; do not
discard dependency-owned lines just to make the batch parse. If the failure
is caused by an actually partial owned unit, use `git-stage-batch undo` until
the bad selection is removed, or immediately peel the missing owned lines from
the same syntactic unit and re-run the check.

The metadata coverage check is not a demand to include unchanged context
lines. It is a demand to notice suspicious holes in changed source regions.
For example, if the ledger says `lines 328-440, prepare subparser
registration` and `batch.json` claims `328-330,334-440`, inspect the missing
lines before moving on. If the missing lines are part of the same
`add_parser` or `add_argument` call, peel them immediately or undo the partial
selection. If the missing lines are shared aggregation context, include them
into the batch as copied context and keep ownership assigned to their original
concern. Do not carry a batch whose content proves a parser call, test
function, import group, registry, or Markdown fence was sliced.

If verification shows that the upfront note no longer describes every
owned line in the batch, do not broaden the note. Split or undo the offending
selection.

## Output

Return exactly one of:

- `OK_BATCH_PEELED`: include batch name, optional repair batch name, files
  touched, verification performed, and note used.
- `FAIL_SPLIT_REQUIRED`: include the narrower splits needed and the precise
  reason the requested batch is too broad.
- `FAIL_BLOCKED`: include the external blocker or tool failure.

---
name: commit-unstaged-changes
description: Stage unstaged working tree changes into one or more atomic commits with git-stage-batch. Use when the user wants Codex to split unstaged edits into a clean commit series with project-compliant commit messages. Do not use for already-staged changes or for generic git help.
metadata:
  short-description: Commit unstaged changes atomically
compatibility: Designed for Codex with git and git-stage-batch available directly or via pipx.
---

# Commit Unstaged Changes

Use this skill to turn the current **unstaged** working tree into one or more
atomic commits with messages that match project conventions.

Adopt a meticulous, completion-oriented stance toward history curation: once
the correct split is clear, keep executing it until the series is finished or
a listed blocker is reached. Be tenacious, resiliant, and autonomous.

This skill is autonomous and non-interactive. Inspect the working tree, use
`git-stage-batch` to stage related changes, create one or more commits as
needed, and then stop without asking the user to review each message first.

`git-stage-batch` is not just the preferred staging mechanism here. It is the
core execution workflow for carrying out the split. When the history requires
temporarily setting work aside, put that work into named batches with
`discard --to BATCH` or `include --to BATCH` instead of moving it aside by
hand. When a commit needs an earlier coherent version of a changed region or
file, use `include --line --as`, `include --line --as-stdin`, `include --file
--as`, or `include --file --as-stdin` to stage that earlier state rather than
editing unrelated files outside the `git-stage-batch` flow.

Do not broaden or collapse the commit plan merely because the staging work
looks tedious, mixed files require line-level selection, or the analysis is
taking longer than expected. "To keep this tractable", "to reduce tool calls",
"to avoid backtracking", or any similar rollout-efficiency rationale is
explicitly invalid in this skill. If the right split is clear, keep following
it. If the right split is not clear enough to execute safely, stop and report
the ambiguity or blocker instead of inventing a coarser history.

If the remaining split is clear but the next staging step is more intricate,
do not stop merely because it involves repeated `show --file` inspection,
line-level staging, `--as` replacements, batch peeling, temporary restaging of
the index, or other careful execution work. Those are normal costs of this
skill, not blockers. Before stopping, identify one concrete blocker:

- a `git-stage-batch` bug or incorrect staged result
- an intended history that is genuinely ambiguous in a way that risks the
  wrong split
- a repository policy conflict that cannot be resolved from local context
- a missing capability where the desired split cannot be expressed safely with
  the available staging tools

If none of those apply, continue staging. If you can name the exact next
`git-stage-batch`, `git diff --cached`, or `git show :path` command you would
run to keep splitting the current commit, you should usually run it rather
than stopping.

Do not stop merely because a substantial amount of correctly split work still
remains. "This is still a lot of work", "the rest of the series is
substantial", "I have already made enough commits for now", or similar
reasoning is not a blocker under this skill. If the next split is still clear
and stageable, continue until the intended series is finished or one of the
explicit blockers above is reached.

If `git-stage-batch` is available directly in `PATH`, use it. If it is not,
fall back to `pipx run git-stage-batch`.

This skill does not control Codex sandbox or approval mode. If a required git
or `git-stage-batch` command fails because `.git` is read-only or because the
sandbox blocks writes needed for staging, committing, or batch metadata, rerun
that command with an escalation request instead of assuming the repository is
misconfigured or abandoning the workflow.

## Installed resources and project guidance

Set `ASSET_ROOT` to the absolute `.agents` directory containing this installed
skill, two levels above its skill directory. Resolve it from the loaded
`SKILL.md` path, whether installed in the repository or the user's home.
Resolve `references/` and `scripts/` paths relative to this skill directory.
Keep the working directory in the target repository, quote helper paths in
shell commands, and pass `ASSET_ROOT` to workers that use companion resources.

Before drafting, search the project tree for `CONTRIBUTING.md`, excluding
installed assistant assets. Check the repository root, `.github/`, `docs/`,
and guidance scoped to the changed paths. Read the applicable project guide.
Only if the project has no contribution guide of its own, read
`$ASSET_ROOT/internal/CONTRIBUTING.md`. Other project instructions and commit
hooks still take precedence over bundled defaults. Pass the selected guide's
path and applicable rules to any drafting worker.

## Repository-Agnostic Split Policy

Apply the same split logic in every repository. Do not anchor the decision to
project-specific nouns such as assistant names, package names, subsystem names,
or filenames. First normalize the diff into generic behavior axes, then split
 on those axes.

Use these generic axes:

- groundwork: helper logic, shared data structures, remapping, translation,
  persistence plumbing, ownership machinery, or refactors that can land
  without changing a user-facing operation on their own
- adopter: one command, subcommand, workflow, persistence path, replay path,
  recording path, install path, or other externally invocable operation that
  starts using groundwork
- selection semantics: how an existing operation chooses targets, files,
  entries, matches, filters, or scope
- surface expansion: a new target, group, mode, assistant, package, asset
  family, install destination, packaging surface, or externally visible
  capability
- validation: tests, fixtures, docs, examples, manpages, or packaging checks

Every code change must be assigned to exactly one of those axes before staging
begins. If a planned commit still contains code from more than one non-
validation axis, split it by default.

Examples are evidence, not scope. If the skill says to split stale-source
adopters or filter-vs-surface work in one repository, apply the same abstract
rule to analogous diffs in every other repository even when the nouns differ.

Distinguish abstract capability families from concrete implementations.

Do not treat one high-level category such as "database support",
"authentication support", "assistant support", "cloud support", or
"storage backends" as a single concern by default. Adding one concrete
implementation under that category is a separate concern from adding a
second implementation under the same category.

Examples of concrete implementations include:

- one backend versus another backend
- one provider versus another provider
- one target platform versus another target platform
- one install destination versus another install destination
- one protocol adapter versus another protocol adapter
- one asset group versus another asset group
- one bundled entry versus another bundled entry inside the same asset group
- one template or manifest entry versus another sibling entry in the same directory

These distinctions apply equally to data-only additions such as skill
definitions, agent configurations, templates, and bundled manifests. Files
organized under platform-specific sibling directories or targeting different
tool families are separate asset groups even when they provide the same
logical capability or share identical internal structure. Platform boundary is
an asset-group boundary by default.

Sibling entry boundary is also a split boundary by default. If one directory,
manifest, or bundled asset group contains two or more entries that are
separately installable, selectable, invocable, or independently meaningful to
review, treat each entry as its own concrete implementation even when the
entries share identical boilerplate, live under one installer command, or are
marketed as one bundle.

If a diff adds or changes two or more concrete implementations, assume
the correct split is:

1. shared groundwork, if any
2. one commit per implementation
3. validation for that implementation as required by repository rules
4. docs for that implementation when they belong in the same series

Shared purpose at the category level is not enough to combine them.
"database support", "authentication support", "assistant assets", or
similar umbrella wording is too broad when the diff contains separate
implementation-specific paths.

When a rule below says a broader commit is allowed only if separating it would
leave an "intermediate commit" broken, apply that phrase narrowly. A broken
intermediate commit is one where the repository would fail in a concrete,
immediate way after the narrower commit lands, such as a missing import,
missing symbol reference, broken parser dispatch, necessarily failing test in
that commit, invalid persisted shape, or missing packaged/install-time asset.

The following do not count as a broken intermediate commit:

- the two changes live in the same command, subcommand, module, package, file,
  YAML document, or documentation section
- the changes share helper code, ownership machinery, source refresh logic, or
  another dependency that can land on its own
- the broader commit feels easier to explain, review, or stage
- the narrower split would require more line-level staging, more passes, or
  more temporary batches
- the broader split still "feels coherent", "seems defensible", or preserves a
  high-level story

Before keeping any commit that touches more than one plausible axis or more
than one independently testable behavior, run this mandatory falsification
test in your working notes:

1. Name the narrowest plausible split in generic terms.
2. Name the exact command, test, import, parser path, runtime path, or
   packaged/install-time asset that would be broken immediately after that
   narrower commit.
3. Explain why that failure would happen in the intermediate history.

If you cannot answer step 2 concretely, the broader commit is forbidden.
The fact that two implementations satisfy the same abstract feature goal does
not count as coupling.

## Core Workflow

1. Inspect the repository state with `git status --short`.
2. Run `git-stage-batch check-unstaged` to check whether the index is
   suitable for this unstaged-only workflow.
3. If `check-unstaged` fails, stop and tell the user this skill only handles
   **unstaged** changes. Do not commit the staged set, and do not use
   `git-stage-batch` to add more changes on top of it. If it succeeds with a
   staged-deletion and/or start-time rename notice, continue; `start`
   normalizes those file-level states into workflow content.
4. If a required command fails because `.git` is read-only or a write under
   `.git` is blocked, immediately retry it with escalated permissions. Do not
   keep probing the repository with more read-only commands once the sandbox
   limitation is clear.
5. Check for repository-specific commit guidance before planning messages.
   Read `CONTRIBUTING.md` when present, and inspect `.git/hooks/commit-msg`
   when present, so commit prefixes, body format, trailers, and validation
   rules come from the repository rather than guesswork. Verify the hook path
   with a direct filesystem check such as `test -f` or `ls`; do not infer its
   absence from tracked-file searches such as `rg --files`.
6. Derive commit-layout constraints from that guidance before reviewing the
   diff as a commit series. Do not treat the hook as message-only validation.
   Extract any path-scoped rules, required prefixes for specific directories,
   restrictions on mixing paths in one commit, required paragraph counts, line
   length limits, trailers, and any other formatting constraints that can
   affect how the series must be split. Carry those constraints forward as
   hard requirements for both staging and message drafting.
7. Review the full unstaged diff before deciding how many commits are needed.
   For each hunk, classify every changed line by the concern it serves, not
   by the file it lives in. When a single hunk contains lines serving
   different concerns, note that the hunk will need line-level staging later.
   Carry these per-line classifications into the series outline in the next
   step.
8. Draft a commit series outline before staging anything. Write one line per
   planned commit: its prefix and a specific purpose clause that passes the
   self-test. Order the commits so the series tells a coherent story:
   foundational changes first, then per-file or per-module applications. When
   the series both fixes an existing target and adds a new one, put the
   existing-target fix first, then its docs or tests, then the new-target
   support, then its docs or tests unless that ordering would break the
   series. This outline drives all subsequent staging decisions and commit
   messages. Revise the outline if staging reveals a split you did not
   anticipate.
   When the diff mixes corrective work for an existing workflow with
   unrelated capability expansion, treat the corrective work as earlier
   history by default. Commit the fix series first, including any required
   path-pure docs or tests commits that belong with it, before staging the
   separate feature series unless that ordering would leave the history
   broken.
   The outline must satisfy the derived hook constraints from the start. If
   the hook requires path-pure commits such as `tests:` or `docs:` commits
   that only touch those directories, split the series that way before any
   staging begins instead of discovering the rule at commit time.
   For each planned commit, list which concern categories it touches:
   existing-target behavior, new-target support, selection behavior,
   shared plumbing, docs, and tests. If a planned commit touches more than
   one non-validation category, assume the plan is too broad and split it
   before staging unless the mandatory falsification test identifies an exact
   broken intermediate commit. When two or more split shapes seem plausible,
   choose the more fine-grained one unless the narrower split fails that test.
   After that classification, run a counterexample audit for each planned
   commit. Ask:
   1. Does this commit contain shared plumbing plus one or more adopters?
   2. Does this commit change an existing workflow while also adding a new
      target, integration, asset group, or install surface?
   3. Does this commit change selection semantics while also expanding what
      can be selected or installed?
   4. Does this commit change two or more externally invocable workflows,
      even if they are sibling variants under one command?
   5. Does this commit add or change two or more concrete implementations
      inside one abstract feature family, such as two backends, two providers,
      or two assistant targets?
   6. Does this commit add or change two or more sibling bundled entries that
      users can install, select, invoke, or review independently, even if
      those entries live under one asset group or one packaging surface?
   If any answer is yes, split by default. Only keep the work combined if the
   mandatory falsification test identifies the exact broken intermediate
   command, test, import, runtime path, or packaged/install-time asset that
   would fail after separating it.
   After that, run an independent-failure audit for each planned commit. Ask:
   1. Does this commit fix more than one failing test scenario?
   2. Does this commit fix more than one stale-state path, error path, or
      incorrect-selection path?
   3. Does this commit resolve more than one independently describable user
      complaint or complaint you have as the maintainer?
   If any answer is yes, split by default even when the lines share one
   helper, one state file, one data structure, or one command name. Only keep
   the work combined if you can name the exact invariant that is common to all
   of those failures and the exact immediate failure that would appear in the
   narrower intermediate history.
   For every planned commit that changes command behavior, name the exact
   operation it changes. If the purpose clause still describes more than one
   command, subcommand, workflow, or external entry point after naming them,
   the plan is too broad and should be split unless the mandatory
   falsification test identifies the exact immediate failure in the narrower
   history.
   After outlining the series, assign every changed file to one planned
   commit before staging begins. If a file appears to belong to multiple
   planned commits, treat that as evidence that the split is still too broad
   or that later line-level staging will be required.
   Do not let a mixed file fall back to coarse path staging just because one
   planned commit owns most of it. When one file contains lines for more than
   one planned commit, treat `include --files` as unsafe for that file until
   the later lines have been peeled away or the current commit has been
   narrowed with line-level staging. In that situation, `skip --files` may
   still be used to defer unrelated path groups, but `include --files` must
   not be used as a shortcut for "mostly this file".
   Similarity is not coupling. Two changes that feel like the same kind of
   work, such as two bug fixes, two command updates, or two docs edits, still
   need separate commits when each one fixes a different user-visible behavior
   or adds a different capability. Shared vocabulary, adjacent code, or a
   common subsystem does not make them one concern.
   Before staging begins, walk the outline against this checklist. Every item
   must pass. If any item fails, revise the outline before continuing.
   - [ ] No planned commit spans files from two or more platform-specific or
         tool-family sibling directories such as `claude-skills/` and
         `codex-skills/`, `postgresql/` and `mariadb/`, or `aws/` and `gcp/`.
         Platform boundary is an asset-group boundary.
   - [ ] No planned commit bundles two or more independently definable
         implementations, assets, or configurations, even when they serve the
         same logical capability or share identical internal structure.
   - [ ] No planned commit bundles two or more sibling bundled entries from
         one directory, manifest, or asset group when those entries can be
         installed, selected, invoked, or reviewed independently.
   - [ ] Each commit's purpose clause uses no conjunctions (`and`, `also`,
         `as well as`) and no semicolons. An umbrella phrase that avoids
         conjunctions only by being vague still fails.
   - [ ] No commit summary uses umbrella wording such as "add assistant
         assets", "add database support", or "bundle definitions" when the
         diff contains implementation-specific paths for more than one
         concrete target.
   - [ ] For every commit that spans more than one file, the mandatory
         falsification test has been run in working notes: (a) name the
         narrowest plausible split, (b) name the exact breakage in the
         intermediate history, (c) explain why. If step (b) cannot be
         answered concretely, the commit must be split.
   As part of the outline, write down three explicit lists before staging:
   1. groundwork commits whose code can land without changing command-level
      behavior
   2. adopter commits, one per command, subcommand, workflow, or persistence
      path that starts using that groundwork
   3. expansion commits, one per new assistant target, asset group, install
      surface, or packaging surface
   If any one planned commit still contains items from more than one of those
   lists, split it before staging unless the mandatory falsification test
   identifies the exact immediate failure in the narrower history.
   After that, run an axis audit for every changed file and mixed hunk:
   write down which lines are groundwork, which lines belong to adopter A,
   which lines belong to adopter B, which lines change selection semantics,
   and which lines expand the surface area. If two lines in one hunk map to
   different axes, plan to use line-level staging or batch peeling before
   staging begins.
9. Read affected files as needed so the commit messages describe the real
   capability and problem, not only the raw diff.
10. Group changes into atomic commits that each have a single clear purpose.
   Prefer more, smaller commits over fewer, larger ones when each commit
   stands on its own.
   When in doubt between a coarser split and a finer split, prefer the finer
   split if each resulting commit remains coherent and usable. The default
   bias of this skill is toward more commits, not fewer.
   Optimize for pristine history, not for lower token usage, fewer tool calls,
   or shorter staging sessions. A broader commit that saves analysis or staging
   effort is still the wrong split when a finer coherent history is possible.
11. Use `git-stage-batch` commands to stage only the changes for the current
   commit.
12. Before writing or creating a commit, compare the staged path set against
   the derived hook constraints and restage if they do not match. Do not use
   a failing commit attempt as the first time those rules are checked.
13. If the staging session was long, the diff is subtle, or you want a fresh
    context for commit-message drafting, spawn a read-only subagent after the
    current commit is fully staged. Use the shared `commit-message-drafter`
    constraints as the canonical briefing template rather than drafting the
    message in the same long-running staging context.
14. Write or finalize the commit message using the conventions below.
15. Create the commit.
16. Repeat until all intended changes are committed.
17. End any active `git-stage-batch` session with `git-stage-batch stop`.

## Staging Rules

- Before staging anything for a commit, state its single purpose in one clause
  with no conjunctions. If you need `and`, `also`, `as well as`, or a
  semicolon to describe the purpose, you have two commits, not one. Split
  first, then stage. This self-test is mandatory for every commit in the
  series.
- After writing the purpose clause, test it by asking whether the clause could
  be broken into two narrower purposes that are each independently meaningful.
  If it can, the commit conflates concerns. Rewrite the purpose at the
  narrower level and split accordingly. An umbrella phrase that avoids
  conjunctions by being vague is not a single purpose.
- Before staging anything, look for natural split points in the diff. Changes
  that touch different concerns, even within the same file, are usually
  separate commits. A large diff that "serves one purpose" at a high level
  often contains independently reviewable steps at a closer look.
- Do not rewrite the split plan around execution convenience after this closer
  look. A plan does not become "good enough" because line-level staging is
  awkward, a file is mixed, or the next clean split would take more effort.
  If the narrower plan still produces coherent history, keep the narrower plan.
- If two split shapes both seem reasonable, choose the one with more commits
  as long as each commit still has a single clear purpose. Only keep the
  broader shape if the mandatory falsification test identifies the exact
  immediate failure in the narrower history.
- Treat hook-enforced path scope as part of the split logic, not as cleanup at
  the end. If the hook requires a `tests:` prefix for commits that touch
  `tests/`, or forbids mixing `docs/` paths with code under a `docs:` summary,
  build those path-pure commits into the original plan and stage to that shape
  intentionally.
- Do not combine concerns casually just because they are broadly similar. Two
  bug fixes are not one concern just because both are bug fixes. Two command
  changes are not one concern just because both affect the same CLI. If a
  reviewer could reasonably want one change without the other, split them.
- Do not combine multiple concrete implementations into one commit merely
  because they belong to the same feature family. If one change adds a
  PostgreSQL backend and another adds a MariaDB backend, or one change adds
  PAM support and another adds /etc/passwd support, those are separate
  concerns by default even if both live under one command, one module, one
  interface, or one documentation section.
- Prefer commit purposes at the implementation level, not the category level.
  Summaries such as "add database support", "add authentication", "add cloud
  backends", or "add assistant assets" are too broad when the staged diff
  contains more than one implementation-specific path.
- Shared command surface is not coupling. Two changes under one command,
  subcommand, or module still require separate commits when they affect
  different behavior axes or independently testable scenarios. The same file,
  parser branch, or help text is not enough to keep them together.
- When shared helpers can land on their own, commit them first. Then apply
  that groundwork one implementation at a time.
- Do not treat shared plumbing as a commit shape by itself. If one planned
  commit fixes two or more independently testable failure modes in helpers,
  state management, remapping, persistence, ownership, or selection plumbing,
  split by failure mode unless one fix cannot function without the other in
  the intermediate history.
- Do not treat "one command" as "one concern" by default. If a planned commit
  changes one command in two or more independently describable ways, name each
  behavior separately. If those behaviors could be tested, demonstrated, or
  complained about independently, split them unless the mandatory
  falsification test identifies the exact immediate failure in the narrower
  history.
- Groundwork commits have a stricter gate than adopter commits. Before keeping
  a groundwork commit, verify that it introduces or restores exactly one
  invariant, fixes exactly one family of failures, and can be explained
  without `and`, `also`, or `while`. If the explanation needs more than one
  independently meaningful problem statement, split the groundwork.
- Before keeping any corrective commit, list the exact failing tests or
  scenarios it fixes. If the list contains more than one independently
  meaningful item, split by default. Only keep the fixes combined when those
  scenarios are all symptoms of the same broken invariant and a narrower split
  would leave an intermediate commit broken.
- Do not rely on repository-specific labels when deciding what is atomic.
  Replace local nouns with generic roles such as groundwork, adopter,
  selection semantics, and surface expansion. If the split only makes sense
  when phrased in local names, the skill is overfitting to the current repo
  rather than following a reusable rule.
- Distinguish new-target support from behavior changes for existing targets.
  Adding support for a new assistant, skill host, module, or interface is not
  the same concern as changing how an existing one behaves. Sharing a command,
  subcommand, or module is not enough to merge them. If a change affects
  current users of Claude and also introduces Codex support, split those unless
  an earlier commit would be unusable on its own.
- Distinguish selection behavior from install-surface expansion even when both
  live under the same command. Changing how users select entries, such as
  moving from exact names to pattern filters, is a separate concern from adding
  a new asset group, assistant target, or install location.
- In particular, install-surface changes must be split mechanically:
  1. selection semantics for existing install surfaces
  2. validation for that selection change as required by repository rules
  3. new packaged assets or companion config for the new surface
  4. command wiring for the new surface
  5. validation and docs for the new surface as required
  Do not collapse those steps unless the mandatory falsification test
  identifies the exact immediate failure in the narrower history.
- Distinguish corrective work on an existing workflow from unrelated feature
  expansion even when both touch the same subsystem or both seem like feature
  work at a high level. If one slice repairs stale behavior, follow-up
  correctness, replacement handling, or another existing workflow limitation
  while another slice adds a new capability elsewhere, land the corrective
  slice first unless an earlier commit would be broken.
- Do not treat "the same fix across multiple workflows" as one concern by
  default. If two or more externally invocable workflows need the same
  corrective logic, assume the right split is shared groundwork first and then
  one follow-on commit per adopting workflow. Only keep them combined if the
  mandatory falsification test identifies the exact immediate failure in the
  narrower history.
- When a series contains both an existing-target fix and new-target support,
  prefer this order: fix the existing target, document or test that fix, add
  the new target, then document or test the new target. Do not put the new
  target first unless the existing-target fix depends on groundwork that would
  otherwise leave the history broken.
- Treat install-surface expansion and selection-behavior changes as separate
  concerns. For example, adding a new `install-assets` group such as
  `codex-skills` is not the same purpose as changing that command from exact
  skill names to pattern filters, even though both changes live under the same
  subcommand.
- Do not collapse multiple concerns into one purpose clause just because they
  share a command surface, file, or documentation section. A summary like
  "expand install-assets" is too broad if the diff both changes selection
  semantics and adds a new assistant target. Write the narrower purposes out
  explicitly and split them.
- Treat distinct user-facing operations as distinct concerns by default, even
  when they share helpers or data structures. If one command, subcommand, or
  workflow can be fixed independently of another, split them unless an
  intermediate commit would be broken. Shared plumbing may justify an earlier
  groundwork commit; it does not justify bundling the later behavior changes
  together. When a helper, remapping layer, ownership model, or other shared
  mechanism can land on its own without changing user-visible behavior, treat
  it as groundwork. If two or more commands or workflows then adopt that
  groundwork independently, default to one follow-on commit per adopting
  operation rather than one combined fix. Only collapse those follow-on
  commits when a separate application step would leave the history broken.
- When a change introduces shared plumbing and then updates multiple distinct
  user-facing operations to use it, default to one groundwork commit plus one
  follow-on commit per adopting operation. Treat each command, subcommand,
  workflow, or externally visible entry point as its own concern unless a
  separate application commit would leave the history broken.
- When one change path records masked or translated state and another change
  path later persists, reapplies, discards, or otherwise consumes that state,
  treat those as separate adopters by default. Shared ownership or source
  refresh logic is groundwork; the recording path and the consuming path are
  separate command-level operations and should land in separate commits unless
  one side cannot function in the intermediate history.
- Do not combine one groundwork change with multiple adopting operations in
  the same commit merely because the current diff arrived that way. Shared
  scaffolding plus two or more adopters is a signal to split the history, not
  a reason to keep it together.
- If one adopter records or translates state for one workflow and another
  adopter persists, reapplies, or discards that state for a second workflow,
  still treat them as separate adopters. Shared state machinery is groundwork;
  the operations remain separate concerns unless splitting them would leave one
  side broken.
- Do not combine two command-level applications into one commit merely because
  they rely on the same new helper, remapping logic, ownership model, source
  refresh mechanism, or other dependency. Shared dependency is not the same
  thing as shared purpose. If the groundwork can land without changing
  user-visible behavior, commit it first and then apply it one operation at a
  time.
- Before combining operations anyway, name the exact intermediate failure that
  would result from separating them. If you cannot describe the broken
  intermediate state concretely in terms of a command, test, import, runtime
  path, or packaged/install-time asset, the operations are not coupled enough
  to share one commit.
- Use a falsification test before keeping any broad commit. Try to argue for a
  narrower split in generic terms:
  1. groundwork without adopters
  2. one adopter at a time
  3. selection semantics before surface expansion
  4. existing-surface fixes before new-surface additions
  If that narrower story still builds, runs, or remains internally coherent,
  the broader commit is disallowed.
- Treat "internally coherent" strictly here. A narrower story remains coherent
  unless the mandatory falsification test identifies an exact immediate
  failure after that narrower commit lands.
- Selection expansion has two axes. Filtering, matching, or pattern-based
  selection changes who the existing command can target. New assistant groups,
  packaged skills, repo-local configs, or other new destination surfaces
  change what the command can install. Treat those axes as separate commits by
  default even when both are implemented inside the same subcommand.
- Packaging is a separate expansion surface. If a code change adds a new asset
  family and another change updates wheel contents, packaged config files, or
  install destinations for that family, stage the packaged assets with the new
  family support, not with unrelated selection or filtering behavior.
- Run a cross-impact check before staging. For each hunk, note whether each
  changed line affects existing behavior, new behavior, shared plumbing, docs,
  or tests. If one planned commit touches both existing behavior and new-target
  support, assume the commit is conflating concerns until you prove the split
  would break the series.
- After outlining the series, establish the pass for the current commit with
  `--files` when the commit only covers a coarse subset of changed paths. Use
  per-hunk `skip` only after that narrower pass is in place, not as a
  substitute for deciding the commit's path scope.
- Treat `include --files` as a high-trust action. Only use it when every
  changed line in every matched file belongs to the current planned commit,
  or when earlier peeling has removed later-concern lines from those files.
  If a file has been identified as mixed during the axis audit, do not use
  `include --files` for that file in the current commit. Narrow the selection
  with line-level staging, batch peeling, or a smaller path set first.
- Treat `skip --files` differently from `include --files`. Skipping a path
  group is a safe way to postpone unrelated files after the current commit's
  path scope is known. Including a path group is a claim that the full diff
  of every matched file already belongs to the current commit. Hold `include
  --files` to that stricter standard.
- When docs or tests mention two concerns because the command surface is shared,
  do not use that overlap to justify a combined code commit. Keep the code
  split at the narrower behavioral boundary and mirror that split in docs or
  tests as closely as the repository rules allow.
- Split points exist inside hunks, not only between them. A single hunk that
  modifies two logically separate things should be split using line-level
  staging with `include --line` and `skip --line`. When a mixed hunk rewrites
  one coupled block, prefer `include --line --as $'replacement\ntext'` so the
  staged result stays coherent while excluding later-concern lines such as
  new-target support. Treat line-level staging as a normal part of the
  workflow, not an edge case.
- Use `discard --to BATCH` earlier when the selected concern is easier to
  express as "remove this later layer from the working tree for now" than as
  "stage the earlier layer directly." This is especially appropriate when a
  raw `include --line` selection would leave the index with a half-replaced
  function, duplicated top-level definition, broken parser branch, dangling
  import, or another obviously invalid intermediate file shape.
- Prefer `discard --to BATCH` over wider `include --line` guesses when one
  concern can be named cleanly as the layer to peel away, such as new-target
  support inside a broader rewrite, follow-on adopter logic sitting on top of
  groundwork, or docs/examples for a later behavior change. Peel the later
  concern out, verify that the remaining diff is one coherent earlier commit,
  and only then stage or commit that earlier slice.
- If line-level staging still cannot separate concerns cleanly, unpeel the
  feature by peeling layers into named batches. Use `git-stage-batch discard
  --to BATCH` to save the selected hunk, file, or lines into a batch and
  remove that selection from the working tree, then make manual edits so the
  remaining diff becomes one coherent concern. After committing the smaller
  slice, bring later layers back with `include --from BATCH` when they are
  ready to become their own commits. Manual unpeeling is preferable to forcing
  unrelated concerns into one commit.
- When multiple independent files share the same pattern of changes, do not
  batch them into one commit by concern. Each independently functional file,
  module, skill, config, or package gets its own commits. Batching across
  unrelated files obscures per-file history.
- Only combine changes into a single commit when they are genuinely coupled:
  splitting them would leave an intermediate commit broken or unable to build.
  Adjacency in the same file or YAML block is not coupling. Shared helper code,
  the same command surface, or a common subsystem is not enough by itself; if
  the helper changes can land first without changing existing behavior, stage
  that groundwork separately. If you can tell two plausible stories about the
  same diff, choose the narrower story unless the broader one is required to
  keep the history working.
- Before planning the first commit, decide whether the unstaged tree contains
  one series or several independent series. If several are present, outline
  and commit one series at a time. Do not force unrelated series into one
  narrative merely because they are all unstaged at the same time.
- When splitting, make each commit build on the earlier ones in a coherent
  narrative so the series tells a story from start to finish.
- A commit's position creates no introduction or conclusion duty. Include the
  context needed to explain the current patch's reason, design, or scope.
- Treat docs and tests as validation of a behavior change, not proof that the
  behavior changes belong together. If repository hooks require docs-only or
  tests-only commits, keep the code split at the narrower behavioral boundary
  first, then mirror that structure as closely as the hook allows.
- Tests, fixtures, examples, and docs for one implementation should not be
  used to justify combining it with another implementation. If two
  implementations land in separate code commits, prefer separate validation
  and documentation commits for them unless repository rules require a
  different path-pure shape.
- When repository rules require path-pure prefixes such as `tests:` or `docs:`,
  let those rules force a finer-grained series. Start from the narrowest code
  split that reflects the real behavior changes, then add separate tests-only
  or docs-only commits for each code step as needed. Do not merge adjacent test
  updates or docs updates back together merely because the hook requires them
  to be separate from code.
- Do not ask the user how to split the work unless the repository state is
  genuinely ambiguous and a wrong split would be risky.
- Do not rely on `git add -p` or `git commit -a`. Use `git-stage-batch` for
  staging decisions.
- If `.git` writes fail under sandboxing, treat that as an execution
  environment issue, not as evidence that the split plan is wrong. Retry the
  blocked command with escalation rather than changing the commit structure to
  work around sandbox limits.

## Fresh-Context Message Drafting

When the current commit is fully staged, you may spawn a subagent to draft the
commit message in a fresh context. This is useful when the staging session was
long or the diff is subtle.

Do not load another skill just to get fresh context for message drafting.
Activating that skill still expands the main context window. Use a subagent
instead when isolation is the goal, and treat the shared
`commit-message-drafter` asset as the canonical source for how that subagent
should be briefed.

If you spawn a subagent for message drafting:

1. Spawn it only after the staged diff for the current commit is complete.
2. Tell it not to stage, edit, or commit anything.
3. Give it a self-contained briefing that includes:
   - the relevant state before the patch: the operation's purpose and its
     input/output relationship, with supporting code locations
   - a representative failing case when available, keeping its triggering
     condition and consequence separate from the technical correction
   - the exact change, with supporting source evidence
   - the supplied motivation, observations, assessments, and relevant history,
     retaining their degree of certainty and who made relevant choices; identify
     supplied context separately from independently verified source facts;
     keep your inferences separate from the caller's actual claims
   - whether the patch adds machinery or makes behavior run in a caller
   - the series goal as background and only the dependencies needed to explain
     the patch's design or scope
   - which independent series this commit belongs to when the unstaged tree
     contains more than one
   - the repository-specific message rules already discovered
   - any preferred prefixes already established by history
   - the exact files staged for this commit
4. Tell it to inspect only what it needs, typically:
   - `git diff --cached --stat`
   - `git diff --cached`
   - `git log --pretty=oneline -- <path>` for representative staged paths
   - `CONTRIBUTING.md` when present
   - `.git/hooks/commit-msg` when present
5. Require it to return:
   - a short causal account of the old operation and reason for the change,
     followed by one proposed commit message
   - a short checklist confirming the prefix, declarative body prose, necessary
     reasoning, and the particular fact each cross-commit reference explains
   - any specific uncertainty about source evidence or supplied context

Review the draft in the main skill before committing. If it no longer matches
the staged diff or repository rules, fix it in the main skill rather than
pushing the problem back to the user.

Before committing, edit the draft into one alternative using the same
source evidence and supplied context. Keep the first body sentence as a
longer, non-imperative version of the summary. Preserve the intended
behavior, supported judgments, actual uncertainty, and necessary scope.

Edit the summary and its expanded first sentence too when their wording
hides the action in an abstract label. Keep the expanded sentence first,
and use it to state the actual change in ordinary words.

After that opening, edit as if a colleague has asked why we need the
change. Put the action or need in ordinary verbs. Let a short judgment
stand on its own. Remove a sentence that merely labels the change or
repeats a consequence the surrounding prose already makes clear. Keep inspection notes in CHECKS.

Check the explanation's level of detail as well as its wording. If the
reader needs a missing relationship to follow the affected input or
operation to the wrong result, restore that relationship before polishing
the implementation description.

Compare the reasoning and how naturally it is expressed. A wording
improvement may keep every fact and causal step unchanged; it does not
need a new reason or extra detail to earn its place. Preserve useful
causal links instead of replacing them with a list of operations. Check
local references from the prose alone. Unpacking an unexplained reference
is a real improvement even when it takes more words. A descriptive label
or a related word used earlier does not establish its meaning. Prefer
stating the condition and action when that saves the reader from having
to reconstruct the relationship. Keep the original if the alternative is
less natural, loses needed reasoning,
or offers no improvement. Let difficult interactions take room and direct
reasons stay brief. Length, contractions and first person do not decide
which version is better.

Repeat the edit-and-compare step using the best version so far. Make at
most three comparisons in total, including the first. Stop after two
consecutive comparisons offer no clear improvement. Keep wording that
already works; an extra comparison does not require accepting an edit.
Fix factual errors and repository-rule violations before committing,
regardless of that limit.

Write and choose in the main skill's existing review. The comparison is
internal; use the selected message for the commit.

Verify the final wording with the accuracy check in the writing guidance
below before committing. Repeat that check after any further wording edits.

## `git-stage-batch` Workflow

Use the non-interactive subcommands rather than interactive mode.
Before starting a `git-stage-batch` session, run
`git-stage-batch check-unstaged`. Do not start the session if that command
fails. A passing staged-deletion and/or start-time rename notice is allowed;
`start` presents those file-level states as workflow content alongside the
remaining unstaged work.

Treat that allowance narrowly. The staging area is allowed here only as an
intent channel for file-level states that the workflow cannot otherwise infer
reliably. Do not pre-stage ordinary additions, modifications, or mixed content
to steer the split.

### Start a pass

```bash
git-stage-batch start
```

### Inspect the selected hunk

```bash
git-stage-batch show
git-stage-batch status
```

Use `show` to view the current hunk and `status` to understand progress and
skipped work.

### Narrow a pass by file pattern

```bash
git-stage-batch show --files 'src/**' 'tests/**'
git-stage-batch include --files 'docs/**'
git-stage-batch skip --files 'vendor/**'
```

Use `--files` with gitignore-style patterns when the current commit should
cover a coarse subset of changed files. This narrows the pass, but it does
not replace line classification inside mixed hunks or mixed files. `skip
--files` is usually the safer first move when you need to postpone unrelated
paths. `include --files` should be reserved for files whose full remaining
diff has already been proven to belong to the current commit.

### Stage the selected change

```bash
git-stage-batch include
```

Use this when the whole hunk belongs in the current commit.

### Stage only part of the selected change

```bash
git-stage-batch include --line 1,3,5-7
git-stage-batch include --line 4-9 --as $'claude_only_line_1\nclaude_only_line_2'
git-stage-batch include --file path/to/file.py --as $'full staged file text'
git-stage-batch include --file path/to/file.py --as-stdin < replacement.txt
```

Line numbers refer to the hunk lines shown by `show`, numbered starting at 1.
Use line-level staging whenever a hunk mixes changes that belong in different
commits. When the selected lines sit inside one rewritten block, use
`include --line --as` to stage a coherent replacement that omits the later
concern instead of staging a broken partial rewrite. Reading the `show` output
line by line and classifying each changed line by concern is the normal
workflow for producing atomic commits from coarse hunks.

When replacement text comes from generated output or file-backed content,
prefer `--as-stdin` over `--as` so exact bytes, including trailing newlines,
are preserved. Reserve `--as` for short inline literals typed directly in the
command.

Important: `include --line ... --as TEXT` replaces only the selected
underlying changed region. The `TEXT` argument must therefore be the replacement
for that selected span, not the whole file. If you pass whole-file text to
`include --line ... --as`, any unchanged prefix or suffix outside the selected
span will be preserved as context and may be duplicated in the staged result.

Important: `--as` only works for one changed region at a time when used with
`--line`. In file-scoped views, one conceptual rewrite may still appear as
multiple disjoint regions. If the selected IDs span more than one region, the
expected workflow is to run multiple `--as` commands, one region at a time, or
peel later work away with `discard --to BATCH`. Do not treat a multi-region
`--as` failure as evidence that the history should be broader. If the narrower
split still cannot be carried out safely after region-by-region `--as` or
batch peeling, stop and report that blocker instead of broadening the commit.

When the cleanest earlier slice is easiest to express as the full staged file
contents rather than as one region replacement, prefer `include --file PATH
--as TEXT`. That form stages `TEXT` as the full index content for the selected
file-scoped path. Likewise, `discard --file PATH --as TEXT` rewrites the
working-tree content for one file-scoped path without staging it.
Prefer that approach when one file's exact current-commit content is easy to
write directly and there is no need to preserve the removed layer as a
reusable deferred unit. Prefer a named batch instead when the right split is
"remove this later layer for now" rather than "write the exact whole-file
text for this commit", especially when the later concern spans multiple hunks
or multiple files.

### Defer work to a later commit

```bash
git-stage-batch skip
git-stage-batch skip --line 2-4
git-stage-batch skip --file
```

Use `skip` for changes that do not belong in the current commit.

### Unpeel a coarse feature manually

```bash
git-stage-batch discard --to api-layer
git-stage-batch again
git-stage-batch discard --to auth-layer
git-stage-batch again
git-stage-batch include --from auth-layer
```

Use `discard --to BATCH` when the current diff is too entangled for hunk-level
or line-level staging to produce atomic commits safely. This command saves the
selected hunk, file, or lines into the named batch and removes that selection
from the working tree. Use it to peel off the topmost dependent layer, edit
the remaining tree to remove dangling references, repeat until a foundation
layer is isolated, commit that layer, then reapply later layers with
`include --from BATCH` in dependency order. This is the documented fallback
when `include --line` would still leave mixed concerns.
It is also the preferred choice when `--file --as-stdin` would require
reconstructing too much earlier text by hand or when the peeled concern should
be preserved across multiple files as one deferred unit.

Reach for `discard --to BATCH` promptly in cases like these:

- the lines you want for the current commit are spread across a rewritten
  block, but the later concern is easier to identify than the earlier one
- a tentative `include --line` plan would leave an obviously invalid
  intermediate such as a half-replaced function or duplicate top-level symbol
- the file contains groundwork plus later adopter logic, and peeling the
  adopter out first would leave a coherent groundwork commit
- the file contains an existing-behavior fix plus new-target support, and
  removing the new-target layer first would leave a coherent fix commit
- the later concern spans multiple files or mixed hunks and should be restored
  together as one named deferred layer

Use explicit batch names that describe the peeled layer, such as
`mariadb-layer`, `codex-layer`, or `docs-layer`, instead of generic names such
as `tmp` or `later`.
Do not stop to annotate a short-lived batch that will be reapplied in the
next one or two steps when the batch name already captures the deferred
concern. Add a note with `-m`, `--note`, or `annotate` when the batch will
persist across multiple commits, when several batches are active, or when the
peeled layer is subtle enough that its purpose may be forgotten.

Do not treat `discard --to` as a last resort only after many failed
`include --line` guesses. When you can already see that peeling a later layer
will make the remaining tree coherent faster and more safely, use
`discard --to` first.

### Revisit skipped work

```bash
git-stage-batch again
```

Use this after creating a commit when skipped hunks should be reconsidered for
the next commit in the series.

### Finish the session

```bash
git-stage-batch stop
```

Always stop the session when done.

## Commit Construction Strategy

For each commit:

1. Run `git-stage-batch check-unstaged`.
2. If it fails, stop and tell the user this skill only handles unstaged
   changes, except for staged text deletions and start-time renames that pass
   this check.
3. Otherwise, if no session is active, run `git-stage-batch start`.
4. Inspect each selected hunk with `show`. Read the changed lines and
   classify each one by which concern it serves. Record whether each line is
   for existing behavior, new-target support, shared plumbing, docs, or tests.
5. Before staging the hunk, ask whether the current commit would change how an
   existing target behaves. If yes, do not also introduce a new target in that
   same commit unless splitting the two would leave the history broken. The
   fact that both changes sit under one CLI command or one implementation file
   does not make them a single purpose.
   Also ask whether the current commit changes more than one externally
   invocable workflow. If it does, treat that as a split signal even when the
   workflows are sibling variants under one command.
   Also ask whether the current commit contains groundwork plus an adopter, or
   one adopter plus a second adopter. If yes, split by default. Groundwork,
   record-time adoption, replay-time adoption, and install-surface expansion
   are separate purposes unless one of those commits would leave the history
   concretely broken.
   Finally, restate the candidate commit without local repo nouns. If the
   restated purpose still contains more than one axis, the split is too broad.
6. If every changed line in the hunk belongs in the current commit, use
   `include`. If only some lines belong, use `include --line` to stage exactly
   those lines. When exact line staging would leave the index with a broken
   partial rewrite, use `include --line --as $'replacement\ntext'` to stage a
   coherent earlier-slice version of the block instead. If no lines belong,
   use `skip`, `skip --file`, or `skip --file FILENAME`.
7. If line-level staging still cannot express the split without mixing
   concerns, stop trying to force the hunk. Use `discard --to BATCH` to save
   one dependent layer into a batch and remove it from the working tree, make
   manual edits to clean up dependencies in what remains, then resume the
   series. Reapply peeled layers later with `include --from BATCH`.
8. Skip hunks or lines that belong in later commits.
9. When the current commit is fully staged, write the message following the
   message template below, then commit it.
   Before drafting the message, verify that the staged paths still satisfy any
   hook-enforced path scope for the chosen prefix. Also verify that every
   staged file belongs to the current planned commit rather than to a later
   adopter or neighboring concern.
10. If skipped work remains, run `git-stage-batch again` and build the next
   commit.
11. When no more hunks remain for the requested work, run
    `git-stage-batch stop`.

## Message Template

Re-read this guidance before writing each commit message in a multi-commit
series. Lead with the changed behavior in plain language, then explain the old
behavior and the correction. The summary should imply the actual contrast to
the status quo, using wording that fits the patch rather than a fixed word
list. State, problem, and solution are questions to answer, not paragraphs to
fill. Combine them when separation repeats the same fact; a mechanical change
may need one sentence, while a one-line correctness fix may need several
paragraphs.

Write for a competent developer who does not know this part of the codebase.
Explain the local roles and specialized terms needed for the change without
reproducing a subsystem introduction.

Keep explanations within the commit history. Do not refer to outside
development context such as "the plan" or "review results". Explain the
motivation directly, using only context available at that point in the
series.

Expect a drive-by reader to be new to the codebase but likely to read the
series together in order. Explain shared context and unfamiliar terms where
they first matter. Later messages may rely on that explanation. Keep useful
repetition, but make it a shorter reminder rather than repeating the detail;
add new detail where relevant. A standalone commit needs its own context.

```text
prefix: Summary under 68 chars

[A plain-language statement of the changed behavior, followed by the
concrete old behavior and how the change addresses it. Use declarative
narrative present tense and the explanation the reader needs.]
```

### First Line (Summary)

Use a short, lowercase prefix (`project:`, `cli:`, `patch:`,
`editor:`, `state:`, etc.). Capitalize the first word of the
summary after the colon. Keep the entire line under 68 characters.
If unsure which prefix to use, run `git log --pretty=oneline FILE`
and see what prefixes were used previously.

When repository prefixes are free-form, prefer a concrete subsystem or surface
label established by history. Generic change-type labels such as `fix:` and
`refactor:` usually add little signal. Follow Conventional Commits or another
declared type-based format when the repository requires it.

### Relevant State

Describe the program's current state at this point in history.

Describe the status quo in present tense from the viewpoint of the code
after the previous commit. Use a light cue such as `Right now, ...`,
`Currently, ...`, or `As things stand, ...` when it helps distinguish
time-dependent behavior from the change. `This commit ...` supplies the
transition wherever the explanation needs it.

Do not add a cue automatically to the first paragraph. Timeless background
or a lasting contract can stand unqualified; naming the project or component
may already make the context clear. For example, `Mutter redraws or copies
damaged regions` does not need `Right now`. If the ambiguity is in the problem
paragraph, anchor that claim instead: `Currently, buffer repair does not
consistently follow that rule.`

Avoid routinely attaching `Before this commit is applied, ...` to background
facts, since that invites a contrast even when those facts remain true
afterward.

Use `already` selectively to contrast an established capability with an
extension the patch needs. Verify it against the previous commit; do not
use the word merely to mean that something exists before the current patch.

When the relevant state comes from a recent change, describe that change
as a past event and identify it as earlier work: `Recent commits moved
buffer selection and damage history into the copy tracker.` Present tense
describes an existing state; past tense can recount the recent change that
established it. Avoid `now` alone for that transition, since it can make
the current commit sound responsible. Do not anticipate later work.

When a cue helps, use it where it resolves the ambiguity without repeating
it throughout the message. Vary the wording naturally; some repetition is
preferable to forced synonyms. Keep present tense for the change itself,
such as `This commit returns NULL on failure`, and future tense for later
work.

In message prose, use `commit`, not `revision`, and `previous commit`, not
`parent commit`.

Summarize what capabilities, interfaces, or documentation exist in
the project immediately before this commit is applied. This is the
program's state, not the user's situation. Focus on what the
program has or provides, not on what users must do or cannot do.

Describe the relevant state after the previous commit, without claiming
later work already exists or recapping unrelated earlier changes. A French
translation does not need a list of previously added languages.

If a patch exists specifically for a later capability, explain that reason and
the relevant constraint. Do not describe unrelated cleanup as groundwork.

Combine that context with the problem or change when separate paragraphs
would repeat the same fact.

### Actual Reason

Explain the underlying problem from the appropriate perspective.

Choose the perspective based on who experiences the problem:
- Use the maintainer's perspective for internal concerns. Describe the
  project's needs, choices, and limitations from inside the project; use
  first person when natural. Keep supported judgments and explain their
  basis. Never refer to maintainers as outsiders.
- Use **user perspective** for external concerns (confusing
  interfaces, missing documentation, poor workflows). Frame as
  "Users cannot X" or "Users must Y."

Describe what is non-obvious, hard to discover, confusing, missing,
or limited about the current state. Explain the actual reason at the smallest
scope that makes it understandable. Internal simplification can be sufficient.

Useful tests:
- Does the explanation identify the actual reason for this patch?
- Is this something users would notice, or only you as the
  maintainer?

Do not broaden a local reason merely to make the patch sound important.
Distinguish supplied motivation from consequences demonstrated by the code.
Do not invent reported failures, benchmarks, testing history, or rejected
alternatives.

### Change

Describe how the commit addresses one part of that problem.

Be precise about scope. If the commit only addresses one path (such
as the man page, CLI help, or internal structure), say so clearly
rather than implying the entire problem is solved.

If the commit introduces infrastructure or an early step toward a
larger feature, describe it as such.

Before claiming that the program begins an operation, verify that this patch
makes it run in the relevant execution path. Adding a helper that callers will
adopt separately is a legitimate outcome.

Prefer `This commit ...` to mark the change in narrative present tense.
Use imperative voice only in the summary; every body sentence is an indicative,
declarative statement. The transition can share a paragraph with its reason.

Use natural prose such as:
- `This commit addresses that by ...`
- `This commit begins adding support for ... by ...`
- `This commit lays groundwork for ... by ...`

### Series Context

A commit message explains its own change. Series context belongs in the
message when it explains a prerequisite, design choice, contract with a
consumer, or deliberately unfinished scope. A future use for a capability
does not establish follow-up commits. Mention later commits only when the
supplied series or plan establishes them; otherwise explain the purpose
without promising a sequence.

For each reference, identify the particular fact about this patch it explains.
Would removing it make the patch's reason, design, or stopping point unclear?
If removing it only loses knowledge of what happens elsewhere, remove it.
Merely sharing a topic or appearing next in the series is not enough.

For example, a full-damage representation change can explain why a later
shadow-copy patch can use ordinary region operations. An unused-argument
cleanup should not merely announce that the next commit fixes failed GPU
copies; it needs a meaningful connection to that work.

Describe a necessary relationship where it belongs in the explanation.
It need not occupy a separate paragraph or repeat the current patch's reason.

Being first, last, or adjacent to another commit creates no obligation to
introduce, summarize, or conclude the series. Put the series-wide overview and
recap in the review request or cover letter, while retaining the rationale
that belongs to this patch.

Verify references against actual patches. Describe work in later commits in
future tense and do not claim it is already implemented. Make references to
those commits explicit: `in a later commit`, `later commits will ...`, or
`the final commit will ...`, not bare `later`. For example, write `the
tracker introduced in a later commit`, not `the tracker introduced later`.
When a retained segue refers only to the upcoming final commit, use the
singular. Preserve useful connections; remove unrelated recaps, next-item
announcements, and repetitive boilerplate. Varying the wording does not make
an irrelevant segue useful.

Check for repeated explanation, claims that belong to another patch, and
imperative body sentences. Narrative present tense and the preferred
`This commit ...` transition apply regardless of paragraph count.

## Commit Message Guidelines

Follow any guidance found in `CONTRIBUTING.md` or
`.git/hooks/commit-msg` before falling back to the generic rules
here. Those project-specific conventions override any conflicting
guidance in this skill.

### Explain the Change

The first body sentence is a longer, non-imperative version of the summary
line. State the same change in plain language as an indicative, declarative
sentence, with enough detail to make the outcome clear. Keep that opening
before the background and reasoning.

Explicitly attribute newly introduced behavior to the current commit, using
`This commit ...` or an equally clear construction. Describe existing
behavior in present tense from the state before the patch. Leading with the
outcome does not change that temporal viewpoint. For example, `This commit
adds pattern matching to the command` identifies the patch as the cause;
`The command now accepts patterns` can blur the change with the status quo.

Use straight quotes and apostrophes in commit message prose. Avoid em
dashes. Use commas, parentheses, or separate sentences where needed.

In prose, prefer periods to semicolons.

After the opening, explain the decision as you would to another
maintainer who has not investigated the problem. Use ordinary verbs and
familiar terms, with contractions when they fit. Let the reader follow
the thought: a reasonable assumption, the catch, and the consequence that
makes the change worth doing. Include those turns when the reasoning
needs them; a direct fix may need only its direct reason.

Match the firmness of each claim to its basis. State a demonstrated
failure, unnecessary restriction, or supported cost plainly. Preserve
uncertainty about people's behavior, predictions, and design judgments
where the evidence leaves room. A message can explain the code confidently
and qualify an inference about its users in the next sentence.

State established behavior directly, with the condition that matters.
Use "can" or "may" for meaningful capability, contingency, or uncertainty,
not automatic precision. Check changed implications against the evidence.

Write decisions from inside the project. When relevant context explains
who made an earlier choice and why, retain that ownership and reasoning.
"We" can describe a shared decision or need; a component name is often
clearer for an operation. First person does not establish personal
experience or project history that the evidence does not supply.

Caller-supplied motivation, observations, assessments, and relevant history
can explain the change even when absent from the diff. Carry their meaning
and degree of certainty through the drafting handoff. In the message,
combine clarifications that explain the same fact. Include history when
it helps explain the present decision. Keep provenance and
verification limits outside the commit message. Report contradictions
with source there and avoid relying on a contradicted account as fact.
Never claim independent verification merely because context was supplied.

Supplied context establishes meaning, not a list of points to cover. Treat
a wording clarification as a constraint on the relevant claim. Do not turn
it into a separate explanation of a hypothetical misunderstanding.

Before drafting a behavior fix, explain the old operation to a developer
who knows programming but not the subsystem's domain: what is being
processed, what result is intended, and what policy relates them. Trace
the condition that exposes the defect through the intermediate effect to
the wrong result. Use prior source and supplied context to fill gaps in
that account before composing the message.

Draft from that account rather than from the sequence of edits. A reader
may understand every technical term and still not know why the bad case
arises. Establish both the relationship that creates that case and the
old code's incorrect handling of it. Use the changed code to verify the
correction and its limits. This is a drafting aid, not a required
paragraph structure.

Once the opening states the correction, the rest of the message may
explain just the old behavior and why it is wrong. Return to the fix when
the reader still needs a design choice, a non-obvious mechanism, or a
meaningful limit explained. A separate solution paragraph is not required.
Give a judgment or a turn in the argument its own sentence or paragraph
when that helps it land. Keep a brief reminder when it connects the next
step. Once the reason, decision, and necessary scope are clear, stop.

The summary should make the difference from the status quo apparent, rather
than merely name a desirable end state. Choose wording from the actual
before-and-after behavior. For example, `Align statistics with the visible
sensor crop` implies that the measured and displayed regions differ;
`Process only the selected files` implies that unselected files are currently
included. These illustrate how meaning supplies the contrast, not a fixed
vocabulary or a list of required verbs. Choose the words that best fit the
specific patch. Use a qualifier such as `only` when the restriction is real;
do not imply exact exclusion if alignment or rounding can include extra data.

The expanded opening should state that same concrete action in ordinary
words, without cataloguing internal operations or unchanged preconditions.

Select technical details for the understanding they give the reader.
For a behavior fix, describe what changes for the affected input or
operation. Establish the existing relationships and policy that explain
when the result is wrong, even when that context is unchanged by the
patch. Explain the correction at that level before introducing formulas,
fields, or helper calls. Those mechanics belong when they explain a
design choice or meaningful limit, rather than merely demonstrate how
the patch implements the correction.

A new internal API can be the outcome of a preparatory patch; explain
its purpose without cataloguing all its capabilities. When remaining
work matters to the explanation, say what still needs to be done. Use
active verbs for those actions instead of listing missing components or
call sites.

For tone, given repeated reads of an unchanged file, "We read the same
file for every row. Once is enough" explains the judgment more naturally
than "Repeated file reads represent avoidable overhead". When a field is
not yet read or forwarded, "We still need to read the field and pass its
value to the worker" puts the remaining actions in active verbs. "The reader
and value forwarding still need to be added" leaves them in nouns and
passive prose. When work remains, use a person or component as the subject
and say what they still need to do. These illustrate ordinary explanation,
not phrases to copy or a requirement to use first person.

Keep verification notes outside the commit message. A condition belongs
in the message when leaving it out would obscure the reasoning or
materially misstate the claim. The body need not demonstrate every fact
checked during inspection. Scope the claim accurately and give its
explanation room.

Make each necessary scope limit qualify the claim it belongs to when
that reads plainly. Once a clarification is expressed in that claim,
remove a sentence that only restates the same distinction. Keep a separate
limit when it explains a stopping point or another consequence. Do not
append an inventory of operations or unchanged behavior simply to account
for the patch.

Before accepting the message, read it on its own as an explanation to
someone who has not inspected the diff. Consider the question each
paragraph answers at that point in the explanation. Keep the details
that answer that question; move answers to separate questions raised
only by inspecting the implementation into review notes outside the message.

Revise in both directions: restore an intermediate step the reader
needs to understand the consequence, and remove facts whose main purpose
is to demonstrate thorough inspection. A feature's presence in the patch
supports its accuracy, but does not by itself explain why the reader
needs it. Keep the maintainer's supported judgment and the connection
between ideas when shortening the message.

After drafting or rewording, verify the meaning of the final sentences
against the selected patch, relevant prior code, and supplied context.
Check the conditions, timing and effects the message states or implies.
A simpler phrase can introduce a new claim; an operation's name does not establish
its effect. Inspect the relevant implementation when that distinction
matters. Narrow or remove unsupported implications and report any
remaining uncertainty outside the message. Correct contradictions in the
affected claim, then remove any caveat or reassurance that only repeats
its corrected meaning. Repeat the accuracy check after further edits.
Keep code evidence, supplied context, and inference distinct in review
notes.

### Structure

Use a concise subject and the explanation the reader needs:

```text
prefix: Concise summary of the change

A plain-language statement of the changed behavior, followed by the concrete
old behavior and how the change addresses it, in declarative narrative present
tense. The explanation determines the number of paragraphs.
```

### Key Principles

- Write for drive-by reviewers with limited context. Assume the
  reader does not know the project well.
- You are the maintainer; write to a casual reader. The commit
  message is you explaining the change to someone unfamiliar with
  the codebase. Never refer to maintainers in the third person.
- Keep cross-commit context when it explains a fact about this patch: a
  prerequisite, design choice, contract, or deliberately unfinished scope.
- Separate independent series. A dirty worktree can contain
  multiple unrelated commit series. Split them into separate
  series instead of forcing one message thread across all unstaged changes.
- Being first or last creates no introduction or conclusion duty. Put the
  series-wide overview and recap in the review request.
- Use the tense that reflects the state of the project just before
  the commit is applied. When discussing the old behavior, treat
  it as the current behavior. When discussing the changes, treat
  them as new behavior.
- Explain the actual reason at the smallest scope that makes it understandable.
  A local simplification or clarification of an internal contract can be
  sufficient. Do not inflate it into a project-wide limitation.
- Be precise about scope. If a change only addresses one aspect of
  a problem, do not imply it fully solves it.
- If the commit is a step toward a larger feature, say so
  explicitly. Describe the end goal briefly, then explain how this
  commit moves toward it.
- Name the feature goal in early groundwork commits. If a commit
  mainly exists to enable a later user-facing feature, say what
  that feature is and why it matters instead of presenting the
  commit as isolated infrastructure work.
- Ground assessments in concrete behavior. Avoid words
  like "cumbersome", "better", or "improved" without explaining
  why.
- Do not use `Co-Authored-By` for contributions produced from AI.
  Only use it for human co-authors.
- Only use the word `this` when referring to the commit itself.
  Use `that` or similar for other contexts.
- Wrap body paragraphs at 75 characters.
- Be humble and forward thinking. Avoid words like "comprehensive"
  or "crucial", and avoid a tone that could sound like bragging or
  seem short-sighted.
- Do not invent concise self-describing labels for internal ideas
  and use them casually, expecting the user to implicitly know what
  they should mean. Explain things in a way that reduces cognitive
  load on the reader.

### Checklist

Before finalizing a commit message, check:

- Does the summary use a fitting prefix and stay under 68
  characters?
- Does the description of existing behavior match the state before the patch?
- Is the status quo clear, with temporal cues only where they resolve an
  ambiguity rather than make lasting background sound temporary?
- Does any `already` claim identify an existing capability the patch builds
  on, rather than assume later work?
- Does the body use declarative prose and narrative present tense?
- Does the background reflect the relevant state after the previous commit
  without an unrelated recap?
- If the worktree contains multiple independent series, are they
  split into separate series?
- Does each reference explain a particular fact about this patch?
- Does the explanation use the appropriate maintainer or user perspective?
- Does it describe the actual reason without artificially broadening its scope?
- If the patch exists for a later capability, does it explain that dependency?
- Does the message clearly state what this commit does without overstating
  its impact or claiming later integration?
- If this is an incremental step, does it clearly say so?
- Would removing any cross-commit reference make this patch's reason, design,
  or stopping point unclear?
- Does every paragraph advance understanding, retaining useful reminders and removing repeated conclusions?
- Do body paragraphs wrap at 75 characters?

### Example: Single Commit

```
cli: Show review progress with --verbose

This commit adds a --verbose flag showing how many hunks have been
processed, how many remain, and the selected hunk's position.

The command currently shows the selected hunk, but not how much work is
left.
```

### Example: Commit Series

The representation change explains the contract that its consumer will use.
The consumer's message explains why its existing special case is no longer
needed. Their positions create no introduction or conclusion duty.

**Commit 1:**

```text
renderer: Use framebuffer bounds for full redraw damage

This commit marks full redraws with a region covering the whole
framebuffer.

Right now, an empty damage region means "copy everything" to the renderer
and the shadow-copy code. Region operations normally treat an empty region
as no pixels at all, so both paths have to interpret that special value.

In a later commit, the shadow-copy path will use the supplied region
directly instead of treating an empty region as a full copy.
```

**Consumer commit:**

```text
renderer: Use supplied shadow buffer damage

This commit passes the supplied shadow buffer damage directly to the copy
operation, dropping its special case for empty regions.

The preceding commit changed full redraws to supply a region covering the
framebuffer. The shadow-copy code still treats an empty region as a request
to copy everything, even though the supplied region already identifies
the pixels to copy.
```

By contrast, an unused-argument cleanup does not need "The next commit will
fix failed GPU copies." That sentence announces another patch without
explaining the cleanup's role, even if both patches touch the same function.

### Anti-Patterns

Snippets labelled "excerpt" show only part of a message. Complete examples
still start with the expanded, non-imperative summary.

❌ **Don't describe the selected state in past tense (state excerpt):**
```
The code used to only show minimal output...
```

✅ **Do describe the selected state in present tense (state excerpt):**
```
The command currently shows only the selected hunk.
```

❌ **Don't use an imperative body sentence:**
```
Add verbose output to the command-line interface.
```

✅ **Do describe the change in narrative present tense (opening excerpt):**
```
This commit adds a --verbose flag that shows review progress.
```

❌ **Don't turn missing documentation into a claim about all users:**
```
Users cannot discover interactive mode.
```

✅ **Do identify the missing explanation (problem excerpt):**
```
The man page doesn't explain the interactive review workflow.
```

❌ **Don't invent a broader reason for a local cleanup:**
```
Unused parameters prevent the rendering subsystem from evolving.
```

✅ **Do explain the actual reason, even when it is brief:**
```text
renderer: Remove the unused renderer_gpu_data argument

This commit removes renderer_gpu_data from copy_shared_framebuffer_cpu()
and its caller. The helper doesn't use it.
```

❌ **Don't leave a judgment unsupported (reason excerpt):**
```
The command-line interface is cumbersome to use.
```

✅ **Do give the judgment a concrete basis (reason excerpt):**
```
We have to run another command for each hunk. That gets tedious over a
large change.
```

❌ **Don't overstate the impact of the commit:**
```
This commit solves discoverability of interactive mode.
```

✅ **Do name the actual change (opening excerpt):**
```
This commit adds an interactive review walkthrough to the man page.
```

❌ **Don't describe the selected state too broadly in a series:**

Assume earlier commits have added translations for other languages.

```text
i18n: Add French translations

The application outputs all user-facing text in English.
```

✅ **Do describe the affected locale after the previous commits:**
```text
i18n: Add French translations

This commit adds French translations for user-facing messages.

The French locale currently falls back to English.
```

## Safety Checks

- Do not commit pre-existing staged changes directly. Staged text deletions
  and start-time renames that pass `git-stage-batch check-unstaged` must be
  handled through the `git-stage-batch` workflow before they are committed.
- Do not fold unrelated refactors, cleanup, or drive-by formatting into a
  feature commit just because they are adjacent in the diff.
- Do not try to simplify the story by broadening the commit. When the rules
  point to a narrower split, follow that split even if the broader commit
  feels easier to summarize.
- Do not rationalize a broader commit with statements about tractability,
  momentum, convenience, token limits, mixed files, or staging friction. Those
  are execution concerns, not history concerns, and they never justify merging
  separable work.
- Do not consolidate commits in the name of pragmatism. A broader commit that
  feels faster, simpler, or easier to explain is still the wrong split when
  the changes can stand as narrower, coherent history.
- Do not let rollout efficiency override history quality. Reduced token usage,
  fewer staging commands, or a shorter path to completion are not valid reasons
  to merge concerns that can stand as separate commits.
- If a hunk is too coarse, split it with `--line`; do not rationalize keeping
  it whole because it is inconvenient to separate.
- If `--line` is still too coarse, use `discard --to BATCH` to peel selected
  work into named batches, edit the remaining tree manually, and later
  reapply those batches with `include --from BATCH` instead of committing a
  blended change.
- If the skill reaches a point where carrying out the correct split would
  require more staging work than expected, continue with that staging work or
  stop and report the concrete blocker. Do not silently downgrade the split to
  "finish the task."
- Do not stop just because several valid commits have already been created
  while more clearly stageable work remains. Partial progress is not a valid
  completion condition under this skill.
- Treat this escalation ladder as the default response to intricate mixed
  staging, not as a last resort:
  1. refresh the current gutter IDs with `git-stage-batch show --file`
  2. use line-level staging for the current commit
  3. use `--as` when the earlier commit needs alternate staged text
  4. use `discard --to BATCH` and later reapply with `include --from BATCH`
     when line staging is insufficient
  5. verify the staged result with `git diff --cached` or `git show :path`
  6. continue until the planned commit is coherent or one of the explicit
     blockers above is reached
- Before committing, perform a file-to-plan audit: for each staged file, state
  which planned commit owns it. If the same commit still contains multiple
  command-level operations, multiple adopters of one groundwork change, or an
  umbrella purpose clause such as "expand", "improve", or "update" without a
  narrower operation named, split again unless an intermediate commit would be
  broken.
- If the diff cannot be split confidently without risking damage, stop and say
  why instead of guessing.

## Split Examples

Bad split:

- `cli:` change how an existing workflow selects targets while also adding a
  new target or install surface

Better split:

1. `cli:` change the selection behavior for the existing workflow
2. `tests:` or `docs:` cover the corrected existing behavior as required
3. `assets:` or `config:` add the new target's packaged data
4. `cli:` wire the new target into the existing command
5. `tests:` cover the new target as required
6. `docs:` cover the new target as required

Use this pattern whenever a series changes current behavior and expands the
set of targets, integrations, or install surfaces. Prefer landing the fix for
current users before adding the new target that benefits from that fix.

Replacement-state example:

Bad split:

- `batch:` fix stale replacement handling across source refresh, include
  masking, and discard replay in one commit

Better split:

1. `batch:` refresh stale replacement ownership primitives
2. `include:` record replacement selections against refreshed ownership
3. `tests:` cover the include path as required
4. `discard:` replay refreshed replacement selections when discarding to a batch
5. `tests:` cover the discard path as required

Use this pattern whenever shared stale-source or ownership machinery is
adopted by multiple command paths in the same diff. Groundwork lands first,
then one adopter per command-level operation.

Install-assets example:

Bad split:

- `commands:` expand install-assets with filters and Codex support

Better split:

1. `cli:` add pattern-based asset selection for existing install-assets groups
2. `tests:` cover the new selection behavior as required
3. `assets:` add the Codex skill bundle and repo-local config assets
4. `commands:` install the Codex asset group
5. `tests:` cover the Codex install path as required
6. `docs:` document the new filter syntax and Codex asset group as required

Use this pattern whenever one diff both changes how an existing install
surface is selected and adds a new assistant target or asset group. Selection
semantics and surface expansion are separate concerns even inside one
subcommand.

Bundled-entry example:

Bad split:

- `assets:` add two bundled workflow entries under one skill or template group

Better split:

1. `assets:` add one bundled entry
2. `tests:` cover packaging or install behavior for that entry as required
3. `commands:` install or expose that entry when command wiring is separate
4. `docs:` document that entry as required
5. repeat for the next bundled entry

Use this pattern whenever one diff adds or updates multiple sibling entries in
the same asset group, manifest, template directory, or packaged bundle.
Shared packaging surface is not enough to merge them when each entry is
separately meaningful to users or reviewers.

Shared groundwork example:

Bad split:

- `commands:` add shared plumbing and update multiple workflows to use it

Better split:

1. `state:` or `batch:` add shared source-refresh plumbing
2. `commands:` or `cli:` use that plumbing in one workflow
3. `tests:` cover the first workflow as required
4. `commands:` or `cli:` use that plumbing in the second workflow
5. `tests:` cover the second workflow as required

Implementation-family example:

Bad split:

- `backend:` add database support
- `auth:` add authentication support

Better split:

1. `state:` or `core:` add shared backend-selection plumbing
2. `postgres:` add PostgreSQL support
3. `tests:` cover PostgreSQL support
4. `docs:` document PostgreSQL support
5. `mariadb:` add MariaDB support
6. `tests:` cover MariaDB support
7. `docs:` document MariaDB support

Better split:

1. `state:` or `core:` add shared account lookup plumbing
2. `pam:` add PAM authentication
3. `tests:` cover PAM authentication
4. `docs:` document PAM authentication
5. `passwd:` add /etc/passwd authentication
6. `tests:` cover /etc/passwd authentication
7. `docs:` document /etc/passwd authentication

Independent bug example:

Bad split:

- `state:` preserve file-scoped selection state and refresh stale
  consumed-selection ownership

Better split:

1. `state:` preserve file-scoped selection kind and file-view offsets
2. `state:` refresh stale consumed-selection ownership

Use this pattern whenever one diff fixes multiple independently describable
state or helper failures, even when both fixes live in the same persistence or
selection machinery.

Single-command bug example:

Bad split:

- `include:` keep file-scoped selections usable

Better split:

1. `include:` preserve file-scoped selection after line-level include
2. `include:` allow same-session restaging after include `--files`

Use this pattern whenever one command picks up multiple independently testable
behavior fixes. Shared command surface is not enough to merge them.

## Quality Checks

Re-read this section before writing each commit message, especially after a
long staging session. These checks reiterate the most commonly forgotten
commit message requirements.

Before committing, verify:

- If the summary line contains `and`, `also`, or describes two actions, the
  commit combines concerns. Do not commit it.
- The staged diff is atomic and internally coherent.
- Unrelated hunks are skipped for later commits.
- If the worktree contains multiple independent series, they are split into
  separate series.
- The summary uses a fitting prefix and stays under 68 characters.
- The background matches the relevant state after the previous commit.
- The actual reason is clear from the appropriate maintainer or user perspective.
- The body uses declarative prose and narrative present tense.
- The change is clear without claiming work done by another patch.
- Each cross-commit reference explains the current patch's reason, design, or
  scope, and every paragraph adds information.
- Body paragraphs wrap at 75 characters.

The most common errors are:

1. repeating the same fact to fill separate paragraphs
2. claiming behavior that only later integration makes active
3. using bare imperative voice instead of `This commit ...`

Check for all three before committing.

## Completion

After creating the final commit, report back with:

- how many commits were created
- the subject line of each commit in order

---
name: commit-unstaged-changes
description: Stage unstaged changes into one or more atomic git commits with project-compliant commit messages
user-invocable: true
disable-model-invocation: true
context: fork
when_to_use: Use when the user wants Claude Code to turn unstaged working tree changes into one or more atomic git commits with project-compliant commit messages. Examples: "commit these unstaged changes", "split these edits into atomic commits", "stage and commit this work", "prepare a small commit series".
allowed-tools:
  - Read
  - Grep
  - Glob
  - LS
  - Agent(Explore)
  - Agent(commit-message-drafter)
  - Bash(git *)
  - Bash(git-stage-batch *)
  - Bash(pipx run git-stage-batch *)
---

# Commit Unstaged Changes

Use this skill to turn the current **unstaged** working tree into one or more
atomic commits with messages that match project conventions.

## Usage

```text
/commit-unstaged-changes
```

This skill is autonomous and non-interactive. It should inspect the working
tree, use `git-stage-batch` to stage related changes, create one or more
commits as needed, and then stop without asking the user to review each
message first.

Do not broaden or collapse the commit plan merely because the staging work
looks tedious, mixed files require line-level selection, or the analysis is
taking longer than expected. "To keep this tractable", "to reduce tool calls",
"to avoid backtracking", or similar rollout-efficiency reasoning is explicitly
invalid in this skill. If the right split is clear, keep following it. If the
right split is not clear enough to execute safely, stop and report the
ambiguity or blocker instead of inventing a coarser history.

If the remaining split is clear but the next staging step is intricate, do not
stop merely because it involves repeated `show --file` inspection, line-level
staging, `--as` replacements, batch peeling, temporary restaging of the
index, or other careful execution work. Those are normal costs of this skill,
not blockers. Before stopping, identify one concrete blocker:

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

This skill is written for Claude Code. Use Claude Code's normal file-reading
and search tools directly, and use `Agent(Explore)` only for read-only
parallel investigation that materially improves the commit split or message
quality.

To keep long staging sessions from polluting the context used for commit
message writing, use the shared `commit-message-drafter` agent after the
current commit is fully staged. That reusable agent lives separately from this
skill so future commit-oriented skills can share it without moving files
around. It should only draft the message. The main skill remains responsible
for checking the draft against repository rules, creating the commit, and
deciding whether the series narrative is still correct.

If `git-stage-batch` is available directly in `PATH`, use it. If it is not,
fall back to `pipx run git-stage-batch`.

If a required git or `git-stage-batch` command hits a Claude Code permission
request, approve and continue the intended workflow instead of treating the
request itself as a blocker or changing the commit structure to avoid it.

## Repository-Agnostic Split Policy

Apply the same split logic in every repository. Do not anchor the decision to
project-specific nouns such as assistant names, package names, subsystem
names, or filenames. First normalize the diff into generic behavior axes, then
split on those axes.

Use these generic axes:

- groundwork: helper logic, shared data structures, remapping, translation,
  persistence plumbing, ownership machinery, or refactors that can land
  without changing a user-facing operation on their own
- adopter: one command, subcommand, workflow, persistence path, replay path,
  recording path, install path, or other externally invocable operation that
  starts using groundwork
- selection semantics: how an existing operation chooses targets, files,
  entries, matches, filters, or scope
- surface expansion: a new target, group, mode, package, asset family,
  install destination, packaging surface, or externally visible capability
- validation: tests, fixtures, docs, examples, manpages, or packaging checks

Every code change must be assigned to exactly one of those axes before staging
begins. If a planned commit still contains code from more than one
non-validation axis, split it by default.

Examples are evidence, not scope. If the skill says to split stale-source
adopters or filter-vs-surface work in one repository, apply the same abstract
rule to analogous diffs in every other repository even when the nouns differ.

Distinguish abstract capability families from concrete implementations.
Do not treat one high-level category such as database support,
authentication support, assistant support, cloud support, or storage
backends as a single concern by default. Adding one concrete
implementation under that category is a separate concern from adding a
second implementation under the same category.

Examples of concrete implementations include:

- one backend versus another backend
- one provider versus another provider
- one target platform versus another target platform
- one install destination versus another install destination
- one protocol adapter versus another protocol adapter
- one asset group versus another asset group

These distinctions apply equally to data-only additions such as skill
definitions, agent configurations, templates, and bundled manifests.
Files organized under platform-specific sibling directories or targeting
different tool families are separate asset groups even when they provide
the same logical capability or share identical internal structure.
Platform boundary is an asset-group boundary by default.

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
that commit, invalid persisted shape, or missing packaged or install-time
asset.

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
   packaged or install-time asset that would be broken immediately after that
   narrower commit.
3. Explain why that failure would happen in the intermediate history.

If you cannot answer step 2 concretely, the broader commit is forbidden.
The fact that two implementations satisfy the same abstract feature goal does
not count as coupling.

## Core Workflow

1. Inspect the repository state with `git status --short`.
2. Check whether the index already contains staged changes.
3. If staged changes already exist, stop and tell the user this skill only
   handles **unstaged** changes. Do not commit the staged set, and do not use
   `git-stage-batch` to add more changes on top of it.
4. Check for repository-specific commit guidance before planning messages.
   Read `CONTRIBUTING.md` when present, and inspect `.git/hooks/commit-msg`
   when present, so commit prefixes, body format, trailers, and validation
   rules come from the repository rather than guesswork. Verify the hook path
   with a direct filesystem check such as `LS .git/hooks` or equivalent; do
   not infer its absence from tracked-file searches alone.
5. Derive commit-layout constraints from that guidance before reviewing the
   diff as a commit series. Do not treat the hook as message-only validation.
   Extract any path-scoped rules, required prefixes for specific directories,
   restrictions on mixing paths in one commit, required paragraph counts, line
   length limits, trailers, and any other formatting constraints that can
   affect how the series must be split.
6. Review the full unstaged diff before deciding how many commits are needed.
   For each hunk, classify every changed line by the concern it serves, not
   by the file it lives in. When a single hunk contains lines serving
   different concerns, note that the hunk will need line-level staging later.
7. Draft a commit series outline before staging anything. Write one line per
   planned commit: its prefix and a specific purpose clause that passes the
   self-test. Order the commits so the series tells a coherent story:
   foundational changes first, then per-file or per-module applications.
8. The outline must satisfy the derived hook constraints from the start. If
   the hook requires path-pure commits such as `tests:` or `docs:` commits
   that only touch those directories, split the series that way before any
   staging begins instead of discovering the rule at commit time.
9. For each planned commit, list which concern categories it touches:
   existing-target behavior, new-target support, selection behavior, shared
   plumbing, docs, and tests. If a planned commit touches more than one
   non-validation category, assume the plan is too broad and split it before
   staging unless the mandatory falsification test identifies an exact broken
   intermediate commit.
10. After outlining the series, assign every changed file to one planned
    commit before staging begins. If a file appears to belong to multiple
    planned commits, treat that as evidence that the split is still too broad
    or that later line-level staging will be required.
11. **Pre-staging split audit.** Before staging begins, walk the outline
    against this checklist. Every item must pass. If any item fails, revise
    the outline before continuing.
    - [ ] No planned commit spans files from two or more platform-specific
          or tool-family sibling directories (e.g., `claude-skills/` and
          `codex-skills/`, `postgresql/` and `mariadb/`, `aws/` and
          `gcp/`). Platform boundary is an asset-group boundary.
    - [ ] No planned commit bundles two or more independently definable
          implementations, assets, or configurations, even when they serve
          the same logical capability or share identical internal structure.
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
12. If the repository is large, the diff spans several areas, or the split
    into atomic commits is unclear, use `Agent(Explore)` to gather read-only
    context in parallel before staging anything.
13. Read affected files as needed so the commit messages describe the real
    capability and problem, not only the raw diff.
14. Group changes into atomic commits that each have a single clear purpose.
    Prefer more, smaller commits over fewer, larger ones when each commit
    stands on its own.
15. Optimize for pristine history, not for lower token usage, fewer tool
    calls, or shorter staging sessions. A broader commit that saves effort is
    still the wrong split when a finer coherent history is possible.
16. Use `git-stage-batch` commands to stage only the changes for the current
    commit.
17. Before writing or creating a commit, compare the staged path set against
    the derived hook constraints and restage if they do not match. Do not use
    a failing commit attempt as the first time those rules are checked.
18. If the staging session was long, the diff is subtle, or the series
    narrative is easy to lose, spawn `Agent(commit-message-drafter)` and give
    it the fully staged diff plus the exact constraints listed below. Use the
    agent's draft as input, not as an unquestioned final answer.
19. Write or finalize the commit message using the conventions below.
20. Create the commit.
21. Repeat until all intended changes are committed.
22. End any active `git-stage-batch` session with `git-stage-batch stop`.

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
  separate commits.
- Do not rewrite the split plan around execution convenience after this closer
  look. A plan does not become "good enough" because line-level staging is
  awkward, a file is mixed, or the next clean split would take more effort.
- If two split shapes both seem reasonable, choose the one with more commits
  as long as each commit still has a single clear purpose. Only keep the
  broader shape if the mandatory falsification test identifies the exact
  immediate failure in the narrower history.
- Treat hook-enforced path scope as part of the split logic, not as cleanup at
  the end. Build those path-pure commits into the original plan.
- Do not combine concerns casually just because they are broadly similar. Two
  bug fixes are not one concern just because both are bug fixes. Two command
  changes are not one concern just because both affect the same CLI.
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
  different behavior axes or independently testable scenarios.
- When shared helpers can land on their own, commit them first. Then apply
  that groundwork one implementation at a time.
- Do not treat shared plumbing as a commit shape by itself. If one planned
  commit fixes two or more independently testable failure modes in helpers,
  state management, remapping, persistence, ownership, or selection plumbing,
  split by failure mode unless one fix cannot function without the other in
  the intermediate history.
- Distinguish new-target support from behavior changes for existing targets.
  Sharing a command, subcommand, or module is not enough to merge them.
- Distinguish selection behavior from install-surface expansion even when both
  live under the same command. Changing how users select entries is a separate
  concern from adding a new asset group, package, assistant target, or install
  location.
- Distinguish corrective work on an existing workflow from unrelated feature
  expansion even when both touch the same subsystem or both seem like feature
  work at a high level.
- When a series contains both an existing-target fix and new-target support,
  prefer this order: fix the existing target, document or test that fix, add
  the new target, then document or test the new target.
- Run a cross-impact check before staging. For each hunk, note whether each
  changed line affects existing behavior, new behavior, shared plumbing, docs,
  or tests. If one planned commit touches both existing behavior and new
  target support, assume the commit is conflating concerns until you prove
  the split would break the series.
- After outlining the series, establish the pass for the current commit with
  `--files` when the commit only covers a coarse subset of changed paths. Use
  per-hunk `skip` only after that narrower pass is in place.
- Treat `include --files` as a high-trust action. Only use it when every
  changed line in every matched file belongs to the current planned commit, or
  when earlier peeling has removed later-concern lines from those files.
- Treat `skip --files` differently from `include --files`. Skipping a path
  group is a safe way to postpone unrelated files after the current commit's
  path scope is known. Including a path group is a claim that the full diff of
  every matched file already belongs to the current commit.
- Split points exist inside hunks, not only between them. A single hunk that
  modifies two logically separate things should be split using line-level
  staging with `include --line` and `skip --line`.
- Use `discard --to BATCH` earlier when the selected concern is easier to
  express as "remove this later layer from the working tree for now" than as
  "stage the earlier layer directly."
- Prefer named batches when the intended split is easier to express as
  peeling away one later concern than as writing the exact full-file content
  for the current commit. This is especially true when the later concern spans
  multiple hunks or multiple files, or when `--file --as-stdin` would require
  reconstructing too much earlier text by hand.
- Prefer `--file --as-stdin` when one file's exact current-commit content is
  easy to write directly and there is no need to preserve the removed layer as
  a reusable deferred unit.
- If line-level staging still cannot separate concerns cleanly, unpeel the
  feature by peeling layers into named batches. Manual unpeeling is preferable
  to forcing unrelated concerns into one commit.
- Before planning the first commit, decide whether the unstaged tree contains
  one series or several independent series. If several are present, outline
  and commit one series at a time. Do not force unrelated series into one
  narrative merely because they are all unstaged at the same time.
- Only combine changes into a single commit when they are genuinely coupled:
  splitting them would leave an intermediate commit broken or unable to build.
  Adjacency in the same file or YAML block is not coupling.
- Treat docs and tests as validation of a behavior change, not proof that the
  behavior changes belong together. If repository hooks require docs-only or
  tests-only commits, keep the code split at the narrower behavioral boundary
  first, then mirror that structure as closely as the hook allows.
- Tests, fixtures, examples, and docs for one implementation should not be
  used to justify combining it with another implementation. If two
  implementations land in separate code commits, prefer separate validation
  and documentation commits for them unless repository rules require a
  different path-pure shape.
- Do not ask the user how to split the work unless the repository state is
  genuinely ambiguous and a wrong split would be risky.
- Do not rely on `git add -p` or `git commit -a`. Use `git-stage-batch` for
  staging decisions.

## Split Patterns

Bad split:

- `backend:` add database support
- `auth:` add authentication support
- `assets:` add assistant assets

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

Better split:

1. `cli:` add selection semantics for an existing install surface
2. `tests:` cover that selection behavior
3. `assets:` add one concrete asset group
4. `commands:` install that asset group
5. `tests:` cover that install path
6. `docs:` document that asset group

## Explore Agent

Use `Agent(Explore)` for read-only parallel investigation when it materially
improves the commit split or message quality.

- Good uses:
  - mapping a large diff to the files and subsystems it touches
  - checking how affected files fit together before deciding commit boundaries
  - looking up recent history or related implementations in parallel
  - summarizing commit-prefix patterns for the touched files
- Do not use `Agent(Explore)` for editing, staging, or committing.
- Keep assignments narrow and concrete, such as:
  - `Identify which files in this diff belong to the same user-facing change.`
  - `Check whether these refactors are prerequisites for a later feature in the same series.`
  - `Summarize commit-prefix patterns for the touched files.`
- Treat `Agent(Explore)` as optional acceleration, not a required step for
  small or obvious changes.

## Commit Message Agent

Use `Agent(commit-message-drafter)` when the commit is already staged and the
main skill would benefit from a fresh context for message drafting. Treat that
agent as shared infrastructure for commit-related workflows, not as something
owned only by this skill.

- Spawn it only after the current commit's staged diff is complete.
- Do not ask it to stage, commit, or edit files.
- Give it a self-contained briefing. Because a specified subagent starts
  fresh, include everything it needs in the prompt instead of referring to the
  prior conversation.
- The briefing should include:
  - the planned position of this commit in the series
  - which independent series this commit belongs to when the unstaged tree
    contains more than one
  - the one-clause purpose of the current commit
  - the exact repository message constraints already discovered
  - whether this is the final commit in the series
  - the exact files staged for this commit
  - instructions to inspect `git diff --cached`, relevant `git log` history,
    `CONTRIBUTING.md`, and `.git/hooks/commit-msg` if present
- Require the agent to return:
  - one proposed commit message
  - a short checklist confirming prefix, paragraph count, tense, and series
    narrative requirements
  - any specific uncertainty if the staged diff does not support a confident
    draft
- Review the draft before committing. If it violates the rules below or no
  longer matches the staged diff, fix it in the main skill rather than pushing
  the problem back to the user.

## Git Command Concurrency

Always pass `--no-optional-locks` to read-only git commands such as
`git status`, `git diff`, `git log`, and `git show`. Without this flag,
git refreshes cached filesystem metadata in the index, which requires
`.git/index.lock`. When Claude Code runs multiple read-only git commands
in parallel — which it does by default for concurrency-safe tools — two
stat-refreshing commands race for that lock and one fails.

Claude Code's own internal git commands already use this flag, but
commands run through BashTool do not get it injected automatically.

```bash
# correct
git --no-optional-locks status --short
git --no-optional-locks diff HEAD
git --no-optional-locks log --oneline -5
git --no-optional-locks diff --cached

# incorrect — will race when parallelized
git status --short
git diff HEAD
```

This applies to every `git` invocation in the skill that does not need to
modify the index. Commands that intentionally write to the index — such
as `git commit`, `git add`, or `git apply --cached` — must not use this
flag.

## `git-stage-batch` Workflow

Use the non-interactive subcommands rather than interactive mode.
Do not start a `git-stage-batch` session until the index is clean.

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
cover a coarse subset of changed files. This narrows the pass, but it does not
replace line classification inside mixed hunks.

### Stage the selected change

```bash
git-stage-batch include
```

Use this when the whole hunk belongs in the current commit.

### Stage only part of the selected change

```bash
git-stage-batch include --line 1,3,5-7
git-stage-batch include --line 4-9 --as $'replacement\ntext'
git-stage-batch include --file path/to/file.py --as-stdin < replacement.txt
```

Line numbers refer to the hunk lines shown by `show`, numbered starting at 1.
Use line-level staging whenever a hunk mixes changes that belong in different
commits.
When replacement text comes from generated output or file-backed content,
prefer `--as-stdin` over `--as` so exact bytes, including trailing newlines,
are preserved. Reserve `--as` for short inline literals typed directly in the
command.
Prefer a named batch instead when the right split is "remove this later layer
for now" rather than "write the exact whole-file text for this commit."

### Unpeel a coarse feature manually

```bash
git-stage-batch discard --to codex-layer
git-stage-batch again
# make any small cleanup edits needed so the remaining tree is coherent
git-stage-batch include ...
git commit ...
git-stage-batch include --from codex-layer
```

Use a named batch when direct line-level staging or `--file --as-stdin` would
leave an invalid intermediate shape or require reconstructing too much text by
hand. Name the batch after the peeled concern, such as `mariadb-layer`,
`codex-layer`, or `docs-layer`, not a generic scratch label.
Do not stop to annotate a short-lived batch that will be reapplied in the
next one or two steps when the batch name already captures the deferred
concern. Add a note with `-m`, `--note`, or `annotate` when the batch will
persist across multiple commits, when several batches are active, or when the
peeled layer is subtle enough that its purpose may be forgotten.

### Defer work to a later commit

```bash
git-stage-batch skip
git-stage-batch skip --line 2-4
git-stage-batch skip --file
```

Use `skip` for changes that do not belong in the current commit.

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

Only stop the session after **all** commits in the series have been created.
Between commits, use `git-stage-batch again` to revisit skipped work — do
not stop and restart. Run `stop` once at the very end.

## Commit Construction Strategy

For each commit:

1. Verify that the index does not already contain staged changes.
2. If it does, stop and tell the user this skill only handles unstaged
   changes.
3. Otherwise, if no session is active, run `git-stage-batch start`.
4. Inspect each selected hunk with `show`. Read the changed lines and
   classify each one by which concern it serves.
5. If every changed line in the hunk belongs in the current commit, use
   `include`. If only some lines belong, use `include --line` to stage exactly
   those lines. If no lines belong, use `skip`, `skip --file`, or
   `skip --file FILENAME`.
6. Skip hunks or lines that belong in later commits.
7. When the current commit is fully staged, draft the message directly or via
   `Agent(commit-message-drafter)`, then review it against the rules below.
8. Commit only after the final message matches both the staged diff and the
   repository-specific constraints.
9. If skipped work remains, run `git-stage-batch again` and build the next
   commit.
10. When no more hunks remain for the requested work, run
   `git-stage-batch stop`.

### Message template

Re-read this template before writing each commit message in a multi-commit
series. Fill in each bracketed section. Do not merge or skip paragraphs.

```text
prefix: Summary under 68 chars

[Present-tense description of what the project, file, or interface
currently has or provides. Do not mention the patch or what is
missing yet.]

[Description of what is missing, broken, or insufficient — and why
that matters. Use maintainer perspective for internal concerns,
user perspective for external ones.]

This commit [addresses|mitigates|resolves] that [problem] by [precise
description of what this commit changes and how it solves the problem
stated above].

[Connect to what comes next. Omit this paragraph for the final commit in the
series. For the final commit, use the preceding paragraph or a short closing
paragraph to state that the series goal has been reached.]
```

The most common errors are:

1. skipping the first paragraph and opening with the problem
2. merging the first and second paragraphs with `but` or `however`
3. using bare imperative voice instead of `This commit ...`

Check for all three before committing.

## Commit Message Guidelines

Write for drive-by reviewers with limited context. Write from the
maintainer's voice to a casual reader who does not know the codebase well.

### Required structure

Every commit message should use this shape:

```text
prefix: Concise summary of the change

First paragraph describing the selected project state.

Second paragraph describing the underlying problem.

Third paragraph describing how this commit addresses that problem.

Fourth paragraph for follow-up or final series conclusion when useful.
```

### Summary line

- Use a short lowercase prefix such as `cli:`, `docs:`, `tests:`, `build:`,
  `skills:`, or another prefix established by history.
- Check `git log --pretty=oneline -- <file>` for affected files if the prefix
  is not obvious.
- Capitalize the first word after the colon.
- Keep the full summary line under 68 characters.

### First paragraph

- Describe the project's selected state immediately before the commit is
  applied.
- Use present tense.
- Describe what the program, project, interface, or documentation has or
  provides.
- Do not describe the patch, the user's situation, or future goals here.
- If the commit is part of a series, reflect the cumulative state after all
  earlier commits in that series.
- If this is the first commit in a series, later paragraphs should introduce
  the series goal, even if the first change is narrow groundwork.

### Second paragraph

- Describe the real underlying problem from the right perspective.
- Use maintainer perspective for internal concerns such as missing
  infrastructure, coverage, or build support.
- Use user perspective for external concerns such as unclear workflows,
  missing discoverability, or absent functionality.
- Focus on missing capabilities, not symptoms tied only to a file.
- Prefer concrete limitations over vague judgments.

### Third paragraph

- Describe exactly how this commit addresses one part of the problem.
- Be precise about scope.
- If the commit is an early step toward a larger goal, say so directly.
- When a commit series is building toward one goal, make that goal explicit
  and explain how the current commit advances the narrative.
- For the final commit in a series, explain how it completes or reaches the
  goal when that is true.
- Use phrasing such as:
  - `This commit addresses that by ...`
  - `This commit begins adding support for ... by ...`
  - `This commit continues that work by ...`
  - `This commit completes that series by ...`

### Fourth paragraph

- Use it for every commit except the final one in a multi-commit series.
- Use future tense since the work has not happened yet.
- Be specific about what comes next rather than vague.
- The final commit should conclude the series goal introduced by the opening
  commit instead of pointing toward more work.

## Required principles

- Tell a story. Related commits should read as connected steps, not isolated
  patches.
- Separate independent series. A dirty worktree can contain multiple unrelated
  commit series. Split them into separate series with separate opening and
  concluding commits instead of forcing one message thread across all unstaged
  changes.
- Introduce the series in its first commit. When a commit opens a multi-commit
  series, its message should name the larger goal and explain why the series
  exists, even if the first change is narrow groundwork.
- Conclude the series in its final commit. The final commit should make clear
  that the series has reached its intended goal instead of only describing the
  last small change.
- Name the eventual feature goal in early groundwork commits when that explains
  why the work exists.
- Describe problems at the product level, not only at the file level.
- Be humble and specific. Avoid bragging language and vague praise.
- Only use the word `this` when referring to the commit itself.
- Do not add `Co-Authored-By` lines for AI assistance.
- Wrap body paragraphs at 75 characters.

## Language to avoid

Do not use these words in commit messages:

- `comprehensive`
- `crucial`
- `robust`
- `powerful`
- `seamless`
- `intuitive`
- `easy`
- `simple`
- `clean`
- `elegant`
- `flexible`
- `scalable`
- `efficient`
- `efficiently`
- `improved`
- `improves`
- `better`
- `best`
- `simply`
- `just`
- `obviously`
- `clearly`

## Quality checks

Before committing, verify:

- If the summary line contains `and`, `also`, or describes two actions, the
  commit combines concerns. Do not commit it.
- The staged diff is atomic and internally coherent.
- Unrelated hunks are skipped for later commits.
- If the worktree contains multiple independent series, they are split into
  separate series.
- The summary uses a fitting prefix and stays under 68 characters.
- The first paragraph describes what the project currently has or provides,
  not what is missing, broken, or being changed.
- The second paragraph explains the broader problem from the right
  perspective.
- The third paragraph opens with `This commit` and describes how it addresses
  the problem.
- The message reflects a larger series narrative when the work is split across
  multiple commits.
- If this is the first commit in a series, the message introduces the whole
  series goal rather than only the first change.
- If this is the final commit in a series, the message concludes the series
  goal rather than reading like another incremental step.
- If the commit is not the last in the series, the fourth paragraph names what
  subsequent commits will do.
- Body paragraphs wrap at 75 characters.

## Completion

After creating the final commit, report back with:

- how many commits were created
- the subject line of each commit in order

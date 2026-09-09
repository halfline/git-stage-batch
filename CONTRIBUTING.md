# Contributing to git-stage-batch

Thank you for your interest in contributing!

## Development Setup

This project uses [uv](https://docs.astral.sh/uv/) for development workflow and [Meson](https://mesonbuild.com/) as the build backend.

**Requirements:**
- Python 3.10 through 3.14
- Git 2.39 or newer
- uv (for development)
- meson and ninja-build (install via your system package manager)
- gettext (for building and checking translation catalogs)

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install build tools (example for Fedora or Red Hat Enterprise Linux)
sudo dnf install gettext meson ninja-build

# Clone the repository
git clone https://github.com/halfline/git-stage-batch.git
cd git-stage-batch

# Install dependencies and build
uv sync

# Run tests
uv run pytest -n auto

# Run lint and strict type checks
uv run ruff check src tests scripts
uv run python scripts/check_translations.py
uv run python scripts/check_dead_code.py
uv run python scripts/check_type_hygiene.py
uv run mypy
```

Use the xdist form (`-n auto`) for full-suite runs. The suite is large enough
that serial `uv run pytest` is mainly useful for focused debugging or when
reproducing ordering-sensitive failures.

The type checker targets the oldest supported Python version and checks the
entire `git_stage_batch` package in strict mode. Keep persisted mappings and
other structured containers concrete rather than falling back to bare
`dict`, `list`, or `tuple` annotations. The type-hygiene check also prevents
explicit `Any` from spreading beyond the small set of reviewed serialization,
reflection, and third-party API boundaries.

The dead-code check analyzes the production package without treating tests as
callers. It rejects stale exceptions when an indirectly used schema field or
protocol method no longer needs one.

## Find the Code for a Change

Read [the codebase guide](ARCHITECTURE.md) before changing source. It follows
real commands from argument parsing through command behavior, persisted state,
Git updates, and output. It also lists the files that normally change when a
command or option is added or removed.

Do not begin with `src/git_stage_batch/batch/` for ordinary command or session
work. That package is documented separately because its saved-change storage
and merge rules are much larger than the introductory path. Read
[the batch internals guide](BATCHES.md) when changing a command that uses
`--to` or `--from`, saved ownership, batch display, batch merge, source refresh,
or the temporary batch merge used by `include --line`.

## Commit Message Guidelines

We follow strict commit message conventions to maintain a clear and understandable project history.

### Key Principles

- **Write for drive-by readers who are new to the codebase.** For a series, expect them to read the commits together in order, not only as isolated messages.
- **You are the maintainer; write to a casual reader.** The commit message is you explaining the change to someone unfamiliar with the codebase. Never refer to maintainers in the third person.
- **Tell the series' story in the commit history.** The series is the larger unit of work. Explain its goal, each commit's contribution, and the useful connections between steps so the history remains understandable on its own.
- **Separate independent series.** A dirty worktree can contain multiple unrelated commit series. Keep their explanations separate instead of forcing one narrative across all unstaged changes.
- **Explain groundwork where it matters.** Name the later capability that motivates a preparatory patch, regardless of the patch's position in the series.
- **Let the opening commit speak for the series.** Introduce the overall goal and motivation as well as the opening patch, while distinguishing what that patch actually accomplishes.
- **Close the story in the final commit.** Explain the outcome achieved by the series without overstating the final patch's individual scope or repeating an itinerary.
- **Use the tense that reflects the state of the project just before the commit is applied.** When discussing the old behavior, treat it as the selected behavior. When discussing the changes, treat them as new behavior.
- **Describe problems at the product level, not just the file level.** Focus on what users experience or what you find problematic as the maintainer, not only what is missing in a specific file or function.
- **Focus on missing capabilities, not symptoms.** Documentation gaps, code organization, and naming issues are often symptoms. Identify the underlying limitation or missing behavior that motivates the change.
- **Do not describe secondary effects as the primary problem.** Code organization, maintainability, or cleanliness are rarely the main reason for a change.
- **Be precise about scope.** If a change only improves one aspect of a problem, do not imply it fully solves it.
- **If the commit is a step toward a larger feature, say so explicitly.** Describe the end goal briefly, then explain how this commit moves toward it.
- **Name the feature goal in early groundwork commits.** If a commit mainly exists to enable a later user-facing feature, say what that feature is and why it matters instead of presenting the commit as isolated infrastructure work.
- **Prefer concrete limitations over vague judgments.** Avoid words like "cumbersome", "better", or "improved" without explaining why.
- **Do not use `Co-Authored-By` for contributions produced by artificial intelligence.** Only use it for human co-authors.
- **Only use the word `this` when referring to the commit itself.** Use `that` or similar for other contexts.
- **Wrap body paragraphs at 75 characters.**
- **Be humble and forward thinking.** Avoid words like "comprehensive" or "crucial", and avoid a tone that could sound like bragging or seem short-sighted.
- **Do not invent concise self-describing labels for internal ideas and use them casually,** expecting the reader to implicitly know what they should mean. Explain things in a way that reduces cognitive load on the reader.
- **Build context across the series.** Give shared background where it first matters. Later commits may rely on that explanation; keep useful repetition, but make it a shorter reminder rather than repeating the detail. Add new detail where it becomes relevant. A standalone commit still needs its own context.
- **Define codebase-specific or ambiguous terms when first introduced in the series.** Use a concise role reminder later when it helps, rather than repeating the full definition in every message.
- **Prefer a complete plain-language sentence over compressed shorthand.** Spell out the behavior hidden by noun piles, abstract verbs, or compounds such as `X-backed` and `X-aware` when their meaning is not obvious.

### Format

Use three body paragraphs for the selected state, problem, and solution.
Add a distinct fourth paragraph only when a series connection earns its
place, as described below. Keep each explanation proportionate to the patch.

#### First Line (Summary)

```
prefix: Concise summary of the change
```

- Use a short, lowercase prefix (`project:`, `cli:`, `patch:`, `editor:`, `state:`, etc.)
- Capitalize the first word of the summary after the colon
- Keep the entire line under 72 characters
- If unsure which prefix to use, run `git log --pretty=oneline FILE` and see what prefixes were used previously

#### First Paragraph

Establish the program's selected state at this point in history. If recent
work established that state, briefly recount the change in past tense and
loosely when it happened.

Describe the status quo in present tense from the viewpoint of the code
after the previous commit. Use a light cue such as `Right now, ...`,
`Currently, ...`, or `As things stand, ...` when it helps distinguish
time-dependent behavior from the change. The third paragraph's `This
commit ...` supplies the transition.

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

Summarize what capabilities, interfaces, or documentation exist in the
project immediately before this commit is applied. This is the program's
state, not the user's situation. Focus on what the program has or provides,
not on what users must do or cannot do.

If this commit is part of a series, describe the relevant state after the
previous commit. Do not claim later work already exists, but do not recap
unrelated earlier work either. A French translation does not need a list of
languages added by preceding commits.

If the patch mainly exists to enable a later feature, explain that connection
where it helps establish the problem or design. Do not describe it as generic
cleanup when a specific capability motivates the work.

Do not describe the diff, the change itself, or future goals.

#### Second Paragraph

Explain the underlying problem from the appropriate perspective.

**Choose the perspective based on who experiences the problem:**
- Use **first-person maintainer perspective** for internal concerns (missing infrastructure, lack of test coverage, missing translations, build system gaps). You are the maintainer — frame as "The program lacks X" or "The project does not provide Y", never as "Maintainers cannot X."
- Use **user perspective** for external concerns (confusing interfaces, missing documentation, poor workflows). Frame as "Users cannot X" or "Users must Y."

Describe what is non-obvious, hard to discover, confusing, missing, or limited
about the selected state. Focus on the broader problem and future goals, not just the
specific file being edited.

Prefer the broadest accurate framing of the problem.

Useful tests:
- Would this problem still exist even if the specific file being edited were perfect?
- Is this something users would notice, or only you as the maintainer?

For opening commits in a feature series, prefer framing the problem
around the missing user-facing capability instead of the missing internal
helper. For example, "Users cannot replace selected lines with different
text during include or discard workflows" is usually stronger than "The
project does not provide generic helpers for transformed selections."

#### Third Paragraph

Describe how the commit addresses one part of that problem.

Be precise about scope. If the commit only addresses one path (such as the man
page, command help, or internal structure), say so clearly rather than implying
the entire problem is solved.

If the commit introduces infrastructure or an early step toward a larger
feature, describe it as such.

Describe the capability or intermediate state established by the patch.
Do not force "begins", "continues", or "completes" wording based on position.

Start this paragraph with `This commit`.

Use natural prose such as:
- `This commit addresses that by ...`
- `This commit begins adding support for ... by ...`
- `This commit lays groundwork for ... by ...`

#### Fourth Paragraph

Use a distinct fourth paragraph when it helps explain the current commit's
place in the series. The series is the larger story, not just a collection of
isolated patches, and its goal, rationale, and connections belong in the
commit history itself.

A useful segue explains how the current step advances the goal, why a design
choice enables later work, what a dependent patch needs, or why some scope is
deliberately left unfinished. Ask whether removing the paragraph would make
that progression harder to understand. Merely sharing a topic or appearing
next in the series is not enough.

For example, a full-damage representation change can explain why a later
shadow-copy patch can use ordinary region operations. An unused-argument
cleanup should not merely announce that the next commit fixes failed GPU
copies; it needs a meaningful connection to that work.

Keep a useful segue as a fourth paragraph, separate from the current state,
problem, and solution. Do not fold it into those paragraphs or repeat their
explanation. Omit the fourth paragraph when no useful connection remains,
rather than manufacturing a segue to satisfy a template.

The opening commit speaks for the series as well as its own patch: introduce
the overall goal and motivation, then distinguish the contribution made
here. The final commit closes that story by explaining the achieved outcome,
without claiming more than the series actually establishes. A fourth
paragraph can carry that broader framing or conclusion when it adds useful
context instead of merely repeating the solution.

Verify references against actual patches. Describe work in later commits in
future tense and do not claim it is already implemented. Make references to
those commits explicit: `in a later commit`, `later commits will ...`, or
`the final commit will ...`, not bare `later`. For example, write `the
tracker introduced in a later commit`, not `the tracker introduced later`.
When a retained segue refers only to the upcoming final commit, use the
singular. Preserve useful connections; remove unrelated recaps, next-item
announcements, and repetitive boilerplate. Varying the wording does not make
an irrelevant segue useful.

### Checklist

Before finalizing a commit message, check:

- Does the summary use a fitting prefix and stay under 68 characters?
- Does the first paragraph describe the program's selected state, not the patch?
- Is the status quo clear, with temporal cues only where they resolve an
  ambiguity rather than make lasting background sound temporary?
- Does any `already` claim identify an existing capability the patch builds
  on, rather than assume later work?
- Does the first paragraph describe the program's state (what it has), not the user's situation (what they must do)?
- Does the selected state match the previous commit without recapping
  unrelated earlier work?
- If the worktree contains multiple independent series, are they split into separate series?
- Does each cross-commit reference help explain the current patch's contribution or a meaningful connection in the series?
- Does the opening commit explain the whole series' goal and motivation as well as its own contribution?
- Does the final commit close that story with the outcome actually achieved?
- Can the series be understood from the commit history alone?
- Are unrelated roadmaps, recaps, and next-item announcements omitted?
- Does the second paragraph use the appropriate perspective (first-person maintainer for internal concerns, user for external concerns)?
- Does the second paragraph describe the real problem from either the user's or your own maintainer perspective?
- Is the problem broader than just the file being edited?
- Does the message focus on a missing capability rather than a symptom?
- If the patch is groundwork, does it explain the later capability that motivates the design?
- Does the third paragraph open with `This commit` and clearly state what this commit does without overstating its impact?
- If this is an incremental step, does it clearly say so?
- Would removing any series-context paragraph make the series' progression harder to understand, rather than just lose an announcement?
- Is retained series context accurate, brief, and not repeated elsewhere in the message?
- Is any useful segue a distinct fourth paragraph rather than folded into the first three?
- Is each explanation proportionate to the patch?
- Can a newcomer understand the state, limitation, and change without decoding coined shorthand?
- Are unfamiliar terms introduced where they first matter, with concise reminders where later messages need them?
- Does repeated context become more concise while retaining what helps explain the current patch?
- Do body paragraphs wrap at 75 characters?

### Example: Single Commit

```
cli: Add --verbose flag for detailed output

Right now, the command-line interface provides minimal feedback during
operation. It only shows the selected hunk, without any indication of
progress or internal state.

Users working with large changesets cannot easily determine how much work
remains or what has already been processed, making it difficult to gauge
progress and reason about unexpected behavior.

This commit addresses that lack of visibility by adding a --verbose flag that
displays additional information including the number of hunks processed, total
hunks remaining, and the selected hunk's position in the sequence. The flag is
optional and preserves the existing terse output when not specified.
```

### Example: Commit Series

The opening commit explains the series goal and its first step. Its fourth
paragraph connects the representation change to the consumer that will use
it. The final commit then closes the story with the resulting behavior.

**Commit 1:**

```text
renderer: Represent full redraws with explicit damage

Right now, full redraws use an empty damage region to mean "copy
everything". Rendering and shadow-buffer copies each interpret that
special value.

Ordinary region operations instead treat an empty region as containing no
pixels. The series makes full redraws use ordinary regions throughout
rendering and copying so both paths agree about which pixels to update.

This commit represents full redraws with the framebuffer rectangle,
allowing region operations to describe the complete update directly.

That representation will let the shadow-copy path remove its empty-region
special case and use the damage supplied by the renderer.
```

**Final commit:**

```text
renderer: Use supplied shadow buffer damage

The preceding commit changed full redraws to supply a region covering the
framebuffer.

Shadow buffer copies still translate an empty region into a full copy,
even though the supplied region already describes all pixels to copy.

This commit passes the supplied region directly to the copy operation,
removing the special case.

Rendering and shadow-buffer copying now describe full updates with the
same region semantics. Neither path needs an empty region to mean the
opposite of its ordinary meaning.
```

By contrast, an unused-argument cleanup does not need "The next commit will
fix failed GPU copies." That sentence announces another patch without
explaining the cleanup's role, even if both patches touch the same function.

### Anti-Patterns to Avoid

❌ **Don't write in past tense about the old state:**
```
The code used to only show minimal output...
```

✅ **Do write in present tense about the selected state:**
```
Right now, the code provides minimal output...
```

❌ **Don't describe the change in the first paragraph:**
```
This commit adds verbose output to the command-line interface...
```

✅ **Do frame what exists before the commit:**
```
As things stand, the command-line interface provides minimal feedback
during operation...
```

❌ **Don't confuse a symptom with the real problem:**
```
Users reading the man page cannot discover that interactive mode exists.
```

✅ **Do describe the broader problem first:**
```
Interactive mode is not obvious for a tool that otherwise presents itself as a
command-line interface.
```

✅ **Then describe the narrower gap if relevant:**
```
The man page does not currently help users discover or understand that mode.
```

❌ **Don't frame internal structure as the problem:**
```
Without an organized directory, the code may become harder to maintain.
```

✅ **Do describe the missing capability:**
```
The project does not yet provide an interactive terminal interface.
```

❌ **Don't use vague value judgments:**
```
The command-line interface is cumbersome to use.
```

✅ **Do describe concrete limitations:**
```
The command-line interface requires repeated command invocation and does not
provide a continuous hunk-by-hunk workflow.
```

❌ **Don't overstate the impact of the commit:**
```
This commit solves discoverability of interactive mode.
```

✅ **Do be precise about scope:**
```
This commit addresses that by documenting the workflow in the man page.
```

❌ **Don't describe the program's state inaccurately in a series:**
```
i18n: Add French translation (fr)

The application outputs all user-facing text in English.

Users who speak other languages must work in English...
```

✅ **Do describe the relevant state after previous commits:**
```
i18n: Add French translation (fr)

Currently, the program uses fallback English messages for the French
locale.

Without French translations, French-speaking users cannot use the
program in their native language...
```

❌ **Don't describe user situations in the first paragraph:**
```
i18n: Add French translation (fr)

Users must work in English regardless of their preference.
```

✅ **Do describe the program's state:**
```
i18n: Add French translation (fr)

Currently, the program uses fallback English messages for the French
locale.
```

## Making Changes

1. **Keep commits atomic.** Each commit should represent one logical change.
2. **Use the `git-stage-batch` tool itself** to help stage micro-commits from larger working directory changes.
3. **Follow existing code style.** The project uses standard Python conventions.

### Commit Series Ordering

Order multi-commit series as repeated implementation steps and their related
follow-up commits:

```text
implementation -> tests (-> man page -> bash completion -> website)
implementation -> tests (-> man page -> bash completion -> website)
...
```

Do not put several implementation commits first and then collect the test,
documentation, completion, or website commits at the end. Each test,
documentation, completion, or website commit should sit immediately after the
smallest implementation commit it validates or exposes.

If one implementation change needs more than one follow-up commit because of
repository rules, keep those follow-ups together before moving to the next
implementation. Use the order `tests`, then man page, then bash completion,
then website unless a specific dependency requires otherwise.

When shared groundwork is needed, commit the groundwork first. Then repeat the
same grouped pattern for each command, workflow, or implementation that adopts
that groundwork.

## Questions?

Feel free to open an issue for discussion before starting major work.

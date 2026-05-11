# Contributing to git-stage-batch

Thank you for your interest in contributing!

## Development Setup

This project uses [uv](https://docs.astral.sh/uv/) for development workflow and [Meson](https://mesonbuild.com/) as the build backend.

**Requirements:**
- Python 3.10+
- uv (for development)
- meson and ninja-build (install via your system package manager)

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install meson and ninja (example for Fedora/RHEL)
sudo dnf install meson ninja-build

# Clone the repository
git clone https://github.com/halfline/git-stage-batch.git
cd git-stage-batch

# Install dependencies and build
uv sync

# Run tests
uv run pytest -n auto
```

Use the xdist form (`-n auto`) for full-suite runs. The suite is large enough
that serial `uv run pytest` is mainly useful for focused debugging or when
reproducing ordering-sensitive failures.

## Commit Message Guidelines

We follow strict commit message conventions to maintain a clear and understandable project history.

### Key Principles

- **Write for drive-by reviewers with limited context.** Assume the reader does not know the project well.
- **Tell a story.** The events in history are connected, and that connection should be considered when crafting messages. Do not treat each commit as an isolated writing exercise. If a series of commits contribute collectively to a goal, each commit message should describe how it helps achieve that goal. Early commits can foreshadow later commits if it helps tell the story.
- **Use the tense that reflects the state of the project just before the commit is applied.** When discussing the old behavior, treat it as the selected behavior. When discussing the changes, treat them as new behavior.
- **Describe problems at the product level, not just the file level.** Focus on what users or maintainers experience, not only what is missing in a specific file or function.
- **Focus on missing capabilities, not symptoms.** Documentation gaps, code organization, and naming issues are often symptoms. Identify the underlying limitation or missing behavior that motivates the change.
- **Do not describe secondary effects as the primary problem.** Code organization, maintainability, or cleanliness are rarely the main reason for a change.
- **Be precise about scope.** If a change only improves one aspect of a problem, do not imply it fully solves it.
- **If the commit is a step toward a larger feature, say so explicitly.** Describe the end goal briefly, then explain how this commit moves toward it.
- **Name the feature goal in early groundwork commits.** If a commit mainly exists to enable a later user-facing feature, say what that feature is and why it matters instead of presenting the commit as isolated infrastructure work.
- **Prefer concrete limitations over vague judgments.** Avoid words like "cumbersome", "better", or "improved" without explaining why.
- **Do not use `Co-Authored-By` for contributions produced from AI.** Only use it for human co-authors.
- **Only use the word `this` when referring to the commit itself.** Use `that` or similar for other contexts.
- **Be humble and forward thinking.** Avoid words like "comprehensive" or "crucial", and avoid a tone that could sound like bragging or seem short-sighted.
### Format

Commit messages should follow this three-paragraph structure:

#### First Line (Summary)

```
prefix: Concise summary of the change
```

- Use a short, lowercase prefix (`project:`, `cli:`, `patch:`, `editor:`, `state:`, etc.)
- Capitalize the first word of the summary after the colon
- Keep the entire line under 72 characters
- If unsure which prefix to use, run `git log --pretty=oneline FILE` and see what prefixes were used previously

#### First Paragraph

Describe the program's selected state at this point in history.

Summarize what capabilities, interfaces, or documentation exist in the
project immediately before this commit is applied. This is the program's
state, not the user's situation. Focus on what the program has or provides,
not on what users must do or cannot do.

If this commit is part of a series, the first paragraph must reflect the
cumulative state after all previous commits in the series. For example, if
earlier commits added Spanish and French translations, this paragraph should
state "The program has Spanish and French translations" not "The program only
has English messages."

If this is the opening groundwork commit in a feature series, the later
paragraphs should name the feature goal directly. Do not describe the commit
as generic cleanup or infrastructure when it is really the first step toward a
specific user-facing capability.

Do not describe the diff, the change itself, or future goals.

#### Second Paragraph

Explain the underlying problem from the appropriate perspective.

**Choose the perspective based on who experiences the problem:**
- Use **maintainer perspective** for internal concerns (missing infrastructure, lack of test coverage, missing translations, build system gaps). Frame as "The program lacks X" or "The project does not provide Y."
- Use **user perspective** for external concerns (confusing interfaces, missing documentation, poor workflows). Frame as "Users cannot X" or "Users must Y."

Describe what is non-obvious, hard to discover, confusing, missing, or limited
about the selected state. Focus on the broader problem and future goals, not just the
specific file being edited.

Prefer the broadest accurate framing of the problem.

Useful tests:
- Would this problem still exist even if the specific file being edited were perfect?
- Is this something users would notice, or only maintainers?

For opening groundwork commits in a feature series, prefer framing the problem
around the missing user-facing capability instead of the missing internal
helper. For example, "Users cannot replace selected lines with different
text during include or discard workflows" is usually stronger than "The
project does not provide generic helpers for transformed selections."

#### Third Paragraph

Describe how the commit addresses one part of that problem.

Be precise about scope. If the commit only improves one path (such as the man
page, CLI help, or internal structure), say so clearly rather than implying
the entire problem is solved.

If the commit introduces infrastructure or an early step toward a larger
feature, describe it as such.

Use natural prose such as:
- `This commit addresses that by ...`
- `This commit improves that by ...`
- `This commit begins adding support for ... by ...`
- `This commit lays groundwork for ... by ...`

#### Fourth Paragraph (optional)

If there will be changes coming up in the near future, say so:

- `Subsequent commits will provide ...`
- `In the future, <behavior> will change to ...`

### Checklist

Before finalizing a commit message, check:

- Does the first paragraph describe the program's selected state, not the patch?
- Does the first paragraph describe the program's state (what it has), not the user's situation (what they must do)?
- If this is part of a series, does the first paragraph accurately reflect the cumulative state after all previous commits?
- Does the second paragraph use the appropriate perspective (maintainer for internal concerns, user for external concerns)?
- Does the second paragraph describe the real user-visible or maintainer-visible problem?
- Is the problem broader than just the file being edited?
- Does the message focus on a missing capability rather than a symptom?
- If this is the first groundwork commit in a feature series, does the message name the eventual user-facing feature rather than only the internal machinery?
- Does the third paragraph clearly state what this commit does without overstating its impact?
- If this is part of a series, does it show progression (e.g., "begins", "continues", "completes")?
- If this is an incremental step, does it clearly say so?

### Example: Single Commit

```
cli: Add --verbose flag for detailed output

The CLI currently provides minimal feedback during operation, only showing
the selected hunk without any indication of progress or internal state.

Users working with large changesets cannot easily determine how much work
remains or what has already been processed, making it difficult to gauge
progress and reason about unexpected behavior.

This commit addresses that lack of visibility by adding a --verbose flag that
displays additional information including the number of hunks processed, total
hunks remaining, and the selected hunk's position in the sequence. The flag is
optional and preserves the existing terse output when not specified.
```

### Example: Commit Series

Notice how the first paragraph evolves to reflect the cumulative state, and how each commit shows progression toward the stated goal:

**Commit 1:**
```
i18n: Add Spanish translation (es)

The program has gettext infrastructure in place but only contains
English messages in the POT template.

Without translations, the program cannot serve non-English speaking
users. Spanish is one of the most widely spoken languages globally.

This commit begins expanding language support by adding a complete
Spanish translation file (po/es.po) with 219 translated messages
covering all commands, error messages, and interactive prompts.

Subsequent commits will add translations for additional languages.
```

**Commit 2:**
```
i18n: Add French translation (fr)

The program has Spanish translation but lacks translations for other
major languages.

Without French translations, French-speaking users cannot use the
program in their native language.

This commit continues expanding language support by adding a complete
French translation file (po/fr.po) with 216 translated messages.
```

**Commit 3:**
```
i18n: Add German translation (de)

The program has Spanish and French translations but lacks German.

Without German translations, German-speaking users cannot use the
program in their native language.

This commit continues expanding language support by adding a complete
German translation file (po/de.po) with 216 translated messages.
```

**Final commit:**
```
i18n: Add Arabic translation (ar)

The program has translations for Western European languages, East
Asian languages, and Eastern European languages but lacks support for
Arabic-speaking users.

Without Arabic translations, Arabic-speaking users cannot use the
program in their native language.

This commit completes the initial set of language support by adding a
complete Arabic translation file (po/ar.po) with 216 translated
messages.

The program now supports 14 languages covering major linguistic
regions globally.
```

### Anti-Patterns to Avoid

❌ **Don't write in past tense about the old state:**
```
The code used to only show minimal output...
```

✅ **Do write in present tense about the selected state:**
```
The code currently provides minimal output...
```

❌ **Don't describe the change in the first paragraph:**
```
This commit adds verbose output to the CLI...
```

✅ **Do describe what exists today:**
```
The CLI currently provides minimal feedback during operation...
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
The project does not yet provide a TUI for interactive use.
```

❌ **Don't use vague value judgments:**
```
The CLI is cumbersome to use.
```

✅ **Do describe concrete limitations:**
```
The CLI requires repeated command invocation and does not provide a
continuous hunk-by-hunk workflow.
```

❌ **Don't overstate the impact of the commit:**
```
This commit solves discoverability of interactive mode.
```

✅ **Do be precise about scope:**
```
This commit improves discoverability through the man page by ...
```

❌ **Don't describe the program's state inaccurately in a series:**
```
i18n: Add French translation (fr)

The application outputs all user-facing text in English.

Users who speak other languages must work in English...
```

✅ **Do reflect the cumulative state after previous commits:**
```
i18n: Add French translation (fr)

The program has Spanish translation but lacks translations for other
major languages.

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

The program has Spanish translation but lacks French.
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

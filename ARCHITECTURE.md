# Codebase guide for contributors

This guide explains where the program starts, how one user action moves through
the source tree, and which files normally change when behavior is added or
removed. It describes the current source. The architecture tests remain the
authority when a statement here and the code disagree.

Paths in the command walkthroughs are places to start reading the current
implementation. They are not a required module inventory. Adding a small helper
or combining helpers does not change the organization described here when the
same directory still owns the same work.

Start with this guide for ordinary command, session, display, and interactive
work. Do not start in `src/git_stage_batch/batch/`. That directory implements
named batches and the merge rules used by a small number of line operations.
Read [Batch internals](BATCHES.md) only when a change reaches one of those
behaviors.

## Terms used in this guide

The following terms have their Git meanings:

- **Working tree**: the files checked out on disk.
- **Index**: the Git staging area that will supply the next commit.
- **Current commit**: the commit named `HEAD` by Git.
- **Hunk**: one contiguous section of a file diff.

The following terms name state maintained by this program:

- **Session**: the persisted work started by `git-stage-batch start` and ended
  by `stop` or `abort`.
- **Selected change**: the hunk, file, rename, deletion, binary file, submodule
  pointer, or file mode change saved for the next action.
- **File review**: a saved view of one file, including which pages and line
  identifiers were shown. Later actions use that record to reject stale or
  unseen selections.

## Read these files first

The shortest reading path for an ordinary command is:

1. [`src/git_stage_batch/cli/main.py`](src/git_stage_batch/cli/main.py) parses
   arguments, changes directory when `-C` is present, acquires the session lock,
   and handles errors.
2. [`src/git_stage_batch/cli/subcommand_registry.py`](src/git_stage_batch/cli/subcommand_registry.py)
   lists every command that the parser registers.
3. [`src/git_stage_batch/runtime.py`](src/git_stage_batch/runtime.py) chooses
   interactive or non-interactive execution.
4. [`src/git_stage_batch/cli/execution.py`](src/git_stage_batch/cli/execution.py)
   calls the function stored on the parsed arguments.
5. [`src/git_stage_batch/commands/start.py`](src/git_stage_batch/commands/start.py),
   [`show.py`](src/git_stage_batch/commands/show.py), and
   [`include.py`](src/git_stage_batch/commands/include.py) show the main session
   workflow.
6. [`src/git_stage_batch/data/hunk_tracking.py`](src/git_stage_batch/data/hunk_tracking.py)
   finds and saves the next change.
7. [`src/git_stage_batch/core/diff_parser.py`](src/git_stage_batch/core/diff_parser.py)
   converts Git diff output into Python values.
8. [`src/git_stage_batch/commands/selection/selected_change_staging.py`](src/git_stage_batch/commands/selection/selected_change_staging.py)
   shows how a selected change reaches the index and how the next change is
   selected.

That path is enough to understand whole-hunk `start`, `show`, `include`, `skip`,
and `discard`. It does not require reading the named-batch implementation.

## What each source directory contains

| Path | What belongs there | Change it when |
| --- | --- | --- |
| `cli/` | Parser construction, argument normalization, help routing, completion, and choosing which command function matches the supplied options | A command, option, alias, or argument combination changes |
| `commands/` | Preconditions and the complete sequence for one user-requested operation | The effect of a command changes |
| `core/` | Diff values, diff parsing, line-selection parsing, hashing, replacement values, and byte-preserving buffers | The representation of a change changes without reading or writing session state |
| `data/` | Reading and writing session state, finding the next change, checking freshness, progress, undo and redo records, and file-review records | Behavior must survive another program invocation or must be derived from persisted session state |
| `staging/` | Building exact target file content and writing that content to the index | A selective text operation changes the content staged for one file |
| `output/` | Converting prepared values into terminal text | Wording, colors, rows, summaries, or machine-readable output changes |
| `tui/` | Prompts, key handling, menus, and calls into `commands/` for interactive mode | A behavior must be reachable or presented differently in interactive mode |
| `utils/` | Git command execution, Git object access, Git references, paths, file access, process streaming, and journals | Several callers need the same low-level Git, process, path, or file operation |
| `editor/` | The in-memory line editor, piece table, line endings, and line export | Byte-preserving line editing changes |
| `batch/` | Named-batch storage, ownership records, display reconstruction, matching, merge, discard reversal, and source refresh | A named-batch behavior changes, already-batched lines are filtered incorrectly, or live line inclusion reaches the temporary batch merge described below |

The package root also contains modules with narrow jobs:

- [`runtime.py`](src/git_stage_batch/runtime.py) chooses interactive or
  non-interactive execution.
- [`exceptions.py`](src/git_stage_batch/exceptions.py) defines errors and
  top-level command exit behavior.
- [`git_paths.py`](src/git_stage_batch/git_paths.py) encodes, decodes, and
  parses Git pathnames without losing raw bytes.
- [`i18n.py`](src/git_stage_batch/i18n.py) loads translated messages.

## How a command reaches its implementation

Every explicit non-interactive command follows this sequence:

1. The installed `git-stage-batch` program calls `main()` in
   [`cli/main.py`](src/git_stage_batch/cli/main.py).
2. `parse_command_line()` in
   [`cli/argument_parser.py`](src/git_stage_batch/cli/argument_parser.py)
   expands short actions, builds the parser, parses the arguments, and
   normalizes file arguments.
3. [`cli/root_parser.py`](src/git_stage_batch/cli/root_parser.py) creates the
   root parser and asks `add_cli_subcommands()` to register commands.
4. One parser registration function stores a callable in `args.func` with
   `set_defaults(func=...)`.
5. `dispatch_cli_mode()` in [`runtime.py`](src/git_stage_batch/runtime.py)
   sends non-interactive work to `execute_non_interactive_args()`.
6. [`cli/execution.py`](src/git_stage_batch/cli/execution.py) calls
   `args.func(args)`.
7. That callable invokes a function under `commands/`, either directly or
   through a dispatch module under `cli/` when several option combinations
   share one command name.

The parser chooses *which* operation the user requested. The function under
`commands/` owns *how* that operation behaves.

### Example: `status`

`git-stage-batch status` has a short path that shows the responsibility of each
directory:

1. `add_status_subcommand()` in
   [`cli/session_subcommands.py`](src/git_stage_batch/cli/session_subcommands.py)
   declares `status`, its options, and the call to `command_status()`.
2. `command_status()` in
   [`commands/status.py`](src/git_stage_batch/commands/status.py) checks whether
   a session is active and chooses human-readable, machine-readable, or shell
   prompt output.
3. `read_status_summary()` in
   [`data/status_summary.py`](src/git_stage_batch/data/status_summary.py) reads
   persisted state and returns one dictionary.
4. `print_status_summary()` in
   [`output/status.py`](src/git_stage_batch/output/status.py) prints the
   human-readable form. [`output/status_prompt.py`](src/git_stage_batch/output/status_prompt.py)
   renders the shell prompt form.

The parser does not read session files. The data module does not print. The
output module does not decide whether the command is allowed.

### Example: `include` for one selected hunk

`git-stage-batch include` shows a mutating command:

1. `add_include_subcommand()` in
   [`cli/selection_subcommands.py`](src/git_stage_batch/cli/selection_subcommands.py)
   declares the arguments and stores `dispatch_include_command()` as the
   callable.
2. [`cli/include_dispatch.py`](src/git_stage_batch/cli/include_dispatch.py)
   distinguishes whole-hunk, whole-file, selected-line, replacement, named-batch
   source, and named-batch destination forms.
3. With no scope option, it calls `command_include()` in
   [`commands/include.py`](src/git_stage_batch/commands/include.py).
4. `command_include()` checks repository and session requirements and rejects
   a stale or ambiguous selection.
5. `include_selected_change()` in
   [`commands/selection/selected_change_staging.py`](src/git_stage_batch/commands/selection/selected_change_staging.py)
   loads the selected change or asks `fetch_next_change()` to find one. It also
   opens an undo checkpoint.
6. The same module handles each concrete change type. A text hunk is applied to
   the index with `git_apply_to_index()`. Binary files, renames, file modes,
   deletions, and submodule pointers use their corresponding Git helpers.
7. [`data/progress.py`](src/git_stage_batch/data/progress.py) records the
   completed hunk.
8. `finish_selected_change_action()` in
   [`commands/selection/action_completion.py`](src/git_stage_batch/commands/selection/action_completion.py)
   selects and displays the next change when automatic advancement is enabled.

Whole-hunk inclusion does not use the named-batch merge implementation.

### Exception: `include --line`

Line inclusion has an additional safety path:

1. `command_include_line()` resolves the selected file and line identifiers.
2. `include_live_line_selection()` in
   [`commands/selection/include_line_action.py`](src/git_stage_batch/commands/selection/include_line_action.py)
   loads the current index, the session snapshot, and the working-tree
   snapshot.
3. [`commands/selection/include_line_selection.py`](src/git_stage_batch/commands/selection/include_line_selection.py)
   creates a temporary named batch, translates the selected lines into batch
   ownership, and asks the batch merge code to build the new index content.
4. The function verifies that applying the same temporary ownership to the
   working tree would leave the working tree unchanged.
5. [`staging/index_update.py`](src/git_stage_batch/staging/index_update.py)
   writes the accepted content to the index. The temporary batch is removed
   before the command returns.

Read [Batch internals](BATCHES.md) when changing steps 3 or 4. A change to
argument parsing, session checks, selected-line loading, or the final index
write can usually be understood without reading the rest of `batch/`.

## How interactive mode reaches the same commands

Interactive mode changes how the user chooses an action; it does not provide a
second implementation of that action.

1. `dispatch_cli_mode()` imports and calls `start_interactive_mode()` from
   [`tui/interactive.py`](src/git_stage_batch/tui/interactive.py).
2. [`tui/action_dispatch.py`](src/git_stage_batch/tui/action_dispatch.py) maps
   a key to one handler.
3. A handler for the selected action calls a function under `commands/`. For example,
   [`tui/hunk_actions.py`](src/git_stage_batch/tui/hunk_actions.py) calls
   `command_include()`, `command_skip()`, or `command_discard()`.

Add shared behavior under `commands/`, then call it from interactive mode. Do
not duplicate the operation in `tui/`.

## Where a change goes

Use the narrowest row that describes the change:

| Requested change | Start here | Other places that commonly change |
| --- | --- | --- |
| Add an option to an existing command | Its registration function under `cli/` | Its dispatch module, command function, command tests, manual page, website command reference, shell completion |
| Change what a command does | Its module under `commands/` | The command subpackage that owns the affected sequence, data reads or writes, output renderer, command and functional tests |
| Change how Git diff text is understood | `core/diff_parser.py` and the relevant value in `core/models.py` | Core tests, data discovery code, output code for a new value type |
| Add persisted session information | The module under `data/` that reads and writes that record, plus a path helper under `utils/paths.py` | Session cleanup, abort or undo handling when applicable, data tests, functional tests |
| Change human-readable text layout | The module under `output/` that prints that form | The command that prepares its input, output tests, translated source string |
| Add an interactive key or menu item | A handler under `tui/` that calls the command | `tui/action_dispatch.py`, prompt or help modules, a command function, interactive tests |
| Add a reusable Git operation | A specific `utils/git_*` module | Utility tests and the command or data caller |
| Change line content written to the index | `staging/content_buffers.py` or `staging/index_update.py` | The command that chooses the content, staging tests, command tests |
| Change named-batch persistence or merge behavior | The owning module under `batch/` | A batch-facing command, batch tests, command tests, [Batch internals](BATCHES.md) |

## Add a command users can run

A command is complete when every public way to discover and run it agrees.
Follow these steps:

1. Add the operation under `src/git_stage_batch/commands/`. Put supporting
   functions in an existing command subpackage when that subpackage already
   owns the same behavior.
2. Register the command in the matching file under `src/git_stage_batch/cli/`.
   Current groups include session commands, selected-change commands,
   file-blocking commands, fixup commands, asset commands, and named-batch
   commands.
3. Add that registration function to
   `cli/subcommand_registry.py`. A new option on an existing command does not
   need another registry entry.
4. Call the command function directly from the registration function when the
   argument mapping is simple. Add or extend a dispatch module under `cli/`
   when mutually exclusive forms such as `--file`, `--line`, `--from`, and
   `--to` choose different command functions.
5. Put state reads and writes in `data/`, and terminal rendering in `output/`,
   when the command needs them.
6. If interactive mode exposes the command, add a handler under `tui/` that
   calls the same command function. Update the visible prompt and help text.
7. Add or update `man/git-stage-batch-<command>.1.in`. For a new page, add it
   to `manpage_specs` in `meson.build`. Update `man/git-stage-batch.1.in` when
   the root manual page lists the command.
8. Update `completions/git-stage-batch`, `docs/commands.md`, and any workflow
   guide that teaches the affected behavior.
9. Wrap user-visible Python strings with the translation function imported
   from `i18n.py`. The build runs `scripts/generate_potfiles.py` to find source
   files containing translated strings.
10. Add focused tests in the directory matching the changed source, then add a
    functional test when correctness depends on several invocations or on the
    combined working tree, index, and session state.

## Add an option to an existing command

An option normally touches fewer places:

1. Declare it in the command's registration function under `cli/`.
2. Pass the parsed value through the existing callable or dispatch module.
3. Add the behavior to the command function or to the module that already owns
   that part of the command.
4. Update shell completion, the command's manual page, and `docs/commands.md`.
5. Test parser acceptance and rejection in `tests/cli/`.
6. Test the resulting behavior in `tests/commands/` or `tests/functional/`.
7. If interactive mode offers the same choice, update its prompt, handler, and
   tests explicitly. Do not assume a command-line option becomes interactive
   automatically.

## Remove a command or option

Remove the behavior from the outside inward so each remaining call has an
implementation until its caller is removed:

1. Remove interactive prompts, menu choices, key handlers, and interactive
   tests that call the behavior.
2. Remove the parser argument or command registration and its parser tests.
3. Remove the call from `cli/subcommand_registry.py` when deleting a command.
4. Remove the command function and then any support functions with no callers.
5. Remove persisted state only after accounting for repositories that may still
   contain it. If old state must remain readable, keep the reader or add an
   explicit migration.
6. Remove the command or option from shell completion, manual pages, website
   documentation, examples, and assistant assets that name it.
7. Remove a deleted manual page from `manpage_specs` in `meson.build`.
8. Search for the command spelling, aliases, option spelling, command function,
   state filenames, and translated messages before considering the removal
   complete.
9. Run the architecture tests after deleting support modules. They catch stale
   imports and accidental movement of behavior into the wrong package.

## Change persisted session state

Each kind of persisted session record has one data module that reads and writes
it. Use this sequence when adding or changing a record:

1. Add the path in [`utils/paths.py`](src/git_stage_batch/utils/paths.py).
2. Add serialization and validation in the module under `data/` that owns the
   record.
3. Decide when the record is created, refreshed, and cleared. Selected-change
   files are enumerated by
   [`data/selected_change/store.py`](src/git_stage_batch/data/selected_change/store.py)
   and cleared through
   [`data/selected_change/lifecycle.py`](src/git_stage_batch/data/selected_change/lifecycle.py).
4. Decide whether undo, redo, abort, `again`, or `stop` must restore or delete
   the record. These operations do not cover new state automatically.
5. Reject malformed or stale records at the read boundary. Do not let callers
   guess whether a partially valid record is safe.
6. Test creation, loading, stale input, cleanup, and the relevant recovery
   operation under `tests/data/` and `tests/functional/`.

## Keep prepared data separate from rendering

The `status` path demonstrates the expected division:

- `data/status_summary.py` prepares values.
- `output/status.py` prints those values.
- `commands/status.py` chooses the form and controls errors.

Use the same division for new output. A data module may return strings that are
stored data, but it should not print. An output module may format and print, but
it should not mutate the index, working tree, or session.

## Tests that match each source area

| Source changed | First test directory | Add a functional test when |
| --- | --- | --- |
| `cli/` | `tests/cli/` | Argument routing must be proven with repository state |
| `commands/` | `tests/commands/` | The operation spans several commands or recovery steps |
| `core/` | `tests/core/` | The parsed value must also be applied to a real repository |
| `data/` | `tests/data/` | State must survive several program invocations |
| `staging/` | `tests/staging/` | The index and working tree must be compared after a full command |
| `output/` | `tests/output/` | Usually not needed unless command context changes the rendering |
| `tui/` | `tests/tui/` | The same behavior must be proven across interactive and non-interactive use |
| `utils/` | `tests/utils/` | A real repository is required to prove the helper contract |
| `editor/` | `tests/editor/` | Edited content must pass through a complete command |
| `batch/` | `tests/batch/` | A named batch must survive or interact with a session workflow |

Use `tests/functional/` for multi-step repository behavior, not as a substitute
for a focused test of the owning module.

## Import rules checked by the test suite

[`tests/architecture/test_import_boundaries.py`](tests/architecture/test_import_boundaries.py)
checks the current package boundaries. The broad rules are:

- top-level packages must not form an import cycle
- modules inside `batch/` must not form an import cycle
- `batch/` must not import workflow storage from `data/`
- `commands/` must not import `tui/`
- `batch/`, `core/`, `data/`, and `utils/` must raise errors without importing
  the command exit helper
- modules named by an architecture test must be imported directly rather than
  hidden behind package re-exports

Many tests in that file protect a specific ownership decision. Read the failing
test before moving a function between modules. Its name and assertions state
the required location and allowed import direction.

Run the architecture checks with:

```console
uv run pytest -n auto tests/architecture/test_import_boundaries.py
```

## When `batch/` is relevant

Read [Batch internals](BATCHES.md) before changing any of the following:

- commands that use `--to` or `--from`
- `new`, `list`, `drop`, `annotate`, `validate`, `reset`, or `sift`
- storage below `refs/git-stage-batch/`
- translation between displayed lines and saved batch ownership
- hiding already-batched changes from the live diff
- merge or discard behavior for a saved batch
- source refresh after a file changes
- the temporary batch merge used by `include --line`

For ordinary parser work, whole-hunk actions, session lifecycle, progress,
output, and most interactive menus, stay in the directories described in this
guide and treat `batch/` as an implementation dependency reached through an
existing function.

## Validation before submitting a change

Run focused tests while editing. Before submitting a broad source change, run:

```console
uv run pytest -n auto
uv build
```

For documentation changes, build the website with strict link and navigation
checking:

```console
uv run mkdocs build --strict
```

The complete test suite is intentionally run in parallel. A serial run is most
useful for focused debugging.

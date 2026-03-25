# Interactive Mode

Interactive mode provides a menu-driven workflow for reviewing and staging hunks one at a time. It is similar to `git add -p` but with line-level control, batch integration, and flow routing.

## Starting Interactive Mode

```
❯ git-stage-batch -i
```

Or as a subcommand:

```
❯ git-stage-batch interactive
```

Interactive mode automatically starts a session, records the starting state for undo support, and begins presenting hunks.

---

## How It Works

Interactive mode presents hunks one at a time with a status bar and action menu:

```
════════════════════════════════════════════════════════════════
Source: working tree → Target: staging
Included: 0  Skipped: 0  Discarded: 0

auth.py :: @@ -10,5 +10,5 @@
[#1] - old_hash_function()
[#2] + new_hash_function()
      validate_user()

What do you want to do with this hunk?
  [i]nclude
  [s]kip
  [d]iscard
  [q]uit

Other scope: [l]ines, [f]ile | Flow: [<]from, [>]to | More: [a]gain, [b]atch, [x]fixup, [!]cmd, [?]help

Action:
```

The status bar shows the current source and target for operations, along with progress counters. After each action, the next hunk is displayed automatically.

<div class="section-separator"></div>

## Actions

### Primary Actions

| Key | Name | Description |
|-----|------|-------------|
| `i` | **include** | Stage this hunk to the index |
| `s` | **skip** | Skip this hunk for now |
| `d` | **discard** | Remove this hunk from working tree |
| `q` | **quit** | Exit interactive mode |

`discard` is destructive and asks for confirmation before proceeding.

---

### Line Selection (`l`)

Enter a submenu to operate on specific lines within the hunk:

```
Changed line IDs: 1-4

Action for lines [i]nclude, [s]kip, [d]iscard?
```

After choosing an action, enter line IDs:

```
Enter line IDs (e.g., 1,3,5-7):
```

**Line ID syntax:**

- Single: `1`
- Multiple: `1,3,5`
- Range: `5-7`
- Combined: `1,3,5-7`

After processing, the hunk is recalculated to show remaining changes.

---

### File Operations (`f`)

Operate on all hunks in the current file at once:

```
Action for all hunks in auth.py - [i]nclude, [s]kip, [d]iscard?
```

File-level discard asks for confirmation.

---

### Again (`a`)

Clear the blocklist and restart iteration from the first hunk. Useful for making another pass after committing some changes.

---

### Fixup Suggestions (`x`)

Enter a submenu that iteratively suggests commits that modified the lines in the current hunk:

```
Candidate 1: a1b2c3d Fix authentication logic

[y]es / [n]ext / [r]eset:
```

| Key | Action |
|-----|--------|
| `y` | Accept this candidate and show the fixup command |
| `n` | Show the next older candidate |
| `r` | Reset and start over from the most recent |
| `q` | Cancel and return to the main menu |

On the first invocation, you are prompted for a boundary ref (default: `@{upstream}`). Each press of `n` shows the next older commit that touched those lines.

---

### Shell Commands (`!`)

Run a shell command without leaving interactive mode:

- `!git log --oneline -5` runs the command directly
- `!` alone prompts for a command with readline history support

After the command completes, press Enter to return to the hunk display.

---

### Batch Management (`b`)

Open a submenu for managing named batches:

```
Existing batches:
  debug-code - Temporary debugging output
  refactor - Code cleanup

Batch operations:
  [c]reate
  [e]dit
  [d]rop
  [a]pply
```

If no batches exist, you are prompted to create one immediately.

---

### Flow Control (`<` and `>`)

By default, interactive mode pulls changes from the **working tree** and pushes them to **staging** (the index). Flow control lets you redirect these operations to or from named batches.

#### Setting the source (`<`)

```
Pull changes from:

  [1] Working tree (selected)
  [2] batch: debug-code - Temporary debugging output
```

Or use the shorthand `<batch-name` to select directly.

When pulling from a batch, the batch's accumulated changes are presented as the current hunk. `include` stages them and `discard` removes them from the working tree.

#### Setting the target (`>`)

```
Push changes to:

  [1] Staging for commit (selected)
  [2] batch: debug-code - Temporary debugging output
  [3] New Batch...
```

Or use the shorthand `>batch-name` to select directly.

When the target is a batch, `include` saves changes to the batch (without staging them) and `discard` saves changes to the batch then removes them from the working tree.

!!! info "Flow Constraints"
    Both source and target cannot be batches at the same time. Setting source to a batch automatically resets target to staging, and vice versa.

---

### Help (`?`)

Display a summary of all available commands:

```
Interactive Mode Commands:

Primary actions:
  i, include   - Stage this hunk to the index
  s, skip      - Skip this hunk for now
  d, discard   - Remove this hunk from working tree (DESTRUCTIVE)
  q, quit      - Exit interactive mode

More options:
  a, again     - Clear state and start fresh pass through skipped hunks
  l, lines     - Select specific lines from this hunk
  f, file      - Include or skip all hunks in this file
  x, fixup     - Suggest which commit to fixup (iterative)
  !<cmd>       - Run shell command (e.g., !git log, or just ! to prompt)
  ?, help      - Show this help message
```

<div class="section-separator"></div>

## Smart Quit

When you press `q`, interactive mode checks whether anything changed during the session (commits, staged changes, or discards).

**No changes:** silently exits.

**Changes detected:** prompts for what to do:

```
Keep staged changes? [y]es / [n]o:
```

| Choice | Effect |
|--------|--------|
| `y` | Keep all changes and end the session |
| `n` | Undo everything (reset HEAD, restore working tree, restore batches) |
| Ctrl-C | Cancel and return to the main menu |

---

## Degraded Mode

If there are no changes to stage, interactive mode enters degraded mode. Primary hunk actions (`include`, `skip`, `discard`, `lines`, `file`, `fixup`) are disabled, but you can still:

- Manage batches (`b`)
- Change flow source/target (`<`, `>`)
- Run shell commands (`!`)
- View help (`?`)
- Quit (`q`)

This allows batch management even when the working tree is clean.

<div class="section-separator"></div>

## CLI Escape Hatch

Any unrecognized input at the action prompt is parsed as a CLI command. This gives full access to non-interactive commands from within the interactive session:

```
Action: show --from my-batch
Action: status
Action: annotate my-batch "updated note"
```

<div class="section-separator"></div>

## Keyboard Shortcuts

| Key | Effect |
|-----|--------|
| Ctrl-C | At main prompt: exits. In submenus: cancels and returns to main menu |
| Ctrl-D | Same as Ctrl-C |
| Ctrl-R | Reverse search in shell command prompt (GNU readline only) |
| Enter | Empty input at main prompt is a no-op. In submenus: cancels |

<div class="section-separator"></div>

## Example Session

```
❯ git-stage-batch -i

════════════════════════════════════════════════════════════════
Source: working tree → Target: staging
Included: 0  Skipped: 0  Discarded: 0

auth.py :: @@ -10,5 +10,5 @@
[#1] - old_hash_function()
[#2] + new_hash_function()
      validate_user()

Action: i

════════════════════════════════════════════════════════════════
Source: working tree → Target: staging
Included: 1  Skipped: 0  Discarded: 0

config.py :: @@ -20,3 +20,4 @@
[#1] + DEBUG = True
      TIMEOUT = 30

Action: d
⚠️  This will remove the hunk from your working tree.
Are you sure? [yes/NO]: yes

════════════════════════════════════════════════════════════════
Source: working tree → Target: staging
Included: 1  Skipped: 0  Discarded: 1

utils.py :: @@ -5,7 +5,9 @@
[#1] - def old_helper():
[#2] + def new_helper():
[#3] +     """Better implementation."""
      pass

Action: l

Changed line IDs: 1-3

Action for lines [i]nclude, [s]kip, [d]iscard? i
Enter line IDs (e.g., 1,3,5-7): 2,3

No more hunks to process.

Action: q
Keep staged changes? [y]es / [n]o: y
```

<div class="section-separator"></div>

## Comparison with Command Mode

=== "Interactive Mode"

    ```
    # One command, multiple decisions
    ❯ git-stage-batch -i
    # Then: i, s, d, l, etc.
    ```

    - Guided, visual, familiar to `git add -p` users
    - Continuous session with progress tracking
    - Built-in batch management and flow control

=== "Command Mode"

    ```
    # Multiple commands, one decision each
    ❯ git-stage-batch start
    ❯ git-stage-batch include
    ❯ git-stage-batch skip
    ❯ git-stage-batch include --line 1,3
    ❯ git-stage-batch discard
    ```

    - Scriptable, automation-friendly, AI-compatible
    - Machine-readable output with `--porcelain`
    - Each command is independently composable

## Next Steps

- [Commands Reference](commands.md) - Complete reference for command-based mode
- [Examples](examples.md) - Common workflows and use cases
- [Batch Operations](batches.md) - Advanced batch workflows
- [AI Assistants](ai-assistants.md) - Configure for automation

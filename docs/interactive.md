# Interactive Mode

Interactive mode provides a beginner-friendly workflow similar to `git add -p` with clear prompts and single-letter shortcuts.

## Starting Interactive Mode

```bash
git-stage-batch --interactive
```

## How It Works

Interactive mode presents hunks one at a time and prompts you for an action:

```
test.py :: @@ -10,5 +10,5 @@
[#1] - old_function()
[#2] + new_function()
      context_line()

What do you want to do with this hunk?
  [i]nclude  - Stage this hunk to the index
  [s]kip     - Skip this hunk for now
  [d]iscard  - Remove this hunk from working tree (DESTRUCTIVE)
  [q]uit     - Exit interactive mode

More options: [a]ll, [l]ines, [f]ile, [b]lock, [?]help

Action:
```

## Available Actions

### Basic Actions

| Key | Action | Description |
|-----|--------|-------------|
| `i` | **include** | Stage this hunk |
| `s` | **skip** | Skip this hunk for now |
| `d` | **discard** | Remove from working tree (asks for confirmation) |
| `q` | **quit** | Exit interactive mode |

### Advanced Actions

| Key | Action | Description |
|-----|--------|-------------|
| `a` | **all** | Stage all remaining hunks (asks for confirmation) |
| `l` | **lines** | Enter line selection sub-menu |
| `f` | **file** | Stage or skip all hunks in current file |
| `b` | **block** | Block this file permanently via .gitignore |
| `x` | **suggest-fixup** | Suggest which commit to fixup for this hunk |
| `?` | **help** | Show detailed help |

## Line Selection Sub-Menu

When you press `l`, you enter line selection mode:

```
Available line operations:
  [i] IDS  - Include specific lines (e.g., i 1,3,5-7)
  [s] IDS  - Skip specific lines (e.g., s 2,4)
  [d] IDS  - Discard specific lines (e.g., d 8-10)
  [b]ack   - Return to main menu

Line selection:
```

**Examples:**
```
i 1,3,5-7    # Include lines 1, 3, and 5 through 7
s 2,4        # Skip lines 2 and 4
d 8-10       # Discard lines 8, 9, and 10
back         # Return to main menu
```

## File Operations

When you press `f`, you see file-level options:

```
File operations for test.py:
  [i]nclude - Stage all remaining hunks in this file
  [s]kip    - Skip all remaining hunks in this file
  [b]ack    - Return to main menu

File action:
```

## Fixup Suggestions

When you press `x`, the tool analyzes which commits modified the lines in the current hunk:

```
Suggested fixup target: a1b2c3d auth: Implement new hashing
Run: git commit --fixup=a1b2c3d
```

**Workflow:**
1. Press `x` on a hunk to see which recent commit it should fix up
2. Stage the hunk with `i` (or skip it with `s`)
3. Later, create a fixup commit: `git commit --fixup=a1b2c3d`
4. Use `git rebase -i --autosquash` to automatically squash fixups

**Boundary:**

By default, suggests commits in the range `@{upstream}..HEAD`. You'll be prompted to specify a different boundary ref if needed.

**Use case:**

Perfect for polishing feature branches before submitting. When you notice a bug or improvement opportunity in recently-committed code, use suggest-fixup to quickly identify which commit to amend, keeping your commit history clean and atomic.

## Example Session

```bash
$ git-stage-batch --interactive

# First hunk appears
auth.py :: @@ -10,5 +10,5 @@
[#1] - old_hash_function()
[#2] + new_hash_function()
      validate_user()

Action: i

# Second hunk appears
config.py :: @@ -20,3 +20,4 @@
[#1] + DEBUG = True
      TIMEOUT = 30

Action: s

# Third hunk appears
utils.py :: @@ -5,7 +5,9 @@
[#1] - def old_helper():
[#2] + def new_helper():
[#3] +     """Better implementation."""
      pass

Action: l

# Line selection sub-menu
Line selection: i 2,3

# Returns to next hunk
No pending hunks.

# Exit interactive mode
```

## Why Use Interactive Mode?

### Good For:

- ✅ **Learning** - Clear prompts explain each option
- ✅ **Manual staging** - Hands-on control over each decision
- ✅ **Quick reviews** - Single-letter shortcuts are fast
- ✅ **Familiar workflow** - Similar to `git add -p`

### Less Good For:

- ❌ **Automation** - Use command-based mode instead
- ❌ **AI assistants** - They prefer the command-based workflow
- ❌ **Scripting** - Use `--porcelain` flags with commands

## Tips

1. **Use short keys** - Just type the letter and press Enter
2. **Press `?` for help** - Shows detailed help anytime
3. **Line IDs are shown** - Look for `[#N]` markers
4. **Confirmations for destructive actions** - `discard`, `all`, and `block` ask for confirmation
5. **Exit safely** - Press `q` to quit without processing remaining hunks

## Comparison with Command Mode

=== "Interactive Mode"

    ```bash
    # One command, multiple decisions
    git-stage-batch --interactive
    # Then: i, s, i, d, etc.
    ```

    - **Pros:** Guided, familiar, visual
    - **Cons:** Not scriptable, requires interaction

=== "Command Mode"

    ```bash
    # Multiple commands, one decision each
    git-stage-batch start
    git-stage-batch include
    git-stage-batch skip
    git-stage-batch include
    git-stage-batch discard
    ```

    - **Pros:** Scriptable, automation-friendly, AI-compatible
    - **Cons:** More typing for manual use

Choose based on your workflow!

## Next Steps

- [Commands Reference](commands.md) - Learn command-based mode
- [Examples](examples.md) - See common workflows
- [AI Assistants](ai-assistants.md) - Configure for automation

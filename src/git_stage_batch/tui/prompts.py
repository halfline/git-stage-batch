"""TUI input and prompt utilities for interactive mode."""

from __future__ import annotations

import re

try:
    import readline
    HAS_READLINE = True
    # Check if we're using libedit (which has poor reverse search)
    INPUT_USES_LIBEDIT = getattr(readline, 'backend', None) == 'editline'

    if INPUT_USES_LIBEDIT:
        # Disable Ctrl-R entirely for libedit - it's not good enough
        try:
            readline.parse_and_bind("bind -r ^R")
        except Exception:
            pass
    else:
        # For GNU readline, disable Ctrl-R by default (will enable in shell prompt)
        try:
            readline.parse_and_bind("bind -r ^R")
        except Exception:
            pass
except ImportError:
    HAS_READLINE = False
    INPUT_USES_LIBEDIT = False
    readline = None  # type: ignore

from ..output import Colors, format_hotkey, format_option_list
from ..i18n import _, pgettext

# Module-level storage for shell command history
_shell_command_history: list[str] = []


def wrap_prompt_for_readline(prompt: str) -> str:
    """Wrap ANSI escape sequences in readline ignore markers.

    Readline needs escape sequences wrapped in \\001 and \\002 so it can
    calculate line length correctly for features like backspace and Ctrl-R.

    Args:
        prompt: Prompt string that may contain ANSI escape codes

    Returns:
        Prompt with escape codes wrapped if readline is available,
        otherwise unchanged
    """
    if not HAS_READLINE:
        return prompt

    # Pattern to match ANSI escape sequences (ESC[...m)
    # \x1b is ESC, then [, then any chars until m
    escape_pattern = re.compile(r'(\x1b\[[0-9;]*m)')

    # Wrap each escape sequence in \001 and \002
    return escape_pattern.sub(r'\001\1\002', prompt)


def prompt_action(use_color: bool = True, show_question: bool = True, has_hunk: bool = True) -> str:
    """
    Prompt user for an action choice.

    Displays primary and secondary actions with hotkeys and prompts for input.

    Args:
        use_color: Whether to use colored output
        show_question: Whether to display the "What do you want to do" question
        has_hunk: Whether a hunk is available (affects which actions are shown)

    Returns:
        Normalized choice (e.g., 'i', 's', 'd', 'q', 'a', 'l', 'x', '!', etc.)
    """
    if has_hunk:
        # Primary actions
        primary_options = [
            ("include", "i", Colors.GREEN if use_color else ""),
            ("skip", "s", ""),
            ("discard", "d", Colors.RED if use_color else ""),
            ("quit", "q", ""),
        ]

        # Scope options
        scope_options = [
            ("lines", "l", ""),
            ("file", "f", ""),
        ]

        # Flow options
        flow_options = [
            ("from", "<", ""),
            ("to", ">", ""),
        ]

        # More options
        more_options = [
            ("again", "a", ""),
            ("batch", "b", ""),
            ("fixup", "x", ""),
            ("cmd", "!", ""),
            ("help", "?", ""),
        ]
    else:
        # No hunk available - only show non-hunk actions
        primary_options = [
            ("quit", "q", ""),
            ("help", "?", ""),
        ]

        # Scope options - none in degraded mode
        scope_options = []

        # Flow options - still available
        flow_options = [
            ("from", "<", ""),
            ("to", ">", ""),
        ]

        # More options - limited set
        more_options = [
            ("batch", "b", ""),
            ("cmd", "!", ""),
        ]

    if show_question:
        print()
        if has_hunk:
            print(_("What do you want to do with this hunk?"))
        else:
            print(_("What do you want to do?"))
        for text, hotkey, color in primary_options:
            formatted = format_hotkey(text, hotkey, color)
            print(f"  {formatted}")

        # Three-section menu line
        print()

        rendered_sections = []

        def append_section(
                rendered_sections,
                label,
                options,
                use_color,
        ):
                formatted_options = format_option_list(options)

                if use_color and Colors.enabled():
                        rendered_sections.append(
                                _("{label}: {options}").format(
                                        label=f"{Colors.GRAY}{label}{Colors.RESET}",
                                        options=f"{Colors.CYAN}{formatted_options}{Colors.RESET}",
                                )
                        )
                else:
                        rendered_sections.append(
                                _("{label}: {options}").format(
                                        label=label,
                                        options=formatted_options,
                                )
                        )

        section_specs = [
                (pgettext("menu section label", "Other scope"), scope_options),
                (pgettext("menu section label", "Flow"), flow_options),
                (pgettext("menu section label", "More"), more_options),
        ]

        for label, options in section_specs:
                if options:
                        append_section(
                                rendered_sections,
                                label,
                                options,
                                use_color,
                        )

        if rendered_sections:
                separator = pgettext("menu section separator", " | ")
                if use_color and Colors.enabled():
                        separator = f"{Colors.GRAY}{separator}{Colors.RESET}"

                print(separator.join(rendered_sections))

        print()
    try:
        prompt_text = _("Action: ")
        if use_color and Colors.enabled():
            # Remove trailing space, add color, add space after reset
            prompt_text = f"{Colors.BOLD}{prompt_text.rstrip()}{Colors.RESET} "
        choice = input(wrap_prompt_for_readline(prompt_text)).strip()
    except (KeyboardInterrupt, EOFError):
        return "q"  # Ctrl-C or Ctrl-D exits

    # Normalize full words to single letters (case-insensitive)
    choice_lower = choice.lower()
    word_to_letter = {
        "include": "i",
        "skip": "s",
        "discard": "d",
        "quit": "q",
        "again": "a",
        "lines": "l",
        "file": "f",
        "batch": "b",
        "fixup": "x",
        "command": "!",
        "help": "?",
        "from": "<",
        "to": ">",
    }

    return word_to_letter.get(choice_lower, choice_lower)


def confirm_destructive_operation(_operation: str, message: str) -> bool:
    """
    Prompt for confirmation of a destructive operation.

    Args:
        _operation: Name of the operation (e.g., "discard", "block")
        message: Warning message to display

    Returns:
        True if user confirmed with "yes", False otherwise
    """
    use_color = Colors.enabled()

    print()
    warning_text = _("⚠️  {message}").format(message=message)
    if use_color:
        print(f"{Colors.RED}{warning_text}{Colors.RESET}")
    else:
        print(warning_text)

    try:
        yes_text = _("yes")
        no_text = _("NO")

        if use_color:
            yes_label = f"{Colors.GREEN}{yes_text}{Colors.RESET}"
            no_label = f"{Colors.BOLD}{no_text}{Colors.RESET}"
        else:
            yes_label = yes_text
            no_label = no_text

        prompt_text = _("Are you sure? [{yes}/{no}]: ").format(
            yes=yes_label,
            no=no_label,
        )
        response = input(wrap_prompt_for_readline(prompt_text)).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return False

    return response == yes_text.lower()


def prompt_line_ids() -> str:
    """
    Prompt for line IDs with validation hint.

    Returns:
        User input string (e.g., "1,3,5-7")
    """
    try:
        return input(_("Enter line IDs (e.g., 1,3,5-7): ")).strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def prompt_quit_session() -> str:
    """
    Prompt whether to keep staged changes when quitting.

    Returns:
        'keep', 'undo', or 'cancel'
    """
    use_color = Colors.enabled()

    print()
    try:
        y_text = _("y")
        n_text = _("n")
        yes_text = _("yes")
        no_text = _("no")

        if use_color:
            y_label = f"{Colors.GREEN}{y_text}{Colors.RESET}"
            n_label = f"{Colors.RED}{n_text}{Colors.RESET}"
        else:
            y_label = y_text
            n_label = n_text

        prompt_text = _("Keep staged changes? [{y}]es / [{n}]o: ").format(
            y=y_label,
            n=n_label,
        )
        response = input(wrap_prompt_for_readline(prompt_text)).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return "cancel"

    if response in (y_text.lower(), yes_text.lower()):
        return "keep"
    elif response in (n_text.lower(), no_text.lower()):
        return "undo"
    else:
        return "cancel"


def prompt_shell_command() -> str:
    """
    Prompt for a shell command to execute.

    Uses readline for command history and editing if available.
    Ctrl-R reverse search works here to search previous commands.

    Returns:
        Command string entered by user, or empty string if cancelled
    """
    global _shell_command_history

    # Load shell command history and enable Ctrl-R for GNU readline only
    if HAS_READLINE:
        for cmd in _shell_command_history:
            readline.add_history(cmd)
        # Only enable Ctrl-R for GNU readline (libedit's reverse search is too poor)
        if not INPUT_USES_LIBEDIT:
            try:
                readline.parse_and_bind("bind ^R em-inc-search-prev")  # Re-bind Ctrl-R
            except Exception:
                pass

    try:
        command = input("❯ ").strip()
        if command:
            # Save to module-level history
            _shell_command_history.append(command)
        return command
    except (KeyboardInterrupt, EOFError):
        # Ctrl-C during reverse search should just cancel, not exit
        return ""
    finally:
        # Clear readline history and disable Ctrl-R again (only for GNU readline)
        if HAS_READLINE:
            readline.clear_history()
            if not INPUT_USES_LIBEDIT:
                try:
                    readline.parse_and_bind("bind -r ^R")  # Unbind Ctrl-R
                except Exception:
                    pass


def prompt_fixup_action(use_color: bool = True) -> str:
    """
    Prompt for suggest-fixup submenu action.

    Args:
        use_color: Whether to use colored output

    Returns:
        Normalized choice ('y', 'n', 'r', or other input)
    """
    try:
        y_text = _("y")
        n_text = _("n")
        r_text = _("r")

        if use_color and Colors.enabled():
            y_label = f"{Colors.GREEN}{y_text}{Colors.RESET}"
            n_label = n_text
            r_label = r_text
        else:
            y_label = y_text
            n_label = n_text
            r_label = r_text

        prompt_text = _("[{y}]es / [{n}]ext / [{r}]eset: ").format(
            y=y_label,
            n=n_label,
            r=r_label,
        )
        choice = input(wrap_prompt_for_readline(prompt_text)).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return "q"  # Ctrl-C or Ctrl-D cancels

    # Normalize full words to single letters
    word_to_letter = {
        "yes": "y",
        "next": "n",
        "reset": "r",
        "cancel": "q",
        "quit": "q",
    }

    return word_to_letter.get(choice, choice)

"""TUI input and prompt utilities for interactive mode."""

from __future__ import annotations

from ..output import Colors, format_hotkey, format_option_list
from ..i18n import _


def prompt_action(use_color: bool = True, show_question: bool = True) -> str:
    """
    Prompt user for an action choice.

    Displays primary and secondary actions with hotkeys and prompts for input.

    Args:
        use_color: Whether to use colored output
        show_question: Whether to display the "What do you want to do" question

    Returns:
        Normalized choice (e.g., 'i', 's', 'd', 'q', 'a', 'l', etc.)
    """
    # Primary actions
    primary_options = [
        ("include", "i", Colors.GREEN if use_color else ""),
        ("skip", "s", ""),
        ("discard", "d", Colors.RED if use_color else ""),
        ("quit", "q", ""),
    ]

    # Secondary actions (More options)
    secondary_options = [
        ("all", "a", ""),
        ("lines", "l", ""),
        ("file", "f", ""),
        ("help", "?", ""),
    ]

    if show_question:
        print()
        print(_("What do you want to do with this hunk?"))
        for text, hotkey, color in primary_options:
            formatted = format_hotkey(text, hotkey, color)
            print(f"  {formatted}")

        # More options line
        more_options_text = _("More options: {options}").format(options=format_option_list(secondary_options))
        if use_color and Colors.enabled():
            print(f"\n{Colors.CYAN}{more_options_text}{Colors.RESET}")
        else:
            print(f"\n{more_options_text}")

        print()
    try:
        prompt_text = _("Action: ")
        if use_color and Colors.enabled():
            prompt_text = f"{Colors.BOLD}{prompt_text}{Colors.RESET}"
        choice = input(prompt_text).strip()
    except (KeyboardInterrupt, EOFError):
        return "q"  # Ctrl-C or Ctrl-D exits

    # Normalize full words to single letters (case-insensitive)
    choice_lower = choice.lower()
    word_to_letter = {
        "include": "i",
        "skip": "s",
        "discard": "d",
        "quit": "q",
        "all": "a",
        "lines": "l",
        "file": "f",
        "help": "?",
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
        prompt_text = _("Are you sure? [yes/NO]: ")
        if use_color:
            # Apply color to specific parts
            prompt_text = prompt_text.replace("yes", f"{Colors.GREEN}yes{Colors.RESET}")
            prompt_text = prompt_text.replace("NO", f"{Colors.BOLD}NO{Colors.RESET}")
        response = input(prompt_text).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return False

    return response == "yes"


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
        prompt_text = _("Keep staged changes? [y]es / [n]o: ")
        if use_color:
            # Apply color to specific parts
            prompt_text = prompt_text.replace("[y]", f"[{Colors.GREEN}y{Colors.RESET}]")
            prompt_text = prompt_text.replace("[n]", f"[{Colors.RED}n{Colors.RESET}]")
        response = input(prompt_text).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return "cancel"

    if response in ("y", "yes"):
        return "keep"
    elif response in ("n", "no"):
        return "undo"
    else:
        return "cancel"

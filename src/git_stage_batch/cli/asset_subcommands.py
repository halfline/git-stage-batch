"""Asset installation subcommand registration."""

from __future__ import annotations

from ..commands.install_assets import command_install_assets
from ..i18n import _
from .subcommand_parser import add_subcommand_parser


def add_install_assets_subcommand(subparsers) -> None:
    """Register the install-assets subcommand."""
    parser_install_assets = add_subcommand_parser(
        subparsers,
        "install-assets",
        help=_("Install bundled assistant assets into the repository"),
    )
    parser_install_assets.add_argument(
        "asset_group",
        choices=["claude-agents", "claude-skills", "codex-skills"],
        nargs="?",
        help=_("Bundled asset group to install"),
    )
    parser_install_assets.add_argument(
        "--filter",
        dest="filters",
        metavar="PATTERN",
        nargs="+",
        help=_(
            "Install only bundled assets whose names match one or more gitignore-style PATTERNs"
        ),
    )
    parser_install_assets.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite an existing installed asset"),
    )
    parser_install_assets.set_defaults(
        func=lambda args: command_install_assets(
            args.asset_group,
            args.filters,
            force=args.force,
        )
    )

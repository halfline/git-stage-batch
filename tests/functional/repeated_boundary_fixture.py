"""Fixed content captured from the repeated-boundary staging regression."""


_ORIGINAL = """                        source_segments=("assets", "claude-agents", "decompose-rebuilder.md"),
                        target_segments=(".claude", "agents", "decompose-rebuilder.md"),
                        display_name="Claude agent",
                    ),
                    CompanionAsset(
                        source_segments=("assets", "claude-skills", "refine-history"),
                        target_segments=(".claude", "skills", "refine-history"),
                        display_name="Claude skill",
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".claude",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name="Claude skill",
                    ),
                ),
            ),
            (
                "refine-history",
                (
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".claude",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name="Claude skill",
                    ),
                ),
            ),
        ),
    ),
    "codex-skills": AssetGroup(
        source_segments=("assets", "codex-skills"),
        target_segments=(".agents", "skills"),
        display_name_singular="Codex skill",
        display_name_plural="Codex skills",
        required_entry="SKILL.md",
        companion_assets=(
            CompanionAsset(
                source_segments=(
                    "assets",
                    "codex-skills",
                    "internal",
                    "commit-message-drafter.md",
                ),
                target_segments=(".agents", "internal", "commit-message-drafter.md"),
                display_name="Codex internal asset",
            ),
            CompanionAsset(
                source_segments=("assets", "codex-skills", "config", "config.toml"),
                target_segments=(".codex", "config.toml"),
                display_name="Codex config",
            ),
        ),
    ),
}
"""

_EXPANDED_SOURCE_SEGMENTS = """                        source_segments=(
                            "assets",
                            "claude-agents",
                            "decompose-rebuilder.md",
                        ),"""

_INSERTION = """        entry_companion_assets=(
            (
                "decompose-and-commit-unstaged-changes",
                (
                    CompanionAsset(
                        source_segments=("assets", "codex-skills", "refine-history"),
                        target_segments=(".agents", "skills", "refine-history"),
                        display_name="Codex skill",
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "codex-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".agents",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name="Codex skill",
                    ),
                ),
            ),
            (
                "refine-history",
                (
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "codex-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".agents",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name="Codex skill",
                    ),
                ),
            ),
        ),
"""


def captured_repeated_boundary_insertion() -> tuple[str, str, str]:
    """Return original, changed, and coordinate-correct expected content."""
    compact_source_segments = (
        '                        source_segments=("assets", "claude-agents", '
        '"decompose-rebuilder.md"),'
    )
    final_boundary = "    ),\n}\n"
    assert _ORIGINAL.count(compact_source_segments) == 1
    assert _ORIGINAL.count(final_boundary) == 1
    changed = _ORIGINAL.replace(
        compact_source_segments,
        _EXPANDED_SOURCE_SEGMENTS,
        1,
    ).replace(
        final_boundary,
        _INSERTION + final_boundary,
        1,
    )
    expected = _ORIGINAL.replace(
        final_boundary,
        _INSERTION + final_boundary,
        1,
    )
    return _ORIGINAL, changed, expected

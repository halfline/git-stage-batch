"""Tests for localized bundled-asset labels."""

from git_stage_batch.data import asset_catalog


def test_builtin_asset_menu_labels_use_plural_translation_context(
    monkeypatch,
) -> None:
    seen: list[tuple[str, str]] = []

    def record_translation(context: str, message: str) -> str:
        seen.append((context, message))
        return f"translated:{message}"

    monkeypatch.setattr(asset_catalog, "pgettext", record_translation)

    label = asset_catalog.asset_group_menu_label(
        asset_catalog.ASSET_GROUPS["claude-skills"]
    )

    assert label == "translated:Claude skills"
    assert seen == [("asset group menu label", "Claude skills")]

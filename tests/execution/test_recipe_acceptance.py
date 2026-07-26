from pathlib import Path

import yaml

from studio.execution.recipes import list_recipe_reviews, record_recipe_decision


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    recipe = root / "recipes" / "effect" / "flash"
    recipe.mkdir(parents=True)
    (recipe / "flash.comp").write_text("comp", encoding="utf-8")
    (recipe / "preview.mp4").write_bytes(b"video")
    (recipe / "ACCEPTANCE.md").write_text(
        "Human status: **PENDING**\n", encoding="utf-8"
    )
    config = root / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "recipes": [{
                    "id": "flash", "version": "1.0.0", "kind": "effect",
                    "engine": "fusion", "capability": "fusion_recipe",
                    "verified": False,
                    "artifact": "recipes/effect/flash/flash.comp",
                    "preview": "recipes/effect/flash/preview.mp4",
                    "acceptance": "recipes/effect/flash/ACCEPTANCE.md",
                    "parameters": {},
                }],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return root, config


def test_human_decision_updates_record_and_registry_together(tmp_path):
    root, config = _fixture(tmp_path)
    assert list_recipe_reviews(config=config, root=root)[0].status == "pending"
    accepted = record_recipe_decision(
        "flash", reviewer="Owner", decision="accepted", notes="clean",
        reviewed_at="2026-07-25T19:30:00+08:00", config=config, root=root,
    )
    assert accepted.status == "accepted"
    assert accepted.verified is True
    rejected = record_recipe_decision(
        "flash", reviewer="Owner", decision="rejected", notes="too strong",
        reviewed_at="2026-07-25T19:31:00+08:00", config=config, root=root,
    )
    assert rejected.status == "rejected"
    assert rejected.verified is False

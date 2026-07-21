from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _reload_modules(monkeypatch: pytest.MonkeyPatch, root: Path):
    monkeypatch.setenv("ANIME_EDIT_ROOT", str(root))
    monkeypatch.setenv("ANIME_EDIT_CONFIG", str(root / "config.toml"))
    monkeypatch.setenv("ANIME_EDIT_LIBRARY", str(root / "library"))
    monkeypatch.setenv("ANIME_EDIT_PROJECTS", str(root / "projects"))
    monkeypatch.setenv("ANIME_EDIT_DB_PATH", str(root / "library" / "engine.sqlite"))
    import anime.config as cfg
    import anime.db as db_mod
    import anime.decision_loop as loop
    import anime.cli as cli

    importlib.reload(cfg)
    importlib.reload(db_mod)
    importlib.reload(loop)
    importlib.reload(cli)
    return cfg, db_mod, loop, cli


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "library").mkdir()
    (root / "projects").mkdir()
    (root / "review-web").mkdir()
    (root / "config.toml").write_text(
        """
[tools]
ffmpeg = "ffmpeg"
ffprobe = "ffprobe"

[proxy]
edit_height = 360
analysis_height = 180

[scoring.weights]
technical_quality = 0.15
composition_quality = 0.15
brief_match = 0.20
structure_match = 0.15
preference_score = 0.20
diversity_score = 0.10
risk_penalty = 0.05
""".strip()
    )
    return root


def _make_video(path: Path):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def _seed_data(root: Path, db_mod):
    video = root / "library" / "sample.mp4"
    _make_video(video)
    conn = db_mod.connect()
    db_mod.upsert_asset(
        conn,
        {
            "id": "asset001",
            "path": str(video),
            "sha256": "a" * 64,
            "width": 640,
            "height": 360,
            "fps": 30.0,
            "duration": 2.0,
            "codec": "h264",
            "proxy_path": str(video),
        },
    )
    shots = [
        {"id": "asset001-0", "asset_id": "asset001", "idx": 0, "start_sec": 0.0, "end_sec": 0.6, "keyframe": str(video)},
        {"id": "asset001-1", "asset_id": "asset001", "idx": 1, "start_sec": 0.6, "end_sec": 1.2, "keyframe": str(video)},
        {"id": "asset001-2", "asset_id": "asset001", "idx": 2, "start_sec": 1.2, "end_sec": 1.8, "keyframe": str(video)},
    ]
    for shot in shots:
        db_mod.insert_shot(conn, shot)
    db_mod.update_shot_analysis(
        conn,
        "asset001-0",
        {"brightness": 0.5, "sharpness": 380, "motion_mag": 0.2, "character": "gojo", "emotion": "calm", "tags": "face,close-up,blue"},
    )
    db_mod.update_shot_analysis(
        conn,
        "asset001-1",
        {"brightness": 0.7, "sharpness": 420, "motion_mag": 1.2, "character": "gojo", "action": "fight", "emotion": "intense", "tags": "impact,action,hero"},
    )
    db_mod.update_shot_analysis(
        conn,
        "asset001-2",
        {"brightness": 0.2, "sharpness": 260, "motion_mag": 0.1, "emotion": "sad", "tags": "wide,sky,ending"},
    )
    conn.close()
    return video


def test_db_migrations_and_scoring(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod)
    result = loop.upsert_brief(
        "demo",
        {
            "character_query": "gojo",
            "theme": "awakening",
            "target_emotions": ["intense", "sad"],
            "duration_sec": 24,
            "aspect_ratio": "4:5",
            "target_platform": "douyin",
        },
    )
    assert result["project_id"] == "demo"
    score = loop.score_project_shots("demo")
    assert score["scored_shots"] == 3
    shots = loop.list_project_shots("demo")
    assert shots[0]["final_score"] >= shots[-1]["final_score"]


def test_review_gap_blueprint_and_variant_cli_json(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, _, cli = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod)
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        ["brief", "create", "demo", "--character", "gojo", "--theme", "awakening", "--emotion", "intense,sad", "--duration", "24", "--aspect", "4:5", "--platform", "douyin", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["project_id"] == "demo"

    result = runner.invoke(cli.app, ["gap", "demo", "--json"])
    assert result.exit_code == 0, result.output
    gap = json.loads(result.output)
    assert "segments" in gap and "hook" in gap["segments"]

    result = runner.invoke(cli.app, ["blueprint", "demo", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["variants"]) == 3

    variant_id = payload["variants"][0]["id"]
    result = runner.invoke(cli.app, ["variant", "select", "demo", str(variant_id), "--json"])
    assert result.exit_code == 0, result.output
    selected = json.loads(result.output)
    assert selected["selected"] is True


def test_preference_training_and_explain(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod)
    loop.upsert_brief("demo", {"character_query": "gojo", "target_emotions": ["intense"]})
    for index in range(35):
        shot_id = f"asset001-{index % 3}"
        loop.put_review("demo", shot_id, {"decision": "use" if index % 2 == 0 else "reject", "reasons": ["loop"]})
    trained = loop.train_preference()
    assert trained["trained_on"] >= 1
    explained = loop.explain_preference("asset001-1")
    assert explained["shot_id"] == "asset001-1"
    assert "preference_score" in explained


def test_api_media_security(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("fastapi")
    _, db_mod, loop, _ = _reload_modules(monkeypatch, workspace)
    video = _seed_data(workspace, db_mod)
    loop.upsert_brief("demo", {"character_query": "gojo"})
    from fastapi.testclient import TestClient
    from anime.review_api import create_app

    client = TestClient(create_app())
    assert client.get("/api/projects/demo").status_code == 200
    assert client.get("/api/assets/asset001/preview").status_code == 200

    conn = db_mod.connect()
    conn.execute("UPDATE shots SET keyframe=? WHERE id='asset001-0'", ("../outside.jpg",))
    conn.commit()
    conn.close()
    blocked = client.get("/api/shots/asset001-0/keyframe")
    assert blocked.status_code in {400, 403, 404}
    assert video.exists()

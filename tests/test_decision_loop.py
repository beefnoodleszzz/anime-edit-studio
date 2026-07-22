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
    import anime.reference as reference
    import anime.candidates as candidates

    importlib.reload(cfg)
    importlib.reload(db_mod)
    importlib.reload(loop)
    importlib.reload(cli)
    importlib.reload(reference)
    importlib.reload(candidates)
    return cfg, db_mod, loop, cli, reference, candidates


@pytest.fixture()
def workspace(tmp_path: Path):
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


def _make_video(path: Path, *, size: str = "640x360", color: str = "testsrc"):
    video_filter = f"{color}=size={size}:rate=30:duration=3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            video_filter,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
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


def _seed_data(root: Path, db_mod, loop):
    video1 = root / "library" / "sample_gojo.mp4"
    video2 = root / "library" / "sample_other.mp4"
    _make_video(video1, size="640x360", color="testsrc")
    _make_video(video2, size="360x640", color="testsrc2")
    conn = db_mod.connect()
    assets = [
        {
            "id": "asset001",
            "path": str(video1),
            "sha256": "a" * 64,
            "width": 640,
            "height": 360,
            "fps": 30.0,
            "duration": 3.0,
            "codec": "h264",
            "proxy_path": str(video1),
        },
        {
            "id": "asset002",
            "path": str(video2),
            "sha256": "b" * 64,
            "width": 360,
            "height": 640,
            "fps": 30.0,
            "duration": 3.0,
            "codec": "h264",
            "proxy_path": str(video2),
        },
    ]
    for asset in assets:
        db_mod.upsert_asset(conn, asset)
    shots = [
        {"id": "asset001-0", "asset_id": "asset001", "idx": 0, "start_sec": 0.0, "end_sec": 0.8, "keyframe": str(video1)},
        {"id": "asset001-1", "asset_id": "asset001", "idx": 1, "start_sec": 0.8, "end_sec": 1.6, "keyframe": str(video1)},
        {"id": "asset001-2", "asset_id": "asset001", "idx": 2, "start_sec": 1.6, "end_sec": 2.4, "keyframe": str(video1)},
        {"id": "asset002-0", "asset_id": "asset002", "idx": 0, "start_sec": 0.0, "end_sec": 0.9, "keyframe": str(video2)},
    ]
    for shot in shots:
        db_mod.insert_shot(conn, shot)
    db_mod.update_shot_analysis(conn, "asset001-0", {"brightness": 0.5, "sharpness": 380, "motion_mag": 0.2, "character": "gojo", "emotion": "calm", "tags": "face,close-up,blue"})
    db_mod.update_shot_analysis(conn, "asset001-1", {"brightness": 0.7, "sharpness": 420, "motion_mag": 1.2, "character": "gojo", "action": "fight", "emotion": "intense", "tags": "impact,action,hero"})
    db_mod.update_shot_analysis(conn, "asset001-2", {"brightness": 0.2, "sharpness": 260, "motion_mag": 0.1, "emotion": "sad", "tags": "wide,sky,ending"})
    db_mod.update_shot_analysis(conn, "asset002-0", {"brightness": 0.8, "sharpness": 410, "motion_mag": 0.9, "character": "tanjiro", "action": "run", "emotion": "intense", "tags": "action,hero,green"})
    conn.close()
    loop.upsert_source_record("asset001", {"source_url": "https://example.com/gojo", "status": "approved", "commercial_allowed": True})
    loop.upsert_source_record("asset002", {"source_url": "https://example.com/other", "status": "blocked", "commercial_allowed": False})
    return video1, video2


def test_project_scope_and_scoring(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "theme": "awakening", "target_emotions": ["intense", "sad"], "duration_sec": 24, "aspect_ratio": "4:5"})
    project = loop.get_project("demo")
    assert project["shot_count"] == 3
    assert project["asset_ids"] == ["asset001"]


def test_trim_validation_and_variant_final_master(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    with pytest.raises(ValueError):
        loop.patch_trim("demo", "asset001-0", 0.7, 0.3)
    loop.patch_trim("demo", "asset001-0", 0.1, 0.6)
    blueprints = loop.generate_blueprints("demo")
    selected = loop.select_variant("demo", blueprints["variants"][0]["id"])
    final_spec = json.loads(Path(selected["final_editspec_path"]).read_text())
    assert all("sample_gojo.mp4" in shot["src"] for shot in final_spec["shots"])


def test_cli_json_reference_and_preference_modes(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, cli, reference, _ = _reload_modules(monkeypatch, workspace)
    video1, _ = _seed_data(workspace, db_mod, loop)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["brief", "create", "demo", "--character", "gojo", "--theme", "awakening", "--emotion", "intense,sad", "--duration", "24", "--aspect", "4:5", "--json"]).exit_code == 0
    assert runner.invoke(cli.app, ["project", "attach", "demo", "asset001", "--json"]).exit_code == 0
    ref = runner.invoke(cli.app, ["reference", "analyze", "demo", str(video1), "--json"])
    assert ref.exit_code == 0
    assert json.loads(ref.output)["beat_alignment"] is not None
    gap = runner.invoke(cli.app, ["gap", "demo", "--json"])
    assert gap.exit_code == 0
    blueprints = runner.invoke(cli.app, ["blueprint", "demo", "--json"])
    assert blueprints.exit_code == 0
    payload = json.loads(blueprints.output)
    assert len(payload["variants"]) == 3
    for index in range(35):
        loop.put_review(f"demo-{index}", f"asset001-{index % 3}", {"decision": "use" if index % 2 == 0 else "reject", "reasons": ["loop"]})
    trained = loop.train_preference()
    assert trained["model_type"] == "logistic"
    rebuilt = runner.invoke(cli.app, ["preference", "rebuild", "--json"])
    assert rebuilt.exit_code == 0
    reset = runner.invoke(cli.app, ["preference", "reset", "--json"])
    assert reset.exit_code == 0


def test_api_config_rights_gate_and_candidates(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("fastapi")
    _, db_mod, loop, _, _, candidates = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    from fastapi.testclient import TestClient
    from anime.review_api import create_app

    app = create_app()
    app.state.default_project_id = "demo"
    client = TestClient(app)
    assert client.get("/api/config").json()["default_project_id"] == "demo"
    blocked = loop.rights_report("demo")
    assert blocked["export_allowed"] is True
    listed = candidates.list_candidates(min_height=0, limit=10)
    candidate_map = {item["id"]: item for item in listed}
    assert candidate_map["asset001"]["source_status"] == "approved"
    assert candidate_map["asset002"]["status"] == "reject"

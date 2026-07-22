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


def test_segment_frame_plan_normalizes_reference_dna(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, _, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    roles = ["hook", "build", "climax", "release", "ending"]
    total_frames = 25 * loop.BLUEPRINT_FPS
    # 参考片镜头中位数极短(0.5s)且 Hook/Ending 用参考秒数:归一化后总时长仍须贴合 Brief。
    dna = {"shot_duration_distribution": [0.5, 0.5, 0.4, 0.6], "hook_length": 1.2, "ending_shot_length": 0.9}
    plan = loop._segment_frame_plan({"duration_sec": 25}, dna, roles)
    assert sum(plan.values()) == total_frames
    assert all(frames >= loop.BLUEPRINT_FPS // 2 for frames in plan.values())
    no_ref = loop._segment_frame_plan({"duration_sec": 25}, None, roles)
    assert sum(no_ref.values()) == total_frames


def test_pick_duration_never_exceeds_boundary(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, _, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    # 镜头只剩 ~5 帧(0.167s):不得被强行拉到 15 帧越过 Out 点。
    short = {"start_sec": 0.0, "end_sec": 5 / loop.BLUEPRINT_FPS}
    assert loop._pick_duration_frames(short, requested=45) <= loop._shot_available_frames(short)
    assert loop._pick_duration_frames(short, requested=45) < loop.MIN_SHOT_FRAMES


def test_resolve_master_sources_maps_by_id(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    video1, _ = _seed_data(workspace, db_mod, loop)
    # 正式导出按 shot.id 回源本地母版(assets.path),覆盖 spec 里的代理/占位 src,不做权利拦截。
    out = loop.resolve_master_sources({"shots": [
        {"id": "asset001-0", "src": "/tmp/proxy.mp4"},
        {"id": "unknown-shot", "src": "/tmp/keep.mp4"},
    ]})
    assert out["shots"][0]["src"] == str(video1)   # 已知镜头回源到母版
    assert out["shots"][1]["src"] == "/tmp/keep.mp4"  # 未知镜头保持原 src


def test_select_missing_variant_keeps_selection(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    blueprints = loop.generate_blueprints("demo")
    good_id = blueprints["variants"][0]["id"]
    loop.select_variant("demo", good_id)
    with pytest.raises(ValueError):
        loop.select_variant("demo", 999999)
    variants = {item["id"]: item for item in loop.list_variants("demo")}
    assert variants[good_id]["selected"] == 1  # 传入错误 ID 未清除现有选中状态


def test_final_timeline_has_no_gaps(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    blueprints = loop.generate_blueprints("demo")
    blueprint_spec = json.loads(Path(blueprints["variants"][0]["editspec_path"]).read_text())
    selected = loop.select_variant("demo", blueprints["variants"][0]["id"])
    final_spec = json.loads(Path(selected["final_editspec_path"]).read_text())
    cursor = 0
    for shot in final_spec["shots"]:
        assert shot["start_frame"] == cursor  # 无黑场空洞
        cursor += shot["duration_in_frames"]
    assert final_spec["duration_in_frames"] == cursor
    # 未做审片改动时,Final 时长应等于 Blueprint 计划,而不是被撑到整段源(不越 Trim/不扩展)。
    assert final_spec["duration_in_frames"] == blueprint_spec["duration_in_frames"]


def test_final_shortfall_after_rejects_fails(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    blueprints = loop.generate_blueprints("demo")
    # 拒绝一个被大量复用的镜头 → Final 明显短于计划 → 必须报错而非静默交付短片。
    loop.put_review("demo", "asset001-1", {"decision": "reject", "reasons": ["boring"]})
    with pytest.raises(ValueError):
        loop.select_variant("demo", blueprints["variants"][0]["id"])


def test_patch_trim_in_then_out_keeps_both(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    # 模拟审片:先按 I 只设入点,再按 O 只设出点;两次都不能清掉对方。
    loop.patch_trim("demo", "asset001-0", trim_start_sec=0.2, trim_end_sec=None)
    loop.patch_trim("demo", "asset001-0", trim_start_sec=None, trim_end_sec=0.5)
    review = next(item for item in loop.list_reviews("demo") if item["shot_id"] == "asset001-0")
    assert review["trim_start_sec"] == 0.2
    assert review["trim_end_sec"] == 0.5


def test_slowmo_smooth_spec_uses_memory_object(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _reload_modules(monkeypatch, workspace)
    import anime.slowmo as slowmo
    importlib.reload(slowmo)
    # 内存中的 spec(含一个刚重解析出的母版路径)应被原样处理:无慢镜时不落盘、不改 src。
    spec = {"id": "demo", "fps": 30, "width": 100, "height": 100, "duration_in_frames": 10,
            "shots": [{"id": "asset001-0", "src": "/trusted/master.mp4", "source_in_sec": 0.0,
                       "start_frame": 0, "duration_in_frames": 10, "speed": 1.0}]}
    out = slowmo.smooth_spec(spec)
    assert out["shots"][0]["src"] == "/trusted/master.mp4"  # 不从磁盘重读,保留内存路径
    assert out is not spec  # 返回副本


def test_slowmo_smooth_spec_feeds_memory_master_to_rife(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _reload_modules(monkeypatch, workspace)
    import anime.slowmo as slowmo
    importlib.reload(slowmo)
    captured = {}

    def fake_rife(src, in_sec, shown, out_frames, out_fps):
        captured["src"] = src
        return "/cache/slow_clip.mov"

    monkeypatch.setattr(slowmo, "_rife_slowmo", fake_rife)
    # speed<1 进入 RIFE 慢动作分支,应对内存 spec 里的母版路径插帧,而非从磁盘重读旧文件。
    spec = {"id": "demo", "fps": 30, "width": 100, "height": 100, "duration_in_frames": 30,
            "shots": [{"id": "asset001-0", "src": "/trusted/master.mp4", "source_in_sec": 0.5,
                       "start_frame": 0, "duration_in_frames": 30, "speed": 0.5}]}
    out = slowmo.smooth_spec(spec)
    assert captured["src"] == "/trusted/master.mp4"
    assert out["shots"][0]["src"] == "/cache/slow_clip.mov"
    assert out["shots"][0]["speed"] == 1.0


def test_select_variant_not_blocked_by_pool_rights(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    blueprints = loop.generate_blueprints("demo")
    # 池里混入一个 blocked 素材:门禁已移除,选终版不应因此被阻断。
    loop.attach_assets("demo", ["asset002"])
    selected = loop.select_variant("demo", blueprints["variants"][0]["id"])
    assert selected["shots"] >= 1


def test_final_trim_is_boundary_not_expansion(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    blueprints = loop.generate_blueprints("demo")
    # 蓝图生成后轻微收窄 asset001-0 入出点:0.0–0.667s → 可用 20 帧(<10% 缺口,不触发重生成)。
    loop.patch_trim("demo", "asset001-0", 0.0, 0.667)
    selected = loop.select_variant("demo", blueprints["variants"][0]["id"])
    final_spec = json.loads(Path(selected["final_editspec_path"]).read_text())
    trimmed = [shot for shot in final_spec["shots"] if shot["id"] == "asset001-0"]
    assert trimmed, "asset001-0 应出现在 final"
    # Trim 是素材边界:每次出现的时长都被 20 帧封顶,且用最新入点。
    assert all(shot["duration_in_frames"] <= 20 for shot in trimmed)
    assert max(shot["duration_in_frames"] for shot in trimmed) == 20
    assert all(abs(shot["source_in_sec"] - 0.0) < 1e-6 for shot in trimmed)


def test_blueprint_fails_when_duration_unfillable(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    # 目标 600s 但只有 3 个 ~0.8s 镜头,即使复用也远填不满 → 应报错而非静默输出短片。
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 600, "aspect_ratio": "4:5"})
    with pytest.raises(ValueError):
        loop.generate_blueprints("demo")


def test_api_value_error_returns_400(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("fastapi")
    _, db_mod, loop, _, _, _ = _reload_modules(monkeypatch, workspace)
    _seed_data(workspace, db_mod, loop)
    loop.upsert_brief("demo", {"character_query": "gojo", "duration_sec": 24, "aspect_ratio": "4:5"})
    from fastapi.testclient import TestClient
    from anime.review_api import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    # trim 越界 → 业务错误应为 400 而非 500。
    resp = client.patch("/api/projects/demo/shots/asset001-0/trim", json={"trim_start_sec": 5.0, "trim_end_sec": 6.0})
    assert resp.status_code == 400
    assert "detail" in resp.json()
    # 选择不存在的 variant → 400。
    assert client.post("/api/projects/demo/variants/999999/select", json={"selected": True}).status_code == 400
    # 非法 decision 枚举 → pydantic 422。
    assert client.put("/api/projects/demo/shots/asset001-0/review", json={"decision": "bogus"}).status_code == 422


def test_api_config_and_candidates_ignore_rights(workspace: Path, monkeypatch: pytest.MonkeyPatch):
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
    listed = candidates.list_candidates(min_height=0, limit=10)
    candidate_map = {item["id"]: item for item in listed}
    # 来源状态仍作展示字段返回,但不再影响 status / score。
    assert candidate_map["asset001"]["source_status"] == "approved"
    assert candidate_map["asset002"]["source_status"] == "blocked"
    assert candidate_map["asset002"]["status"] != "reject"  # blocked 不再硬拒
    # 两条技术属性相同的素材(short_edge 360、30fps、无字幕)得分相同——版权不参与打分。
    assert candidate_map["asset001"]["score"] == candidate_map["asset002"]["score"]

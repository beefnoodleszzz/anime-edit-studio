"""anime —— AI 剪辑总监层命令行入口。所有命令支持 --json,便于 AI 解析。"""
from __future__ import annotations

import json as _json
from pathlib import Path

import typer
from rich import print as rprint

app = typer.Typer(add_completion=False, help="私有 AI 动漫剪辑导演工作站")


def _out(data, as_json: bool):
    if as_json:
        typer.echo(_json.dumps(data, ensure_ascii=False, indent=2))
    else:
        rprint(data)


@app.command("source")
def source_cmd(query: str, platform: str = "youtube",
               n: int = typer.Option(10, "-n", "--limit"),
               json: bool = typer.Option(False, "--json")):
    """快搜网络素材候选(YouTube via yt-dlp)。登录态/B站/小红书等主路径请用 agent-reach 技能拿 URL 再 fetch。"""
    from . import acquire
    _out(acquire.source(query, platform=platform, n=n), json)


@app.command("fetch")
def fetch_cmd(url: str, no_ingest: bool = typer.Option(False, "--no-ingest"),
              max_height: int = 1080, min_height: int = typer.Option(1080, "--min-height"),
              json: bool = typer.Option(False, "--json")):
    """下载网络素材 → 自动入库；默认只接受 1080p+。URL 可来自 agent-reach。"""
    from . import acquire
    _out(acquire.fetch(url, ingest=not no_ingest, max_height=max_height,
                       min_height=min_height), json)


@app.command("ingest")
def ingest_cmd(path: str, json: bool = typer.Option(False, "--json")):
    """导入本地视频:哈希 + ffprobe + 1080p 代理。"""
    from . import ingest
    _out(ingest.ingest(path), json)


@app.command("shots")
def shots_cmd(asset_id: str, threshold: float = 27.0, force: bool = False,
              json: bool = typer.Option(False, "--json")):
    """分镜 + 关键帧 + contact sheet(已分镜则跳过,--force 重算)。"""
    from . import shots
    res = shots.detect(asset_id, threshold=threshold, force=force)
    _out({"asset_id": asset_id, "shots": len(res), "detail": res}, json)


@app.command("analyze")
def analyze_cmd(asset_id: str, force: bool = False, json: bool = typer.Option(False, "--json")):
    """逐镜头亮度/清晰度/运动方向(已分析则跳过,--force 重算)。"""
    from . import analyze
    res = analyze.analyze(asset_id, force=force)
    _out({"asset_id": asset_id, "analyzed": len(res), "detail": res}, json)


@app.command("embed")
def embed_cmd(asset_id: str, force: bool = False, json: bool = typer.Option(False, "--json")):
    """CLIP 语义向量化镜头关键帧(已向量化则跳过,--force 重算)。"""
    from . import embed
    n = embed.embed_asset(asset_id, force=force)
    _out({"asset_id": asset_id, "embedded": n}, json)


@app.command("pick")
def pick_cmd(shot_ids: str, json: bool = typer.Option(False, "--json")):
    """偏好学习:记录采用的镜头(逗号分隔 shot id),回灌检索排序。"""
    from . import db
    ids = [s for s in shot_ids.split(",") if s]
    conn = db.connect()
    for sid in ids:
        conn.execute("UPDATE shots SET picked=COALESCE(picked,0)+1 WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    _out({"picked": ids, "count": len(ids)}, json)


@app.command("tag")
def tag_cmd(asset_id: str, force: bool = False, json: bool = typer.Option(False, "--json")):
    """WD-Tagger 动漫打标:角色名 + 属性标签 → 检索/导演(已打标则跳过,--force 重算)。"""
    from . import tag
    _out({"asset_id": asset_id, "tagged": tag.tag_asset(asset_id, force=force)}, json)


@app.command("search")
def search_cmd(query: str = typer.Argument(""), asset_id: str = typer.Option(None, "--asset"),
               slot: str = typer.Option(None), min_sharpness: float = 0.0,
               min_motion: float = 0.0, min_source_height: int = typer.Option(1080, "--min-source-height"),
               limit: int = 20, json: bool = typer.Option(False, "--json")):
    """检索镜头 → 带时间码候选（默认仅短边≥1080的源）。"""
    from . import search
    res = search.search(query, asset_id=asset_id, slot=slot, min_sharpness=min_sharpness,
                        min_motion=min_motion, min_source_height=min_source_height, limit=limit)
    _out(res, json)


@app.command("assemble")
def assemble_cmd(project_id: str, query: str = "", asset_id: str = typer.Option(None, "--asset"), limit: int = 12,
                 shot_frames: int = 45, json: bool = typer.Option(False, "--json")):
    """把检索候选拼成一条 EditSpec 并存盘(M1 手工装配起点)。"""
    from . import editspec, search
    cands = search.search(query, asset_id=asset_id, limit=limit)
    spec = editspec.from_candidates(project_id, cands, shot_frames=shot_frames)
    path = editspec.save(spec)
    _out({"project": project_id, "editspec": str(path), "shots": len(spec.shots),
          "duration_in_frames": spec.duration_in_frames}, json)


@app.command("beat")
def beat_cmd(audio: str, project: str = typer.Option(None),
             mult: int = typer.Option(1, "--mult", help="拍网格倍频细分(快曲 Nightcore/phonk 传 2,修 half-tempo 误判)"),
             json: bool = typer.Option(False, "--json")):
    """节拍分析:BPM / beat / downbeat / onset。"""
    from . import beat
    bm = beat.analyze(audio, beat_mult=mult)
    if project:
        bm["saved"] = str(beat.save(project, bm))
    _out({k: v for k, v in bm.items() if k != "onsets"} |
         {"beats_n": len(bm["beats"]), "onsets_n": len(bm["onsets"])}, json)


@app.command("roughcut")
def roughcut_cmd(project_id: str, audio: str = typer.Option(..., "--audio"),
                 query: str = typer.Option("", "--query"), limit: int = 16,
                 fps: int = 60, json: bool = typer.Option(False, "--json")):
    """节拍 + 检索候选 → 生成节奏/情绪/视觉流三版粗剪 EditSpec。"""
    from . import beat, roughcut, search
    bm = beat.analyze(audio)
    beat.save(project_id, bm)
    cands = search.search(query, limit=limit)
    result = roughcut.generate(project_id, bm, cands, fps=fps)
    _out({"project": project_id, "bpm": bm["bpm"], "candidates": len(cands),
          "variants": result}, json)


@app.command("slots")
def slots_cmd(asset_id: str, force: bool = False, json: bool = typer.Option(False, "--json")):
    """CLIP z-score 相对打分把镜头分类到 opening/build/climax/ending(已分类则跳过,--force 重算)。"""
    from . import director
    _out({"asset_id": asset_id, "classified": director.classify_slots(asset_id, force=force)}, json)


@app.command("direct")
def direct_cmd(project_id: str, audio: str = typer.Option(..., "--audio"),
               query: str = typer.Option("", "--query"), asset_id: str = typer.Option(None, "--asset"),
               must_tag: str = typer.Option(None, "--must-tag"),
               exclude: str = typer.Option("", "--exclude", help="剔除的 shot id(逗号分隔;审片去字幕/OP·ED/跑题镜头)"),
               start_s: float = typer.Option(0.0, "--start"), duration_s: float = typer.Option(None, "--duration"),
               mode: str = typer.Option("arc", "--mode", help="arc=情绪弧(钩子→高潮→收束) | showcase=混剪(闪切开场+恒定锁拍+每刀转场)"),
               fill: str = typer.Option("crop", "--fill", help="默认主体跟踪裁切 | auto=按素材决定 | fit_blur=完整画面"),
               canvas: str = typer.Option("4x5", "--canvas", help="默认4x5=3072x3840真4K | auto | landscape | portrait | square"),
               width: int = typer.Option(None, "--width"),
               height: int = typer.Option(None, "--height"),
               fps: int = 60,
               beat_mult: int = typer.Option(1, "--beat-mult", help="拍网格倍频(快曲 Nightcore/phonk 传 2,修 librosa half-tempo 误判致切点太稀)"),
               hook: str = typer.Option("", "--hook", help="showcase 前3秒钩子文案(压在冷开场王炸帧上,大字)"),
               hook_sub: str = typer.Option("", "--hook-sub", help="钩子副行(角色名/tagline,小字)"),
               json: bool = typer.Option(False, "--json")):
    """AI 导演编排 → editspec.arc.json。--mode arc 情绪弧 / --mode showcase 高速混剪。"""
    from . import beat, director
    bm = beat.analyze(audio, beat_mult=beat_mult)
    beat.save(project_id, bm)
    _out({"project": project_id, "bpm": bm["bpm"],
          **director.direct(project_id, bm, query, asset_id=asset_id, must_tag=must_tag,
                            exclude_ids={s for s in exclude.split(",") if s},
                            start_s=start_s, duration_s=duration_s, fps=fps, mode=mode, fill=fill,
                            canvas=canvas, width=width, height=height,
                            hook_text=hook or None, hook_sub=hook_sub)}, json)


@app.command("slowmo")
def slowmo_cmd(editspec_path: str, json: bool = typer.Option(False, "--json")):
    """对慢镜 RIFE 光流插帧,生成顺滑慢动作版 EditSpec(.smooth.json)。"""
    from . import slowmo
    _out(slowmo.smooth(editspec_path), json)


@app.command("superres")
def superres_cmd(editspec_path: str, shots: str = typer.Option("", "--shots"),
                 json: bool = typer.Option(False, "--json")):
    """Real-ESRGAN 真超分(选择性):--shots 传逗号分隔 shot id,空则全部。"""
    from . import superres
    ids = [s for s in shots.split(",") if s] or None
    _out(superres.upscale_editspec(editspec_path, only_ids=ids), json)


@app.command("restore")
def restore_cmd(editspec_path: str, shots: str = typer.Option("", "--shots"),
                json: bool = typer.Option(False, "--json")):
    """轻度去块/时域降噪/保边锐化，供 RIFE 与超分之前使用。"""
    from . import restore
    ids = [s for s in shots.split(",") if s] or None
    _out(restore.restore_editspec(editspec_path, only_ids=ids), json)


@app.command("relink")
def relink_cmd(editspec_path: str,
               source: list[str] = typer.Option(..., "--source", help="source-id=/absolute/path，可重复"),
               json: bool = typer.Option(False, "--json")):
    """将已批准的剪辑规格重连到更高质量的本地源文件。"""
    from . import relink
    _out(relink.relink_editspec(editspec_path, source), json)


@app.command("fps")
def fps_cmd(editspec_path: str, target_fps: int = typer.Argument(...),
            json: bool = typer.Option(False, "--json")):
    """将已批准的剪辑改为整数倍交付帧率，时长与所有切点保持不变。"""
    from . import fps
    _out(fps.retarget(editspec_path, target_fps), json)


@app.command("sideways")
def sideways_cmd(editspec_path: str, direction: str = typer.Option("cw", "--direction"),
                 overscan: float = typer.Option(1.0, "--overscan"),
                 shift_x: float = typer.Option(0.0, "--shift-x"),
                 shift_y: float = typer.Option(0.0, "--shift-y"),
                 width: int = typer.Option(2160, "--width"),
                 height: int = typer.Option(3840, "--height"),
                 json: bool = typer.Option(False, "--json")):
    """16:9 素材先旋转后铺满 9:16；overscan 仅用于已授权的边角水印。"""
    from . import sideways
    _out(sideways.create(editspec_path, direction=direction, overscan=overscan,
                          shift_x=shift_x, shift_y=shift_y, width=width, height=height), json)


@app.command("interpolate")
def interpolate_cmd(editspec_path: str, shots: str = typer.Option("", "--shots"),
                    target_fps: float = typer.Option(60.0, "--target-fps"),
                    json: bool = typer.Option(False, "--json")):
    """RIFE 给指定/全部 EditSpec 镜头补帧，保持剪辑时长和节拍不变。"""
    from . import interpolate
    ids = [s for s in shots.split(",") if s] or None
    _out(interpolate.interpolate_editspec(
        editspec_path, only_ids=ids, target_fps=target_fps), json)


@app.command("render")
def render_cmd(editspec_path: str, out: str = typer.Option(None),
               preview: bool = typer.Option(False, "--preview"),
               draft: bool = typer.Option(False, "--draft"),
               json: bool = typer.Option(False, "--json")):
    """渲染 EditSpec。三档由快到精:

    --draft   纯 ffmpeg 代理低分辨率,免 Remotion/webpack/40GB 临时件——最快,判断选片+节奏+裁切。
    --preview Remotion 0.5 缩放,能看特效/转场。
    (默认)  Remotion 全分辨率正式导出,按 shot.id 回源本地母版。
    """
    from . import render
    if draft:
        _out({"output": render.draft(editspec_path, out=out), "mode": "draft"}, json)
    else:
        _out({"output": render.render(editspec_path, out=out, preview=preview),
              "mode": "preview" if preview else "final"}, json)


@app.command("finalize")
def finalize_cmd(editspec_path: str,
                 shots: str = typer.Option("", "--shots", help="只增强这些 shot id(逗号分隔),空=全镜头"),
                 target_fps: float = typer.Option(60.0, "--target-fps"),
                 endcard: bool = typer.Option(False, "--endcard", help="平台版尾部叠加 END 卡(混剪硬收尾)"),
                 no_clean: bool = typer.Option(False, "--no-clean", help="保留渲染缓存,不回收磁盘"),
                 quality_gate: bool = typer.Option(True, "--quality-gate/--no-quality-gate"),
                 json: bool = typer.Option(False, "--json")):
    """锁定后一键出终版:restore→RIFE60→超分→4K 渲染→声音→母带→(endcard)→QA→清理。

    仅在 draft/preview 确认镜头选取与合成效果满意、cut 锁定后运行。
    """
    from . import finalize
    ids = [s for s in shots.split(",") if s] or None
    _out(finalize.finalize(editspec_path, shots=ids, target_fps=target_fps,
                           endcard=endcard, clean=not no_clean,
                           quality_gate_enabled=quality_gate), json)


@app.command("matte")
def matte_cmd(shot_id: str, json: bool = typer.Option(False, "--json")):
    """rembg(isnet-anime)生成镜头主体遮罩。"""
    from . import matte
    _out({"shot_id": shot_id, "matte": matte.matte_shot(shot_id)}, json)


@app.command("reframe")
def reframe_cmd(asset_id: str, width: int = typer.Option(3072, "--width"),
                height: int = typer.Option(3840, "--height"),
                json: bool = typer.Option(False, "--json")):
    """主体感知4:5装帧；默认 3072×3840，按主体重构图。"""
    from . import reframe
    _out({"asset_id": asset_id, **reframe.reframe_asset(
        asset_id, target_width=width, target_height=height)}, json)


@app.command("enhance")
def enhance_cmd(src: str, upscale: bool = False, interp: bool = False, mult: int = 2,
                json: bool = typer.Option(False, "--json")):
    """选择性:RIFE 补帧 / Real-ESRGAN 超分。"""
    from . import enhance
    result = {"src": src}
    if upscale:
        result["upscaled"] = enhance.upscale(src)
    if interp:
        result["interpolated"] = enhance.interpolate(src, mult=mult)
    _out(result, json)


@app.command("sound")
def sound_cmd(
    editspec_path: str,
    source_audio: bool = typer.Option(True, "--source-audio/--no-source-audio"),
    source_gain: float = typer.Option(.28, "--source-gain"),
    json: bool = typer.Option(False, "--json"),
):
    """分层声音设计:BGM + SFX + 可控原声床 + ducking。"""
    from . import sound
    _out({"output": sound.build(editspec_path, include_source_audio=source_audio,
                                source_gain=source_gain),
          "capabilities": sound.capabilities()}, json)


@app.command("master")
def master_cmd(path: str, json: bool = typer.Option(False, "--json")):
    """母带:loudnorm -10 LUFS + 保持原画幅的平台版导出。"""
    from . import master
    _out(master.master(path), json)


@app.command("endcard")
def endcard_cmd(path: str, text: str = typer.Option("END", "--text"),
                hold: float = typer.Option(2.5, "--hold"),
                out: str = typer.Option(None, "--out"),
                json: bool = typer.Option(False, "--json")):
    """尾部叠加渐显 END 卡 + 压暗(混剪硬收尾;套在 master/platform 成片上)。"""
    from . import endcard
    _out(endcard.add(path, text=text, hold=hold, out=out), json)


@app.command("qa")
def qa_cmd(path: str, width: int = typer.Option(None, "--width"),
           height: int = typer.Option(None, "--height"),
           silent: bool = typer.Option(False, "--silent", help="期望交付文件不含音轨"),
           json: bool = typer.Option(False, "--json")):
    """成片自动 QA → report.json。"""
    from . import qa
    _out(qa.qa(path, expected_width=width, expected_height=height,
               expected_audio=not silent), json)


@app.command("rights")
def rights_cmd(asset_id: str, source: str = typer.Option(None), license: str = typer.Option(None),
               notes: str = typer.Option(None), commercial: bool = typer.Option(None),
               json: bool = typer.Option(False, "--json")):
    """登记/查看素材版权来源(不传设置项则查看)。"""
    from . import rights
    if any(v is not None for v in (source, license, notes, commercial)):
        r = rights.set_rights(asset_id, source=source, license=license, notes=notes, commercial=commercial)
    else:
        r = rights.get_rights(asset_id)
    _out({"asset_id": asset_id, "rights": r}, json)


@app.command("candidates")
def candidates_cmd(output: str = typer.Option(None, "--output"), min_height: int = typer.Option(1080, "--min-height"), limit: int = typer.Option(100, "--limit"), json: bool = typer.Option(False, "--json")):
    """扫描素材库，输出可用/待复核/淘汰候选及重复风险。"""
    from . import candidates, config
    out = output or str(config.ROOT / "review-web" / "public" / "data" / "material-candidates.json")
    _out(candidates.export_json(out, min_height=min_height, limit=limit), json)


brief_app = typer.Typer(add_completion=False, help="creative brief")
review_app = typer.Typer(add_completion=False, help="review workstation")
preference_app = typer.Typer(add_completion=False, help="preference learning")
variant_app = typer.Typer(add_completion=False, help="cut variants")
reference_app = typer.Typer(add_completion=False, help="reference dna")
project_app = typer.Typer(add_completion=False, help="project asset scope")
library_app = typer.Typer(add_completion=False, help="本地素材库入库与磁盘回收")
experiment_app = typer.Typer(add_completion=False, help="Hook A/B 与发布数据回灌")
quality_app = typer.Typer(add_completion=False, help="导演结构与增强 A/B 质量门禁")
extreme_app = typer.Typer(add_completion=False, help="极致线能力与发布就绪门禁")
app.add_typer(brief_app, name="brief")
app.add_typer(review_app, name="review")
app.add_typer(preference_app, name="preference")
app.add_typer(variant_app, name="variant")
app.add_typer(reference_app, name="reference")
app.add_typer(project_app, name="project")
app.add_typer(library_app, name="library")
app.add_typer(experiment_app, name="experiment")
app.add_typer(quality_app, name="quality")
app.add_typer(extreme_app, name="extreme")


@experiment_app.command("create")
def experiment_create_cmd(
    project_id: str,
    name: str,
    editspec_path: str,
    hook: list[str] = typer.Option(..., "--hook", help="候选 Hook；至少重复传两次"),
    hook_sub: list[str] = typer.Option([], "--hook-sub", help="与 Hook 同序的副行"),
    platform: str = typer.Option("douyin", "--platform"),
    duration: float = typer.Option(2.8, "--duration"),
    json: bool = typer.Option(False, "--json"),
):
    """由同一正文生成只改变开头文案的可归因 A/B EditSpec。"""
    from . import experiment
    _out(experiment.create(project_id, name, editspec_path, hook,
                           subs=hook_sub, platform=platform,
                           duration_sec=duration), json)


@experiment_app.command("matrix")
def experiment_matrix_cmd(
    project_id: str,
    name: str,
    editspec_path: str,
    factors_json: str = typer.Option(..., "--factors-json",
                                     help="JSON 数组；可含 hook/sub/hook_shot_id/audio_offset_frames/style"),
    platform: str = typer.Option("douyin", "--platform"),
    duration: float = typer.Option(2.8, "--duration"),
    json: bool = typer.Option(False, "--json"),
):
    """生成文字×首镜×音乐入点的受控多维实验。"""
    from . import experiment
    factors = _json.loads(Path(factors_json).read_text())
    if not isinstance(factors, list):
        raise typer.BadParameter("factors-json 顶层必须是数组")
    _out(experiment.create_matrix(project_id, name, editspec_path, factors,
                                  platform=platform, duration_sec=duration), json)


@experiment_app.command("record")
def experiment_record_cmd(
    variant_id: int,
    views: int = typer.Option(..., "--views"),
    likes: int = typer.Option(0, "--likes"),
    comments: int = typer.Option(0, "--comments"),
    shares: int = typer.Option(0, "--shares"),
    follows: int = typer.Option(0, "--follows"),
    retention_2s: float = typer.Option(None, "--retention-2s"),
    retention_3s: float = typer.Option(None, "--retention-3s"),
    completion_rate: float = typer.Option(None, "--completion-rate"),
    avg_watch_sec: float = typer.Option(None, "--avg-watch-sec"),
    published_at: str = typer.Option(None, "--published-at"),
    retention_curve_json: str = typer.Option(None, "--retention-curve-json",
                                             help='JSON 数组:[{"second":0,"rate":1},…]'),
    external_post_id: str = typer.Option(None, "--external-post-id"),
    json: bool = typer.Option(False, "--json"),
):
    """录入平台真实表现；比例统一使用 0..1。"""
    from . import experiment
    curve = (_json.loads(Path(retention_curve_json).read_text())
             if retention_curve_json else None)
    _out(experiment.record(
        variant_id, views=views, likes=likes, comments=comments,
        shares=shares, follows=follows, retention_2s=retention_2s,
        retention_3s=retention_3s, completion_rate=completion_rate,
        avg_watch_sec=avg_watch_sec, published_at=published_at,
        retention_curve=curve, external_post_id=external_post_id,
    ), json)


@experiment_app.command("report")
def experiment_report_cmd(
    project_id: str,
    name: str,
    json: bool = typer.Option(False, "--json"),
):
    """按留存优先的置信加权分数报告结果；样本不足时不会伪报赢家。"""
    from . import experiment
    _out(experiment.report(project_id, name), json)


@experiment_app.command("learn")
def experiment_learn_cmd(
    project_id: str = typer.Option(None, "--project"),
    json: bool = typer.Option(False, "--json"),
):
    """把足量样本的镜头留存表现回灌 growth_score。"""
    from . import experiment
    _out(experiment.learn(project_id), json)


@experiment_app.command("import")
def experiment_import_cmd(
    project_id: str,
    name: str,
    csv_path: str,
    json: bool = typer.Option(False, "--json"),
):
    """导入平台授权导出的标准 CSV，不接管账号凭据。"""
    from . import experiment
    _out(experiment.import_metrics(project_id, name, csv_path), json)


@experiment_app.command("insights")
def experiment_insights_cmd(
    project_id: str = typer.Option(None, "--project"),
    json: bool = typer.Option(False, "--json"),
):
    """聚合角色/Hook/首镜/音乐入点等因子的真实表现。"""
    from . import experiment
    _out(experiment.insights(project_id), json)


@quality_app.command("audit")
def quality_audit_cmd(
    editspec_path: str,
    visual: bool = typer.Option(False, "--visual", help="抽取中点帧做近重复指纹检测"),
    json: bool = typer.Option(False, "--json"),
):
    """检查交付规格、素材多样性、runway、字幕风险、重复与连续性。"""
    from . import quality_gate
    _out(quality_gate.audit(editspec_path, visual=visual), json)


@quality_app.command("compare")
def quality_compare_cmd(
    project_id: str,
    shot_id: str,
    stage: str,
    source_path: str,
    processed_path: str,
    json: bool = typer.Option(False, "--json"),
):
    """建立增强前后逐镜 A/B 指标并进入人工决策队列。"""
    from . import quality_gate
    _out(quality_gate.compare(project_id, shot_id, stage,
                              source_path, processed_path), json)


@quality_app.command("decide")
def quality_decide_cmd(
    review_id: int,
    decision: str,
    notes: str = typer.Option(None, "--notes"),
    json: bool = typer.Option(False, "--json"),
):
    """接受或拒绝一项增强结果。"""
    from . import quality_gate
    _out(quality_gate.decide(review_id, decision, notes), json)


@quality_app.command("status")
def quality_status_cmd(
    project_id: str,
    json: bool = typer.Option(False, "--json"),
):
    """显示项目增强门禁是否全部批准。"""
    from . import quality_gate
    _out(quality_gate.status(project_id), json)


@extreme_app.command("capabilities")
def extreme_capabilities_cmd(json: bool = typer.Option(False, "--json")):
    """显示高级能力、真实可用状态与安全降级。"""
    from . import extreme
    _out(extreme.capabilities(), json)


@extreme_app.command("status")
def extreme_status_cmd(
    project_id: str,
    editspec_path: str = typer.Option(None, "--editspec"),
    json: bool = typer.Option(False, "--json"),
):
    """聚合结构、增强审批、发布实验和工具链的最终发布门禁。"""
    from . import extreme
    _out(extreme.status(project_id, editspec_path), json)


@library_app.command("add")
def library_add_cmd(
    video: str,
    series: str = typer.Option(..., "--series", help="系列 slug(小写 ASCII,如 frieren)"),
    season: str = typer.Option(..., "--season", help="季/来源 slug(如 s2、movie1)"),
    kind: str = typer.Option(..., "--kind", help="类型: ep/ncop/nced/op/ed/pv/cm/clip(可带序号 ep05)"),
    tier: str = typer.Option(..., "--tier", help="质量层: bd/raw/web/clip"),
    source_url: str = typer.Option(None, "--source-url"),
    title: str = typer.Option(None, "--title"),
    creator: str = typer.Option(None, "--creator"),
    notes: str = typer.Option(None, "--notes"),
    copy: bool = typer.Option(False, "--copy", help="复制而非移动(默认移动)"),
    do_ingest: bool = typer.Option(False, "--ingest", help="归位后立即 ingest 并登记来源"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的同名目标"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只预览规范化命名与落点,不落盘"),
    json: bool = typer.Option(False, "--json"),
):
    """把一个视频探测→规范化 ASCII 命名→归位到素材库 sources/<series>/<season>/。"""
    from . import library

    _out(library.add(video, series=series, season=season, kind=kind, tier=tier,
                     source_url=source_url, title=title, creator=creator, notes=notes,
                     copy=copy, do_ingest=do_ingest, force=force, dry_run=dry_run), json)


@library_app.command("clean")
def library_clean_cmd(
    project_id: str = typer.Argument(None, help="可选:一并清理该项目的中间件/预览"),
    apply: bool = typer.Option(False, "--apply", help="真正删除(默认只报告可回收空间)"),
    json: bool = typer.Option(False, "--json"),
):
    """交付后回收可再生的渲染缓存/中间件;保留源、母版、engine.sqlite、关键帧。"""
    from . import library

    _out(library.clean(project_id, apply=apply), json)


@brief_app.command("create")
def brief_create_cmd(
    project_id: str,
    character: str = typer.Option(None, "--character"),
    theme: str = typer.Option(None, "--theme"),
    emotion: str = typer.Option("", "--emotion"),
    duration: float = typer.Option(None, "--duration"),
    aspect: str = typer.Option(None, "--aspect"),
    platform: str = typer.Option(None, "--platform"),
    reference: str = typer.Option(None, "--reference"),
    content_lane: str = typer.Option(None, "--content-lane"),
    audience: str = typer.Option(None, "--audience"),
    viewer_promise: str = typer.Option(None, "--viewer-promise"),
    payoff: str = typer.Option(None, "--payoff"),
    ending: str = typer.Option(None, "--ending-aftertaste"),
    edit_mode: str = typer.Option(None, "--edit-mode"),
    visual_motif: str = typer.Option(None, "--visual-motif"),
    sound_strategy: str = typer.Option(None, "--sound-strategy"),
    must_include: str = typer.Option("", "--must-include"),
    must_avoid: str = typer.Option("", "--must-avoid"),
    success_criteria: str = typer.Option(None, "--success-criteria"),
    contract_json: str = typer.Option(None, "--contract-json"),
    from_json: str = typer.Option(None, "--from-json"),
    json: bool = typer.Option(False, "--json"),
):
    """创建或更新创作 brief。"""
    from . import decision_loop

    payload = {}
    if from_json:
        payload = _json.loads(Path(from_json).read_text())
    contract = payload.get("creative_contract_json") or {}
    if contract_json:
        contract.update(_json.loads(Path(contract_json).read_text()))
    contract.update({
        key: value for key, value in {
            "content_lane": content_lane,
            "audience_context": audience,
            "viewer_promise": viewer_promise,
            "payoff": payoff,
            "ending_aftertaste": ending,
            "edit_mode": edit_mode,
            "visual_motif": visual_motif,
            "sound_strategy": sound_strategy,
            "must_include": [item.strip() for item in must_include.split(",") if item.strip()] or None,
            "must_avoid": [item.strip() for item in must_avoid.split(",") if item.strip()] or None,
            "success_criteria": success_criteria,
        }.items() if value is not None
    })
    payload.update(
        {
            "character_query": payload.get("character_query") or character,
            "theme": payload.get("theme") or theme,
            "target_emotions": payload.get("target_emotions") or [item.strip() for item in emotion.split(",") if item.strip()],
            "duration_sec": payload.get("duration_sec") or duration,
            "aspect_ratio": payload.get("aspect_ratio") or aspect,
            "target_platform": payload.get("target_platform") or platform,
            "reference_video_path": payload.get("reference_video_path") or reference,
            "creative_contract_json": contract,
        }
    )
    _out(decision_loop.upsert_brief(project_id, payload), json)


@brief_app.command("validate")
def brief_validate_cmd(
    project_id: str,
    json: bool = typer.Option(False, "--json"),
):
    """验证导演合约是否足以进入素材缺口分析和制作。"""
    from . import decision_loop
    _out(decision_loop.validate_brief(project_id), json)


@app.command("gap")
def gap_cmd(project_id: str, json: bool = typer.Option(False, "--json")):
    """输出素材缺口分析。"""
    from . import decision_loop

    _out(decision_loop.gap_analysis(project_id), json)


@app.command("blueprint")
def blueprint_cmd(project_id: str, json: bool = typer.Option(False, "--json")):
    """生成三版低成本剪辑蓝图。"""
    from . import decision_loop

    _out(decision_loop.generate_blueprints(project_id), json)


@variant_app.command("select")
def variant_select_cmd(project_id: str, variant_id: int, json: bool = typer.Option(False, "--json")):
    """选择最终变体并回灌决策。"""
    from . import decision_loop

    _out(decision_loop.select_variant(project_id, variant_id), json)


@project_app.command("attach")
def project_attach_cmd(project_id: str, asset_ids: str, json: bool = typer.Option(False, "--json")):
    """绑定项目素材池。asset_ids 传逗号分隔。"""
    from . import decision_loop

    ids = [item.strip() for item in asset_ids.split(",") if item.strip()]
    _out(decision_loop.attach_assets(project_id, ids), json)


@review_app.command("serve")
def review_serve_cmd(
    project_id: str,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
):
    """启动本地 Review API。"""
    import uvicorn
    from .review_api import create_app

    app_instance = create_app()
    app_instance.state.default_project_id = project_id
    uvicorn.run(app_instance, host=host, port=port, log_level="info")


@preference_app.command("train")
def preference_train_cmd(json: bool = typer.Option(False, "--json")):
    """训练偏好模型。"""
    from . import decision_loop

    _out(decision_loop.train_preference(), json)


@preference_app.command("explain")
def preference_explain_cmd(shot_id: str, json: bool = typer.Option(False, "--json")):
    """解释指定镜头的偏好分。"""
    from . import decision_loop

    _out(decision_loop.explain_preference(shot_id), json)


@preference_app.command("reset")
def preference_reset_cmd(json: bool = typer.Option(False, "--json")):
    """清空偏好模型。"""
    from . import decision_loop

    _out(decision_loop.reset_preference(), json)


@preference_app.command("rebuild")
def preference_rebuild_cmd(json: bool = typer.Option(False, "--json")):
    """重建偏好模型。"""
    from . import decision_loop

    decision_loop.reset_preference()
    _out(decision_loop.train_preference(), json)


@reference_app.command("analyze")
def reference_analyze_cmd(project_id: str, video: str, json: bool = typer.Option(False, "--json")):
    """提取参考剪辑节奏 DNA。"""
    from . import reference

    _out(reference.analyze_reference(project_id, video), json)


@app.command("source-register")
def source_register_cmd(asset_id: str,
                        source_url: str = typer.Option(None, "--source-url"),
                        creator: str = typer.Option(None, "--creator"),
                        title: str = typer.Option(None, "--title"),
                        license: str = typer.Option(None, "--license"),
                        commercial: bool = typer.Option(None, "--commercial"),
                        status: str = typer.Option("review", "--status"),
                        notes: str = typer.Option(None, "--notes"),
                        json: bool = typer.Option(False, "--json")):
    """登记素材来源:出处/作品/集数/Raw·BD·切片站等,仅作记录。status 只是备注,不影响导出。"""
    from . import decision_loop

    _out(decision_loop.upsert_source_record(asset_id, {
        "source_url": source_url,
        "creator": creator,
        "title": title,
        "license": license,
        "commercial_allowed": commercial,
        "status": status,
        "notes": notes,
    }), json)


@app.command("source-list")
def source_list_cmd(project_id: str = typer.Option(None, "--project"), json: bool = typer.Option(False, "--json")):
    """列出素材来源记录(纯展示,不阻断)。"""
    from . import decision_loop

    _out(decision_loop.audit_sources(project_id), json)


@app.command("source-report")
def source_report_cmd(project_id: str, json: bool = typer.Option(False, "--json")):
    """输出项目素材来源报告(来源/作品/备注,不含导出门禁)。"""
    from . import decision_loop

    _out(decision_loop.source_report(project_id), json)


@app.command("doctor")
def doctor_cmd(json: bool = typer.Option(False, "--json")):
    """检查工具链是否就绪。"""
    from . import aesthetic, config, sound
    names = ["ffmpeg", "ffprobe", "whisper", "realesrgan", "rife", "chrome"]
    status = {n: config.tool_optional(n) for n in names}
    # 美学评分头缺失不算致命:筛选会回退清晰度启发式,但选片质量下降。
    status["aesthetic_model"] = aesthetic.available()
    _out({"tools": status,
          "missing": [n for n, v in status.items() if not v and n != "aesthetic_model"],
          "aesthetic_scoring": "on" if aesthetic.available() else "degraded(清晰度启发式)",
          "audio": sound.capabilities()}, json)


if __name__ == "__main__":
    app()

"""Anime Edit Studio v2 CLI —— 入口命令 `aes`。

设计约束（AGENTS.md）：
- 所有子命令保持 --json 输出能力
- CLI 只做参数解析与结果呈现，不含业务逻辑
"""
from __future__ import annotations

import json as _json
import logging
from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    help="Anime Edit Studio —— AI 导演 / EditSpec IR / DaVinci Resolve 执行",
)

doctor_app = typer.Typer(add_completion=False, help="环境与能力自检")
spec_app = typer.Typer(add_completion=False, help="EditSpec 校验与检查")
resolve_app = typer.Typer(add_completion=False, help="Resolve 构建与渲染")
data_app = typer.Typer(add_completion=False, help="v2 数据库与 ETL")
candidates_app = typer.Typer(add_completion=False, help="候选召回、精排与 A/B/C")
app.add_typer(doctor_app, name="doctor")
app.add_typer(spec_app, name="spec")
app.add_typer(resolve_app, name="resolve")
app.add_typer(data_app, name="data")
app.add_typer(candidates_app, name="candidates")


def out(data, as_json: bool) -> None:
    if as_json:
        typer.echo(_json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(data)


def _load_spec(path: Path):
    from studio.editspec.migrations import load_migrated

    if not path.exists():
        typer.secho(f"EditSpec 不存在: {path}", fg="red", err=True)
        raise typer.Exit(2)
    return load_migrated(_json.loads(path.read_text(encoding="utf-8")))


def _resolver(prefer_master: bool):
    from studio.core.assets import DatabaseResolver
    from studio.core.database import DEFAULT_V2_DB

    return DatabaseResolver(DEFAULT_V2_DB, prefer_proxy=not prefer_master)


def _shot_resolver():
    from studio.core.assets import DatabaseShotResolver
    from studio.core.database import DEFAULT_V2_DB

    return DatabaseShotResolver(DEFAULT_V2_DB)


# ─────────────────────────── 通用 ───────────────────────────

@app.command("version")
def version_cmd(json: bool = typer.Option(False, "--json")):
    """显示版本与当前迁移阶段。"""
    from studio import PHASE, __version__

    out({"version": __version__, "phase": PHASE}, json)


@app.command("search")
def search_cmd(
    text: str | None = typer.Argument(None, help="FTS 查询；可省略只用结构化过滤"),
    character: str | None = typer.Option(None, "--character"),
    action: str | None = typer.Option(None, "--action"),
    motion: str | None = typer.Option(None, "--motion", help="运动方向"),
    min_motion: float | None = typer.Option(None, "--min-motion"),
    subtitle: bool | None = typer.Option(None, "--subtitle/--no-subtitle"),
    min_face: float | None = typer.Option(None, "--min-face"),
    min_pose: float | None = typer.Option(None, "--min-pose"),
    min_energy: float | None = typer.Option(None, "--min-energy"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    json: bool = typer.Option(False, "--json"),
):
    """按角色、动作、运动、字幕和主体质量组合检索 v2 Shot。"""
    from studio.asset_intelligence.indexing import SearchQuery, search_shots
    from studio.core.database import DEFAULT_V2_DB, connect

    query = SearchQuery(
        text=text,
        character=character,
        action=action,
        motion_direction=motion,
        min_motion=min_motion,
        subtitle=subtitle,
        min_face_visibility=min_face,
        min_pose_quality=min_pose,
        min_visual_energy=min_energy,
        limit=limit,
    )
    conn = connect(DEFAULT_V2_DB)
    rows = search_shots(conn, query)
    conn.close()
    if json:
        out({"count": len(rows), "shots": rows}, True)
        return
    for row in rows:
        typer.echo(
            f"{row['id']:24} {row['start_sec']:8.3f}–{row['end_sec']:8.3f} "
            f"motion={row['motion_dir'] or '-'} face={row['face_visibility'] or 0:.2f} "
            f"energy={row['visual_energy'] or 0:.2f}"
        )


@app.command("ingest")
def ingest_cmd(
    source: Path = typer.Argument(..., exists=True, dir_okay=False),
    json: bool = typer.Option(False, "--json"),
):
    """探测媒体、内容寻址、生成代理并登记到 v2 数据库。"""
    from studio.asset_intelligence.ingest import ingest_asset

    result = ingest_asset(source)
    out(result, json) if json else typer.echo(
        f"{result['id']}  {result['width']}x{result['height']}  "
        f"{result['fps']['num']}/{result['fps']['den']}fps\n"
        f"proxy: {result['proxy_path']}"
    )


@app.command("shots")
def shots_cmd(
    asset_id: str = typer.Argument(...),
    threshold: float = typer.Option(27.0, "--threshold"),
    min_scene_sec: float = typer.Option(0.4, "--min-scene-sec"),
    force: bool = typer.Option(False, "--force"),
    json: bool = typer.Option(False, "--json"),
):
    """分镜并为每镜提取 5 个候选代表帧。"""
    from studio.asset_intelligence.shot_detection import detect_shots

    rows = detect_shots(
        asset_id,
        threshold=threshold,
        min_scene_sec=min_scene_sec,
        force=force,
    )
    out({"asset_id": asset_id, "count": len(rows), "shots": rows}, json) if json else (
        typer.echo(f"{asset_id}: {len(rows)} shots")
    )


@app.command("analyze")
def analyze_cmd(
    asset_id: str | None = typer.Argument(None, help="省略则增量分析全部素材"),
    deterministic_only: bool = typer.Option(
        False, "--deterministic-only", help="跳过 CLIP 与 WD Tagger"
    ),
    json: bool = typer.Option(False, "--json"),
):
    """增量运行运动、CLIP、动漫标签、视觉维度与音频分析。"""
    from studio.asset_intelligence.pipeline import analyze_assets

    report = analyze_assets(
        asset_id=asset_id,
        include_models=not deterministic_only,
    )
    out(report, json) if json else typer.echo(_json.dumps(
        report, ensure_ascii=False, indent=2, default=str
    ))


@app.command("review")
def review_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port", min=1, max=65535),
):
    """启动无需 CLI 操作的六步创作工作台。"""
    try:
        import uvicorn
    except ImportError as exc:
        typer.secho('Review UI 依赖未安装: uv pip install -e ".[review]"', fg="red")
        raise typer.Exit(2) from exc
    from studio.review import create_review_app

    typer.echo(f"Anime Edit Studio: http://{host}:{port}")
    uvicorn.run(create_review_app(), host=host, port=port, log_level="info")


@app.command("first-cut")
def first_cut_cmd(
    project_id: str = typer.Argument(...),
    music: Path = typer.Option(..., "--music", exists=True, dir_okay=False),
    duration: float = typer.Option(25.0, "--duration", min=1),
    reference: Path | None = typer.Option(
        None, "--reference", exists=True, dir_okay=False
    ),
    character: list[str] | None = typer.Option(None, "--character"),
    tone: list[str] | None = typer.Option(None, "--tone"),
    naked_cut: bool = typer.Option(
        False,
        "--naked-cut",
        help="只生成硬切与原速镜头，不附加 Recipe、转场或运动短语。",
    ),
    json: bool = typer.Option(False, "--json"),
):
    """一条命令生成 MusicMap、DirectorPlan、候选组和第一版 EditSpec。"""
    from studio.workflows import create_first_cut

    result = create_first_cut(
        project_id=project_id,
        music_path=music,
        duration_sec=duration,
        reference_path=reference,
        primary_characters=character,
        tone=tone,
        naked_cut=naked_cut,
    )
    out(result.model_dump(mode="json"), json) if json else typer.echo(
        f"{result.clip_count} clips / {result.duration_sec:.2f}s\n{result.spec_path}"
    )


# ─────────────────────────── candidates ───────────────────────────

@candidates_app.command("generate")
def candidates_generate_cmd(
    project_id: str = typer.Argument(...),
    role: str = typer.Argument(...),
    character: str | None = typer.Option(None, "--character"),
    action: str | None = typer.Option(None, "--action"),
    target_energy: float = typer.Option(0.5, "--target-energy", min=0, max=1),
    allow_subtitle: bool = typer.Option(False, "--allow-subtitle"),
    review_dir: Path | None = typer.Option(None, "--review-dir"),
    json: bool = typer.Option(False, "--json"),
):
    """数百镜头召回后精排，并持久化一个 A/B/C narrative role 组。"""
    from studio.core.database import connect
    from studio.editing.candidates import create_group, generate_review_assets
    from studio.editing.ranking import CandidateContext, rank_candidates
    from studio.editing.retrieval import RetrievalQuery, retrieve

    conn = connect()
    try:
        shot_ids = retrieve(
            conn,
            RetrievalQuery(
                character=character,
                action=action,
                subtitle_allowed=allow_subtitle,
                limit=200,
            ),
        )
        ranked = rank_candidates(
            conn,
            shot_ids,
            CandidateContext(
                project_id=project_id,
                role=role,
                target_energy=target_energy,
                character=character,
                action=action,
            ),
            limit=50,
        )
        group = create_group(
            conn, project_id=project_id, role=role, ranked=ranked
        )
        result = {
            "retrieved": len(shot_ids),
            "ranked": len(ranked),
            "group": group.model_dump(mode="json"),
        }
        if review_dir:
            result["review"] = generate_review_assets(
                conn, group, output_dir=review_dir
            )
        out(result, json) if json else typer.echo(
            f"{role}: {len(shot_ids)} recalled → {len(ranked)} ranked → "
            f"A/B/C {' / '.join(group.shot_ids)}"
        )
    finally:
        conn.close()


@candidates_app.command("choose")
def candidates_choose_cmd(
    group_id: str = typer.Argument(...),
    shot_id: str = typer.Argument(...),
    style: str | None = typer.Option(None, "--style"),
    json: bool = typer.Option(False, "--json"),
):
    """选择 A/B/C，并写入两个 winner/loser 偏好对。"""
    from studio.core.database import connect
    from studio.editing.candidates import choose_candidate

    conn = connect()
    try:
        group = choose_candidate(
            conn,
            group_id=group_id,
            shot_id=shot_id,
            context={"source": "cli"},
            project_style=style,
        )
        out(group.model_dump(mode="json"), json) if json else typer.echo(
            f"{group.role}: selected {group.selected_shot_id}"
        )
    finally:
        conn.close()


@candidates_app.command("metrics")
def candidates_metrics_cmd(
    project_id: str = typer.Argument(...),
    json: bool = typer.Option(False, "--json"),
):
    """显示可持续计算的 Candidate Precision。"""
    from studio.core.database import connect
    from studio.editing.candidates import precision_metrics

    conn = connect()
    try:
        metrics = precision_metrics(conn, project_id)
        out(metrics, json) if json else typer.echo(metrics)
    finally:
        conn.close()


# ─────────────────────────── doctor ───────────────────────────

@doctor_app.command("env")
def doctor_env_cmd(json: bool = typer.Option(False, "--json")):
    """检查 Python / Resolve / 外部工具是否就绪。"""
    from studio.core.env import check_environment

    report = check_environment()
    if json:
        out(report, True)
        raise typer.Exit(0 if report["ready"] else 1)

    for item in report["checks"]:
        mark = "OK  " if item["ok"] else "FAIL"
        color = "green" if item["ok"] else "red"
        typer.secho(f"[{mark}] {item['name']:24} {item['detail']}", fg=color)
    typer.echo("")
    typer.secho(
        "ready" if report["ready"] else "NOT ready —— 见上方 FAIL 项",
        fg="green" if report["ready"] else "red",
    )
    raise typer.Exit(0 if report["ready"] else 1)


@doctor_app.command("capabilities")
def doctor_capabilities_cmd(json: bool = typer.Option(False, "--json")):
    """显示 Resolve 能力矩阵中已验证 / 待验证 / 未探测的能力。"""
    from studio.core.capabilities import load_capabilities, summarize

    summary = summarize(load_capabilities())
    if json:
        out(summary, True)
        return
    labels = {
        "verified": ("已验证 —— 可用于 EditSpec", "green"),
        "unavailable": ("已实测判定不可用 —— 必须走 fallback", "red"),
        "probed_unverified": ("方法存在但未实测 —— 暂禁使用", "yellow"),
        "unprobed": ("未探测", "bright_black"),
    }
    for bucket, (label, color) in labels.items():
        names = summary[bucket]
        typer.secho(f"\n{label} ({len(names)}):", fg=color, bold=True)
        for name in names:
            fb = summary["fallbacks"].get(name)
            typer.echo(f"  - {name}" + (f"\n      ↳ fallback: {fb}" if fb else ""))


@doctor_app.command("assets")
def doctor_assets_cmd(json: bool = typer.Option(False, "--json")):
    """列出当前可解析的 asset_id。"""
    from studio.core.assets import PROXY_DIR, FilesystemResolver

    ids = FilesystemResolver([PROXY_DIR]).available_ids()
    out({"count": len(ids), "dir": str(PROXY_DIR), "asset_ids": ids}, json) if json else (
        typer.echo(f"{len(ids)} 个可用素材，位于 {PROXY_DIR}\n"
                   + "\n".join(f"  {i}" for i in ids))
    )


# ─────────────────────────── data ───────────────────────────

@data_app.command("status")
def data_status_cmd(json: bool = typer.Option(False, "--json")):
    """显示 v2 数据库关键实体计数。"""
    from studio.core.database import DEFAULT_V2_DB, connect

    conn = connect(DEFAULT_V2_DB)
    tables = (
        "assets", "shots", "characters", "creative_briefs", "project_assets",
        "source_records", "edit_specs", "workflow_states",
    )
    counts = {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in tables
    }
    embeddings = conn.execute("SELECT count(embedding) FROM shots").fetchone()[0]
    conn.close()
    data = {"path": str(DEFAULT_V2_DB), "counts": counts, "embeddings": embeddings}
    if json:
        out(data, True)
    else:
        typer.echo(f"{DEFAULT_V2_DB}")
        for name, count in counts.items():
            typer.echo(f"  {name:20} {count}")
        typer.echo(f"  {'embeddings':20} {embeddings}")


@data_app.command("migrate-v1")
def data_migrate_v1_cmd(
    source: Path | None = typer.Option(None, "--source"),
    target: Path | None = typer.Option(None, "--target"),
    json: bool = typer.Option(False, "--json"),
):
    """从不可变 v1 库创建并逐表验收一个全新 v2 库。"""
    from studio.core.database import (
        DEFAULT_V1_DB,
        DEFAULT_V2_DB,
        migrate_v1,
    )

    try:
        report = migrate_v1(source or DEFAULT_V1_DB, target or DEFAULT_V2_DB)
    except (FileExistsError, ValueError, RuntimeError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    if json:
        out(report.to_dict(), True)
    else:
        typer.secho("ETL PASS", fg="green", bold=True)
        for table, (before, after) in report.counts.items():
            typer.echo(f"  {table:20} {before} → {after}")
        typer.echo(f"  {'embeddings':20} {report.embeddings[0]} → {report.embeddings[1]}")


# ─────────────────────────── spec ───────────────────────────

@spec_app.command("validate")
def spec_validate_cmd(
    path: Path = typer.Argument(..., help="EditSpec JSON 路径"),
    master: bool = typer.Option(False, "--master", help="按母版而非代理解析素材"),
    json: bool = typer.Option(False, "--json"),
):
    """校验 EditSpec（AGENTS.md R2）。"""
    from studio.editspec.validator import validate

    spec = _load_spec(path)
    result = validate(
        spec,
        resolve_asset=_resolver(master),
        resolve_shot=_shot_resolver(),
    )

    if json:
        out(result.to_dict(), True)
        raise typer.Exit(0 if result.ok else 1)

    for issue in result.issues:
        typer.secho(f"  {issue}", fg="red" if issue.severity.value == "error" else "yellow")
    typer.echo("")
    typer.secho(
        f"{'PASS' if result.ok else 'FAIL'} —— "
        f"{len(result.errors)} 错误 / {len(result.warnings)} 警告",
        fg="green" if result.ok else "red",
    )
    raise typer.Exit(0 if result.ok else 1)


@spec_app.command("show")
def spec_show_cmd(
    path: Path = typer.Argument(...),
    json: bool = typer.Option(False, "--json"),
):
    """概览 EditSpec 的结构。"""
    spec = _load_spec(path)
    summary = {
        "id": spec.id,
        "spec_version": spec.spec_version,
        "revision": spec.revision,
        "timebase": f"{spec.timebase.num}/{spec.timebase.den} ({spec.timebase.fps:.3f}fps)",
        "canvas": f"{spec.canvas.width}x{spec.canvas.height}",
        "duration_sec": round(spec.duration_sec, 3),
        "clips": len(spec.clips),
        "tracks": [t.id for t in spec.tracks],
        "markers": len(spec.markers),
    }
    if json:
        out(summary, True)
        return
    for key, value in summary.items():
        typer.echo(f"{key:16} {value}")
    typer.echo("\nclips:")
    for c in spec.clips:
        typer.echo(
            f"  {c.id:14} {c.timeline.in_sec:7.3f}s +{c.timeline.duration_sec:6.3f}s "
            f"{c.timeline.track:3} <- {c.asset_id} @{c.source.in_sec:.3f}s "
            f"[{c.role or '-'}]"
        )


# ─────────────────────────── resolve ───────────────────────────

@resolve_app.command("build")
def resolve_build_cmd(
    path: Path = typer.Argument(..., help="EditSpec JSON 路径"),
    timeline: str = typer.Option("main", "--timeline"),
    incremental: bool = typer.Option(
        False, "--incremental", help="只重建变化的 clip（需已有构建状态）"
    ),
    reset: bool = typer.Option(False, "--reset", help="删除同名工程后重建"),
    master: bool = typer.Option(False, "--master", help="按母版而非代理解析素材"),
    launch: bool = typer.Option(False, "--launch", help="Resolve 未运行时自动启动"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
    json: bool = typer.Option(False, "--json"),
):
    """把 EditSpec 构建进 Resolve 时间线。"""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    from studio.editspec.validator import ValidationError
    from studio.execution.compiler import ResolveCompiler
    from studio.execution.resolve import (
        ResolveAdapter,
        ResolveOperationError,
        ResolveUnavailable,
    )

    spec = _load_spec(path)
    try:
        adapter = ResolveAdapter.open(auto_launch=launch)
        compiler = ResolveCompiler(
            adapter,
            _resolver(master),
            resolve_shot=_shot_resolver(),
            state_dir=path.parent,
        )
        if incremental:
            report = compiler.update(spec, timeline_name=timeline)
        else:
            report = compiler.build(spec, timeline_name=timeline, reset_project=reset)
    except ValidationError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    except (ResolveUnavailable, ResolveOperationError) as exc:
        typer.secho(f"Resolve 错误: {exc}", fg="red", err=True)
        raise typer.Exit(3) from exc

    if json:
        out(report.to_dict(), True)
        return
    typer.secho(
        f"{report.mode} 完成: 工程 {report.project} / 时间线 {report.timeline}",
        fg="green", bold=True,
    )
    typer.echo(
        f"  clip 总数 {report.clips_total} / 变化 {report.clips_changed} / "
        f"未变 {report.clips_unchanged} / 新增 {report.clips_added} / "
        f"移除 {report.clips_removed}"
    )
    typer.echo(f"  时间线写入 {report.clips_written} 个片段，标记 {report.markers_written} 个")
    if report.changed_ranges:
        ranges = ", ".join(f"{a:.2f}–{b:.2f}s" for a, b in report.changed_ranges)
        typer.echo(
            f"  待渲区间 {ranges}"
            f"（共 {report.changed_duration_sec:.2f}s / 全片 {spec.duration_sec:.2f}s）"
        )
    else:
        typer.secho("  无变化，无需渲染", fg="cyan")
    for w in report.warnings:
        typer.secho(f"  警告: {w}", fg="yellow")


@resolve_app.command("info")
def resolve_info_cmd(
    launch: bool = typer.Option(False, "--launch"),
    json: bool = typer.Option(False, "--json"),
):
    """显示 Resolve 当前状态。"""
    from studio.execution.resolve import ResolveUnavailable
    from studio.execution.resolve import connection

    try:
        got = connection.info(auto_launch=launch)
    except ResolveUnavailable as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(3) from exc

    data = {"version": got.version, "page": got.page, "project": got.project}
    if json:
        out(data, True)
        return
    for key, value in data.items():
        typer.echo(f"{key:10} {value}")


def _resolve_render_command(
    path: Path,
    *,
    kind: str,
    output_dir: Path,
    reset: bool,
    master_media: bool,
    launch: bool,
    as_json: bool,
) -> None:
    from fractions import Fraction

    from studio.core.database import connect
    from studio.critic.technical import run_technical_qa
    from studio.execution.compiler import ResolveCompiler
    from studio.execution.render import render_spec
    from studio.execution.resolve import ResolveAdapter

    spec = _load_spec(path)
    adapter = ResolveAdapter.open(auto_launch=launch)
    compiler = ResolveCompiler(
        adapter,
        _resolver(master_media),
        resolve_shot=_shot_resolver(),
        state_dir=path.parent,
    )
    build = compiler.build(spec, reset_project=reset)
    conn = connect()
    try:
        render_id, rendered = render_spec(
            adapter, conn, spec, kind=kind, output_dir=output_dir
        )
        result = {
            "build": build.to_dict(),
            "render_id": render_id,
            "output": str(rendered.output),
            "status": rendered.status,
        }
        if kind == "master":
            expected_freezes = []
            for clip in spec.clips:
                if not clip.shot_id:
                    continue
                row = conn.execute(
                    """
                    SELECT coalesce(subject_motion,motion_mag,1)
                    FROM shots WHERE id=?
                    """,
                    (clip.shot_id,),
                ).fetchone()
                if row is not None and row[0] <= 0.01:
                    padding = 2 / float(spec.timebase.fps)
                    expected_freezes.append(
                        (
                            max(0.0, clip.timeline.in_sec - padding),
                            min(
                                spec.duration_sec,
                                clip.timeline.in_sec
                                + clip.timeline.duration_sec
                                + padding,
                            ),
                        )
                    )
            qa = run_technical_qa(
                rendered.output,
                expected_duration=spec.duration_sec,
                expected_width=spec.canvas.width,
                expected_height=spec.canvas.height,
                expected_fps=Fraction(spec.timebase.num, spec.timebase.den),
                expected_audio=True,
                expected_freeze_ranges=expected_freezes,
                expected_silence_ranges=[
                    (marker.sec, marker.sec + marker.duration_sec)
                    for marker in spec.markers
                    if marker.kind == "expected_silence" and marker.duration_sec > 0
                ],
                render_id=render_id,
                conn=conn,
            )
            result["technical_qa"] = qa.model_dump(mode="json")
        out(result, as_json) if as_json else typer.echo(
            f"{kind} render: {rendered.output}"
            + (
                f"\nTechnical QA: {'PASS' if result['technical_qa']['passed'] else 'FAIL'}"
                if kind == "master" else ""
            )
        )
    finally:
        conn.close()


@resolve_app.command("preview")
def resolve_preview_cmd(
    path: Path = typer.Argument(..., help="EditSpec JSON 路径"),
    output_dir: Path = typer.Option(Path("projects/renders"), "--output-dir"),
    reset: bool = typer.Option(False, "--reset"),
    launch: bool = typer.Option(False, "--launch"),
    json: bool = typer.Option(False, "--json"),
):
    """一条命令校验、构建并由 Resolve 渲染预览。"""
    _resolve_render_command(
        path,
        kind="preview",
        output_dir=output_dir,
        reset=reset,
        master_media=False,
        launch=launch,
        as_json=json,
    )


@resolve_app.command("master")
def resolve_master_cmd(
    path: Path = typer.Argument(..., help="锁画后的 EditSpec JSON"),
    output_dir: Path = typer.Option(Path("projects/renders"), "--output-dir"),
    reset: bool = typer.Option(False, "--reset"),
    launch: bool = typer.Option(False, "--launch"),
    json: bool = typer.Option(False, "--json"),
):
    """母版素材构建、Resolve Master 渲染与 13 项 Technical QA。"""
    _resolve_render_command(
        path,
        kind="master",
        output_dir=output_dir,
        reset=reset,
        master_media=True,
        launch=launch,
        as_json=json,
    )


if __name__ == "__main__":
    app()

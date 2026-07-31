"""Anime Edit Studio CLI —— 入口命令 `aes`。

只保留一条产品用例（REFACTOR.md §15）：Demo + 素材(+ 音乐) 驱动的 AMV 生成。

设计约束（AGENTS.md）：
- 所有子命令保持 --json 输出能力
- CLI 只做参数解析与结果呈现，不含业务逻辑
"""
from __future__ import annotations

import json as _json
from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    help="Anime Edit Studio —— Demo 驱动的 AMV 生成引擎",
)

doctor_app = typer.Typer(add_completion=False, help="环境与能力自检")
library_app = typer.Typer(add_completion=False, help="素材库增量索引")
amv_app = typer.Typer(add_completion=False, help="Demo 驱动的 AMV 生成")
app.add_typer(doctor_app, name="doctor")
app.add_typer(library_app, name="library")
app.add_typer(amv_app, name="amv")


def out(data, as_json: bool) -> None:
    if as_json:
        typer.echo(_json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(data)


def _v2_db() -> Path:
    from studio.core.database import DEFAULT_V2_DB

    return DEFAULT_V2_DB


# ─────────────────────────── library ───────────────────────────

@library_app.command("index")
def library_index_cmd(
    materials_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    profile: str = typer.Option("full", "--profile", help="coarse | full（REFACTOR.md §18）"),
    json: bool = typer.Option(False, "--json"),
):
    """增量索引一个素材目录：ingest → shot 检测 → 分析，全部幂等。"""
    from studio.workflows.create_amv import index_materials

    asset_ids = index_materials(materials_dir, database=_v2_db(), profile=profile)
    out({"materials_dir": str(materials_dir), "profile": profile, "count": len(asset_ids), "asset_ids": asset_ids}, json) if json else (
        typer.echo(f"{materials_dir}: {len(asset_ids)} 个素材已索引（profile={profile}）")
    )


# ─────────────────────────── amv ───────────────────────────

@amv_app.command("create")
def amv_create_cmd(
    project_id: str = typer.Option(..., "--project"),
    demo: Path = typer.Option(..., "--demo", exists=True, dir_okay=False),
    materials: Path = typer.Option(..., "--materials", exists=True, file_okay=False),
    music: Path | None = typer.Option(None, "--music", exists=True, dir_okay=False),
    focus: str | None = typer.Option(None, "--focus"),
    aspect: str | None = typer.Option(None, "--aspect", help="例如 9:16；省略则沿用 Demo"),
    fps: str | None = typer.Option(None, "--fps", help="num/den，例如 24000/1001；省略则沿用 Demo"),
    selector_profile: str = typer.Option(
        "balanced", "--selector-profile", help="fast | balanced | quality（REFACTOR.md §18）"
    ),
    launch: bool = typer.Option(False, "--launch", help="Resolve 未运行时自动启动"),
    json: bool = typer.Option(False, "--json"),
):
    """Demo + 素材(+ 音乐) → 索引 → 分析 → 规划 → AMVSpec → Resolve preview → QA。

    --selector-profile：fast 跳过 ml 分析器（embeddings/tagger）做快速首过；
    balanced/quality 目前行为相同（CoTracker/SAM2/DOVER 精筛尚未接入 selector，
    重模型后端始终按可用性自动降级，不因 profile 而变）。
    """
    from studio.execution.resolve import ResolveAdapter, ResolveUnavailable
    from studio.workflows.create_amv import build_amv_spec_workflow, render_amv_preview

    fps_pair = None
    if fps:
        num, den = fps.split("/")
        fps_pair = (int(num), int(den))

    result = build_amv_spec_workflow(
        project_id=project_id, demo_path=demo, materials_dir=materials,
        music_path=music, focus=focus, aspect=aspect, fps=fps_pair,
        database=_v2_db(), selector_profile=selector_profile,
    )

    report_summary: dict = {
        "project_id": project_id,
        "selector_profile": selector_profile,
        "output_dir": str(result.output_dir),
        "amv_spec": str(result.paths()["amv_spec"]),
        "clips": len(result.spec.clips),
        "duration_sec": result.spec.duration_sec,
        "rendered": False,
    }
    try:
        adapter = ResolveAdapter.open(auto_launch=launch)
        qa_report = render_amv_preview(result, adapter=adapter, database=_v2_db())
        report_summary["rendered"] = True
        report_summary["preview"] = str(result.output_dir / "preview.mov")
        report_summary["qa"] = str(result.output_dir / "qa.json")
        report_summary["qa_passed"] = qa_report.passed
    except ResolveUnavailable as exc:
        report_summary["resolve_error"] = str(exc)

    if json:
        out(report_summary, True)
        return
    typer.echo(f"AMVSpec: {report_summary['amv_spec']} ({report_summary['clips']} clips / {report_summary['duration_sec']:.2f}s)")
    if report_summary["rendered"]:
        typer.secho(
            f"preview {'PASS' if report_summary['qa_passed'] else 'FAIL'}: {report_summary['preview']}",
            fg="green" if report_summary["qa_passed"] else "red",
        )
    else:
        typer.secho(
            f"Resolve 真机验收未执行，不能宣称完整完成: {report_summary.get('resolve_error', 'Resolve 不可用')}",
            fg="yellow",
        )


@amv_app.command("release")
def amv_release_cmd(
    project_id: str = typer.Option(..., "--project"),
    projects_root: Path = typer.Option(Path("projects"), "--projects-root"),
    json: bool = typer.Option(False, "--json"),
):
    """把已通过 QA 硬门禁的 preview.mov 显式发布为 release.mov。"""
    from studio.workflows.create_amv import release_amv

    output_dir = projects_root / project_id
    try:
        release_path = release_amv(output_dir)
    except (FileNotFoundError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc

    out({"project_id": project_id, "release": str(release_path)}, json) if json else typer.echo(
        f"released: {release_path}"
    )


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
        "verified": ("已验证 —— 可用于 AMVSpec", "green"),
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


@doctor_app.command("vision")
def doctor_vision_cmd(json: bool = typer.Option(False, "--json")):
    """选镜阶段各开源视觉后端的可用性（REFACTOR.md §18）：已运行 / 可选未运行 / 已降级。"""
    from studio.selection.backends.anime_face import create_anime_face_backend
    from studio.selection.backends.dense_embedding import create_dense_embedding_backend
    from studio.selection.backends.subject_tracker import create_subject_tracker_backend
    from studio.selection.backends.video_quality import create_video_quality_backend

    statuses = [
        create_anime_face_backend().status,
        create_dense_embedding_backend().status,
        create_subject_tracker_backend().status,
        create_video_quality_backend().status,
    ]
    rows = [status.model_dump() for status in statuses]
    if json:
        out(rows, True)
        return
    for status in statuses:
        if status.fallback:
            mark, color = ("FALLBACK", "yellow") if status.available else ("MISSING ", "red")
        else:
            mark, color = "OK      ", "green"
        typer.secho(f"[{mark}] {status.backend:28} device={status.device}", fg=color)
        if status.fallback:
            typer.echo(f"      ↳ {status.fallback}")


@doctor_app.command("assets")
def doctor_assets_cmd(json: bool = typer.Option(False, "--json")):
    """列出当前可解析的 asset_id。"""
    from studio.core.assets import PROXY_DIR, FilesystemResolver

    ids = FilesystemResolver([PROXY_DIR]).available_ids()
    out({"count": len(ids), "dir": str(PROXY_DIR), "asset_ids": ids}, json) if json else (
        typer.echo(f"{len(ids)} 个可用素材，位于 {PROXY_DIR}\n"
                   + "\n".join(f"  {i}" for i in ids))
    )


if __name__ == "__main__":
    app()

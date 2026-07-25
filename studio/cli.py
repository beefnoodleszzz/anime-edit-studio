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
app.add_typer(doctor_app, name="doctor")
app.add_typer(spec_app, name="spec")
app.add_typer(resolve_app, name="resolve")


def out(data, as_json: bool) -> None:
    if as_json:
        typer.echo(_json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(data)


def _load_spec(path: Path):
    from studio.editspec.schema import EditSpec

    if not path.exists():
        typer.secho(f"EditSpec 不存在: {path}", fg="red", err=True)
        raise typer.Exit(2)
    return EditSpec.model_validate_json(path.read_text())


def _resolver(prefer_master: bool):
    from studio.core.assets import PROXY_DIR, FilesystemResolver

    return FilesystemResolver([PROXY_DIR], prefer_proxy=not prefer_master)


# ─────────────────────────── 通用 ───────────────────────────

@app.command("version")
def version_cmd(json: bool = typer.Option(False, "--json")):
    """显示版本与当前迁移阶段。"""
    from studio import PHASE, __version__

    out({"version": __version__, "phase": PHASE}, json)


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
    result = validate(spec, resolve_asset=_resolver(master))

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
        compiler = ResolveCompiler(adapter, _resolver(master), state_dir=path.parent)
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


if __name__ == "__main__":
    app()

"""Anime Edit Studio v2 CLI —— 入口命令 `aes`。

设计约束（AGENTS.md）：
- 所有子命令保持 --json 输出能力
- CLI 只做参数解析与结果呈现，不含业务逻辑
"""
from __future__ import annotations

import json as _json

import typer

app = typer.Typer(
    add_completion=False,
    help="Anime Edit Studio —— AI 导演 / EditSpec IR / DaVinci Resolve 执行",
)

doctor_app = typer.Typer(add_completion=False, help="环境与能力自检")
app.add_typer(doctor_app, name="doctor")


def out(data, as_json: bool) -> None:
    if as_json:
        typer.echo(_json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(data)


@app.command("version")
def version_cmd(json: bool = typer.Option(False, "--json")):
    """显示版本与当前迁移阶段。"""
    from studio import __version__, PHASE

    out({"version": __version__, "phase": PHASE}, json)


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
        typer.echo(f"[{mark}] {item['name']:22} {item['detail']}")
    typer.echo("")
    typer.echo("ready" if report["ready"] else "NOT ready —— 见上方 FAIL 项")
    raise typer.Exit(0 if report["ready"] else 1)


@doctor_app.command("capabilities")
def doctor_capabilities_cmd(json: bool = typer.Option(False, "--json")):
    """显示 Resolve 能力矩阵中已验证 / 待验证 / 未探测的能力。"""
    from studio.core.capabilities import load_capabilities, summarize

    caps = load_capabilities()
    summary = summarize(caps)
    if json:
        out(summary, True)
        return
    for bucket in ("verified", "probed_unverified", "unprobed"):
        typer.echo(f"\n{bucket} ({len(summary[bucket])}):")
        for name in summary[bucket]:
            typer.echo(f"  - {name}")


if __name__ == "__main__":
    app()

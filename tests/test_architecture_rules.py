"""架构规则的自动化强制 —— AGENTS.md §2 的守卫。

这些测试失败 = 架构被破坏，不是"测试挂了"，必须修代码而不是改测试。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STUDIO = REPO / "studio"

RESOLVE_ADAPTER_DIR = STUDIO / "execution" / "resolve"
FORBIDDEN_RESOLVE_IMPORTS = {"DaVinciResolveScript", "fusionscript"}
# R5：v2 禁止引用 v1
FORBIDDEN_V1_PACKAGES = {"anime"}


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _imported_roots(path: Path) -> set[str]:
    """返回该文件 import 的顶层模块名集合。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover
        pytest.fail(f"{path} 语法错误: {exc}")
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.skipif(not STUDIO.exists(), reason="studio/ 尚未创建")
def test_r1_resolve_api_only_via_adapter():
    """R1：studio/execution/resolve/ 之外禁止 import DaVinciResolveScript。"""
    offenders = []
    for path in _python_files(STUDIO):
        if RESOLVE_ADAPTER_DIR in path.parents or path.parent == RESOLVE_ADAPTER_DIR:
            continue
        bad = _imported_roots(path) & FORBIDDEN_RESOLVE_IMPORTS
        if bad:
            offenders.append(f"{path.relative_to(REPO)} -> {sorted(bad)}")
    assert not offenders, (
        "违反 AGENTS.md R1：Resolve API 只能经 ResolveAdapter 访问。\n"
        + "\n".join(offenders)
    )


@pytest.mark.skipif(not STUDIO.exists(), reason="studio/ 尚未创建")
def test_r5_v2_must_not_import_v1():
    """R5：studio/ 禁止 import anime/（v1 遗留包）。"""
    offenders = []
    for path in _python_files(STUDIO):
        bad = _imported_roots(path) & FORBIDDEN_V1_PACKAGES
        if bad:
            offenders.append(f"{path.relative_to(REPO)} -> {sorted(bad)}")
    assert not offenders, (
        "违反 AGENTS.md R5：v2 不得引用 v1 代码。\n" + "\n".join(offenders)
    )


def test_r3_capabilities_file_is_wellformed():
    """R3：能力矩阵必须存在且每条能力都显式声明 probed / verified。"""
    from studio.core.capabilities import load_capabilities

    caps = load_capabilities()
    assert caps.get("resolve_version_tested"), "必须记录探测所用的 Resolve 版本"
    entries = caps.get("capabilities") or {}
    assert entries, "能力矩阵为空"

    missing = [
        name
        for name, entry in entries.items()
        if "verified" not in entry or "path" not in entry
    ]
    assert not missing, f"以下能力缺少 verified / path 字段: {missing}"

    # verified 必须蕴含 probed（不可能没探测过就说验证过）
    contradictions = [
        name
        for name, entry in entries.items()
        if entry.get("verified") and entry.get("probed") is False
    ]
    assert not contradictions, f"verified=true 但 probed=false，自相矛盾: {contradictions}"


def test_r3_require_rejects_unverified_capability():
    """R3 的强制点必须真的拦得住。"""
    from studio.core.capabilities import CapabilityError, is_verified, require

    # speed_ramp 当前应为 probed 但未 verified
    assert not is_verified("speed_ramp")
    with pytest.raises(CapabilityError):
        require("speed_ramp")

    with pytest.raises(CapabilityError):
        require("no_such_capability_xyz")

    # 已验证的能力应放行
    require("append_clip_with_in_out")


def test_r8_pitfalls_are_documented():
    """实测踩过的坑必须留在能力矩阵里，防止后人重犯。

    每个 P 编号都对应一次真实的静默错位，删除它们等于把坑重新埋回去。
    """
    from studio.core.capabilities import load_capabilities

    ids = {p["id"] for p in load_capabilities().get("pitfalls", [])}
    expected = {f"P{n}" for n in range(1, 12)}
    assert expected <= ids, f"缺失的坑位记录: {sorted(expected - ids)}"

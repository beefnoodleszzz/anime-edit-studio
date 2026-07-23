"""锁定后一键出终版:把已批准的迭代 editspec 走完整套底链到可发布母版。

两阶段工作流的 B 阶段。A 阶段(draft/preview)只判断选片/构图/节奏,绝不 4K/超分/补帧;
镜头选取与合成效果满意、cut 锁定后,才调用本命令一次性:
  restore → interpolate 60fps → superres(全镜头) → 4K 渲染(不回链母版,保留超分源)
  → 分层声音 → 母带(-10 LUFS + LUT + 平台版) → (可选 endcard) → QA → 磁盘回收。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import (endcard as endcard_mod, interpolate, library, master,
               qa, quality_gate, render, restore, sound, superres)


def _register_stage(project_id: str, before_path: str, after_path: str,
                    stage: str, *, original_ranges: bool = False) -> list[dict]:
    before = json.loads(Path(before_path).read_text())
    after = json.loads(Path(after_path).read_text())
    by_id = {shot["id"]: shot for shot in after["shots"]}
    fps = float(before["fps"])
    reports = []
    for shot in before["shots"]:
        processed = by_id.get(shot["id"])
        if not processed or processed["src"] == shot["src"]:
            continue
        duration = (float(shot["duration_in_frames"]) / fps) * float(shot.get("speed") or 1)
        reports.append(quality_gate.compare(
            project_id, shot["id"], stage, shot["src"], processed["src"],
            source_start=float(shot.get("source_in_sec") or 0) if original_ranges else 0,
            duration=duration,
        ))
    return reports


def finalize(editspec_path: str, *, shots: list[str] | None = None,
             target_fps: float = 60.0, endcard: bool = False,
             endcard_text: str = "END", clean: bool = True,
             quality_gate_enabled: bool = True) -> dict:
    spec_path = Path(editspec_path).resolve()
    if not spec_path.exists():
        raise FileNotFoundError(spec_path)
    project_id = spec_path.parent.name
    if not (spec_path.parent / "beatmap.json").exists():
        raise FileNotFoundError(
            f"缺少 {spec_path.parent / 'beatmap.json'};声音设计需要它。请先用 direct/beat 生成节拍。")

    steps: dict = {}
    if quality_gate_enabled:
        steps["structural_quality"] = quality_gate.audit(str(spec_path), visual=True)
        if not steps["structural_quality"]["pass"]:
            return {"project_id": project_id, "delivered": False,
                    "reason": "导演结构质量门禁未通过", "steps": steps}

    steps["restore"] = restore.restore_editspec(str(spec_path), only_ids=shots)
    cur = steps["restore"]["editspec"]
    if quality_gate_enabled:
        steps["restore_reviews"] = _register_stage(
            project_id, str(spec_path), cur, "restore", original_ranges=True)

    previous = cur
    steps["interpolate"] = interpolate.interpolate_editspec(cur, only_ids=shots, target_fps=target_fps)
    cur = steps["interpolate"]["editspec"]
    if quality_gate_enabled:
        steps["rife_reviews"] = _register_stage(project_id, previous, cur, "rife")

    previous = cur
    steps["superres"] = superres.upscale_editspec(cur, only_ids=shots)
    cur = steps["superres"]["editspec"]
    if quality_gate_enabled:
        steps["superres_reviews"] = _register_stage(project_id, previous, cur, "superres")
        steps["enhancement_quality"] = quality_gate.status(project_id)
        if not steps["enhancement_quality"]["pass"]:
            return {
                "project_id": project_id, "delivered": False,
                "reason": "增强 A/B 等待逐镜批准或存在已拒绝结果",
                "editspec": cur, "quality": steps["enhancement_quality"],
                "steps": steps,
            }

    # 回链关闭:cur 的镜头 src 已是超分后的 ProRes,不能被母版回链覆盖。
    steps["render"] = render.render(cur, preview=False, relink_master=False)
    steps["sound"] = sound.build(cur)

    m = master.master(steps["sound"])
    steps["master"] = m
    platform = m["platform"]
    if endcard:
        platform = endcard_mod.add(platform, text=endcard_text)
        steps["endcard"] = platform

    spec_json = json.loads(Path(cur).read_text())
    report = qa.qa(m["master"], expected_width=spec_json["width"],
                   expected_height=spec_json["height"], expected_audio=True)
    steps["qa"] = report
    if not report["pass"]:
        return {"project_id": project_id, "delivered": False,
                "reason": "QA 硬项未通过,已停在交付前", "checks": report["checks"],
                "master": m["master"], "steps": steps}

    if clean:
        steps["clean"] = library.clean(project_id, apply=True)

    return {"project_id": project_id, "delivered": True,
            "editspec": cur, "master": m["master"], "platform": platform,
            "width": spec_json["width"], "height": spec_json["height"],
            "steps": steps}

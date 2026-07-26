"""Schema-constrained creative issues and deterministic conversion to Diff."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.agents import LLMCall, StructuredProvider
from studio.creative.director import DirectorPlan
from studio.creative.reference import StyleFingerprint
from studio.editspec.diff import EditSpecDiff, PatchClip, ReplaceClip
from studio.editspec.schema import Clip, EditSpec, SourceRange


class SuggestedFix(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal[
        "replace_clip", "adjust_timing", "adjust_intensity",
        "reframe", "audio", "none",
    ]
    clip_id: str | None = None
    requirements: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _executable_requirements(self):
        if self.op == "replace_clip":
            allowed = {"min_visual_energy", "character", "action", "role"}
            unknown = set(self.requirements) - allowed
            if unknown:
                raise ValueError(
                    f"replace_clip 含未知 requirements: {sorted(unknown)}"
                )
            energy = self.requirements.get("min_visual_energy")
            if energy is not None and (
                not isinstance(energy, (int, float))
                or isinstance(energy, bool)
                or not 0 <= energy <= 1
            ):
                raise ValueError("replace_clip.min_visual_energy 必须是 0..1 数值")
            for key in ("character", "action", "role"):
                value = self.requirements.get(key)
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"replace_clip.{key} 必须是字符串")
        if self.op == "adjust_intensity":
            scale = self.requirements.get("scale")
            if not isinstance(scale, (int, float)) or isinstance(scale, bool):
                raise ValueError("adjust_intensity 必须给出数值 requirements.scale")
        if self.op == "reframe":
            offset = self.requirements.get("offset_x")
            if not isinstance(offset, (int, float)) or isinstance(offset, bool):
                raise ValueError("reframe 必须给出数值 requirements.offset_x")
        return self


class RevisionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    timeline_start_sec: float = Field(..., ge=0)
    timeline_end_sec: float = Field(..., ge=0)
    severity: Literal["low", "medium", "high"]
    reason: str
    confidence: float = Field(..., ge=0, le=1)
    suggested_fix: SuggestedFix


class CreativeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    issues: list[RevisionIssue]


class Replacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    shot_id: str
    source_in_sec: float = Field(..., ge=0)


def select_replacement_from_db(
    conn: sqlite3.Connection,
    clip: Clip,
    requirements: dict,
) -> Replacement:
    """Resolve a semantic replacement request with deterministic SQL scoring."""
    conn.row_factory = sqlite3.Row
    candidate_ids = [
        value for value in clip.decision.alternatives if value != clip.shot_id
    ]
    where = ["(end_sec-start_sec)>=?"]
    params: list[object] = [clip.timeline.duration_sec * clip.retime.speed]
    if candidate_ids:
        where.append(f"id IN ({','.join('?' for _ in candidate_ids)})")
        params.extend(candidate_ids)
    if requirements.get("min_visual_energy") is not None:
        where.append("coalesce(visual_energy,0)>=?")
        params.append(float(requirements["min_visual_energy"]))
    for column in ("character", "action"):
        value = requirements.get(column)
        if value:
            where.append(
                f"(lower(coalesce({column},'')) LIKE ? OR lower(coalesce(tags,'')) LIKE ?)"
            )
            needle = f"%{value.lower()}%"
            params.extend([needle, needle])
    row = conn.execute(
        f"""
        SELECT id,asset_id,start_sec FROM shots
        WHERE {' AND '.join(where)}
        ORDER BY
          coalesce(visual_energy,0) DESC,
          coalesce(image_quality,0) DESC,
          coalesce(cutability,0) DESC,
          id
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        raise ValueError(f"clip {clip.id} 没有满足 Revision 约束的替换镜头")
    return Replacement(
        asset_id=row["asset_id"],
        shot_id=row["id"],
        source_in_sec=row["start_sec"],
    )


def _clip_context(spec: EditSpec) -> list[dict]:
    return [
        {
            "clip_id": clip.id,
            "timeline": [clip.timeline.in_sec, clip.timeline.out_sec],
            "role": clip.role,
            "shot_id": clip.shot_id,
            "locked": clip.decision.locked,
            "alternatives": clip.decision.alternatives,
        }
        for clip in spec.clips
    ]


def _validate_targets(review: CreativeReview, spec: EditSpec) -> CreativeReview:
    known = {clip.id: clip for clip in spec.clips}
    for issue in review.issues:
        fix = issue.suggested_fix
        if fix.op != "none":
            if not fix.clip_id or fix.clip_id not in known:
                raise ValueError(f"LLM 返回不存在的 clip_id: {fix.clip_id!r}")
            if known[fix.clip_id].decision.locked:
                raise ValueError(f"LLM 试图修改用户锁定 clip: {fix.clip_id}")
        if issue.timeline_end_sec < issue.timeline_start_sec:
            raise ValueError("issue 时间范围倒置")
    return review


def parse_feedback(
    provider: StructuredProvider,
    *,
    feedback: str,
    spec: EditSpec,
) -> tuple[CreativeReview, LLMCall]:
    system = (
        "你是剪辑反馈解析器。只做语义理解，不计算帧、不修改时间线。"
        "必须引用给定 clip_id；用户说某秒时按给定 timeline 范围定位。"
        "adjust_intensity 必须给出安全的 requirements.scale(0.5..3)；"
        "reframe 必须给 requirements.offset_x(-1..1)。"
        "replace_clip 只允许 min_visual_energy(0..1)、character、action、role。"
        "不确定时降低 confidence，不得发明镜头或 Recipe。"
    )
    prompt = json.dumps(
        {
            "task": "把用户反馈转为结构化 Revision issues",
            "feedback": feedback,
            "clips": _clip_context(spec),
        },
        ensure_ascii=False,
    )
    review, call = provider.generate(
        system=system, prompt=prompt, output_type=CreativeReview
    )
    return _validate_targets(review, spec), call


def run_creative_critic(
    provider: StructuredProvider,
    *,
    preview_analysis: dict,
    plan: DirectorPlan,
    style: StyleFingerprint | None,
    spec: EditSpec,
) -> tuple[CreativeReview, LLMCall]:
    system = (
        "你是 Creative Critic。技术 QA 不在你的职责内，绝不决定 DELIVERED。"
        "检查 DirectorPlan、节奏、连续性、构图、主体、字幕残留、音效密度；"
        "所有修订必须引用现有 clip_id 并输出严格 schema。"
    )
    prompt = json.dumps(
        {
            "task": "审查预览的创意问题并提出最小修订",
            "preview_analysis": preview_analysis,
            "director_plan": plan.model_dump(mode="json"),
            "style_fingerprint": (
                style.model_dump(mode="json") if style is not None else None
            ),
            "clips": _clip_context(spec),
        },
        ensure_ascii=False,
    )
    review, call = provider.generate(
        system=system, prompt=prompt, output_type=CreativeReview
    )
    return _validate_targets(review, spec), call


def proposal_to_diff(
    review: CreativeReview,
    spec: EditSpec,
    *,
    select_replacement: Callable[[Clip, dict], Replacement],
    source: Literal["user", "critic"] = "critic",
) -> EditSpecDiff:
    """Convert semantic proposals to executable ops without LLM arithmetic."""
    ops = []
    clips = {clip.id: clip for clip in spec.clips}
    for issue in review.issues:
        fix = issue.suggested_fix
        if fix.op == "none":
            continue
        assert fix.clip_id is not None
        clip = clips[fix.clip_id]
        if fix.op == "replace_clip":
            replacement = select_replacement(clip, fix.requirements)
            updated = clip.model_copy(deep=True)
            updated.asset_id = replacement.asset_id
            updated.shot_id = replacement.shot_id
            source_duration = clip.timeline.duration_sec * clip.retime.speed
            updated.source = SourceRange(
                in_sec=replacement.source_in_sec,
                out_sec=replacement.source_in_sec + source_duration,
            )
            updated.decision.source = "user" if source == "user" else "ai"
            updated.decision.reasoning = issue.reason
            updated.decision.confidence = issue.confidence
            ops.append(ReplaceClip(clip_id=clip.id, new=updated))
        elif fix.op == "adjust_intensity":
            value = float(fix.requirements["scale"])
            if not 0.5 <= value <= 3:
                raise ValueError("adjust_intensity.scale 超出安全范围 0.5..3")
            ops.append(
                PatchClip(clip_id=clip.id, path="framing.scale", value=value)
            )
        elif fix.op == "reframe":
            x = float(fix.requirements["offset_x"])
            if not -1 <= x <= 1:
                raise ValueError("reframe.offset_x 超出 -1..1")
            ops.append(PatchClip(clip_id=clip.id, path="framing.offset_x", value=x))
        else:
            # Timing/audio changes require dedicated deterministic solvers or
            # verified recipes. Preserve the issue, but never fake execution.
            continue
    return EditSpecDiff(
        from_version=spec.revision,
        to_version=spec.revision + 1,
        source=source,
        ops=ops,
    )


__all__ = [
    "CreativeReview",
    "Replacement",
    "RevisionIssue",
    "SuggestedFix",
    "parse_feedback",
    "proposal_to_diff",
    "run_creative_critic",
    "select_replacement_from_db",
]

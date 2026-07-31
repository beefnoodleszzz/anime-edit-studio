"""Deterministic EditSpec diff and patch application.

Clip ids are the stable anchors.  The module never asks an LLM to calculate
timeline shifts or mutate nested fields (AGENTS.md R6).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.editspec.schema import Clip, EditSpec


class DiffError(ValueError):
    """A diff is invalid for the supplied base spec."""


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddClip(_Op):
    op: Literal["add_clip"] = "add_clip"
    clip: Clip


class RemoveClip(_Op):
    op: Literal["remove_clip"] = "remove_clip"
    clip_id: str


class ReplaceClip(_Op):
    op: Literal["replace_clip"] = "replace_clip"
    clip_id: str
    new: Clip


class PatchClip(_Op):
    op: Literal["patch_clip"] = "patch_clip"
    clip_id: str
    path: str
    value: object


class PatchSpec(_Op):
    op: Literal["patch_spec"] = "patch_spec"
    path: Literal["audio", "markers", "captions", "meta", "motion_phrases"]
    value: object


class ShiftAfter(_Op):
    op: Literal["shift_after"] = "shift_after"
    from_clip: str
    delta_sec: float


DiffOp = Annotated[
    Union[AddClip, RemoveClip, ReplaceClip, PatchClip, PatchSpec, ShiftAfter],
    Field(discriminator="op"),
]


class EditSpecDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_version: int = Field(..., ge=1)
    to_version: int = Field(..., ge=2)
    source: Literal["ai", "user", "critic", "rule"] = "rule"
    ops: list[DiffOp] = Field(default_factory=list)

    @model_validator(mode="after")
    def _versions_are_consecutive(self):
        if self.to_version != self.from_version + 1:
            raise ValueError("EditSpec diff 必须只跨一个 revision")
        return self


def _clip_map(spec: EditSpec) -> dict[str, Clip]:
    result: dict[str, Clip] = {}
    for clip in spec.clips:
        if clip.id in result:
            raise DiffError(f"base spec 含重复 clip id: {clip.id}")
        result[clip.id] = clip
    return result


def diff_specs(
    before: EditSpec,
    after: EditSpec,
    *,
    source: Literal["ai", "user", "critic", "rule"] = "rule",
) -> EditSpecDiff:
    """Create a stable, deterministic clip-level diff."""
    if before.id != after.id:
        raise DiffError("不能 diff 不同 project id 的 EditSpec")
    old, new = _clip_map(before), _clip_map(after)
    ops: list[DiffOp] = []

    for clip_id in old.keys() - new.keys():
        ops.append(RemoveClip(clip_id=clip_id))
    for clip_id in new.keys() - old.keys():
        ops.append(AddClip(clip=deepcopy(new[clip_id])))
    for clip_id in old.keys() & new.keys():
        if old[clip_id] != new[clip_id]:
            ops.append(ReplaceClip(clip_id=clip_id, new=deepcopy(new[clip_id])))
    for path in ("audio", "markers", "captions", "meta", "motion_phrases"):
        if getattr(before, path) != getattr(after, path):
            value = after.model_dump(mode="python", by_alias=True)[path]
            ops.append(PatchSpec(path=path, value=deepcopy(value)))

    order = {clip.id: index for index, clip in enumerate(after.clips)}
    ops.sort(
        key=lambda op: (
            order.get(
                op.clip.id if isinstance(op, AddClip) else getattr(op, "clip_id", ""),
                len(order),
            ),
            op.op,
        )
    )
    return EditSpecDiff(
        from_version=before.revision,
        to_version=before.revision + 1,
        source=source,
        ops=ops,
    )


def _assert_mutable(clip: Clip, source: str) -> None:
    if clip.decision.locked and source != "user":
        raise DiffError(f"clip {clip.id} 已由用户锁定，{source} diff 不得修改")


def _patch_clip(clip: Clip, path: str, value: object) -> Clip:
    parts = path.split(".")
    if not parts or any(not part or part.startswith("_") for part in parts):
        raise DiffError(f"非法 patch path: {path!r}")
    payload = clip.model_dump(mode="python", by_alias=True)
    cursor: object = payload
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            raise DiffError(f"patch path 不存在: {path!r}")
        cursor = cursor[part]
    if not isinstance(cursor, dict) or parts[-1] not in cursor:
        raise DiffError(f"patch path 不存在: {path!r}")
    cursor[parts[-1]] = value
    return Clip.model_validate(payload)


def apply_diff(spec: EditSpec, patch: EditSpecDiff) -> EditSpec:
    """Apply a diff atomically; the input spec is never mutated."""
    if patch.from_version != spec.revision:
        raise DiffError(
            f"revision 不匹配: spec={spec.revision}, diff.from={patch.from_version}"
        )
    result = spec.model_copy(deep=True)

    for op in patch.ops:
        clips = _clip_map(result)
        if isinstance(op, AddClip):
            if op.clip.id in clips:
                raise DiffError(f"add_clip id 已存在: {op.clip.id}")
            result.clips.append(op.clip.model_copy(deep=True))
        elif isinstance(op, RemoveClip):
            clip = clips.get(op.clip_id)
            if clip is None:
                raise DiffError(f"remove_clip 找不到: {op.clip_id}")
            _assert_mutable(clip, patch.source)
            result.clips = [item for item in result.clips if item.id != op.clip_id]
        elif isinstance(op, ReplaceClip):
            clip = clips.get(op.clip_id)
            if clip is None:
                raise DiffError(f"replace_clip 找不到: {op.clip_id}")
            _assert_mutable(clip, patch.source)
            if op.new.id != op.clip_id:
                raise DiffError("replace_clip 不得改变稳定 clip id")
            result.clips = [
                op.new.model_copy(deep=True) if item.id == op.clip_id else item
                for item in result.clips
            ]
        elif isinstance(op, PatchClip):
            clip = clips.get(op.clip_id)
            if clip is None:
                raise DiffError(f"patch_clip 找不到: {op.clip_id}")
            _assert_mutable(clip, patch.source)
            replacement = _patch_clip(clip, op.path, op.value)
            result.clips = [
                replacement if item.id == op.clip_id else item for item in result.clips
            ]
        elif isinstance(op, PatchSpec):
            payload = result.model_dump(mode="python", by_alias=True)
            payload[op.path] = deepcopy(op.value)
            result = EditSpec.model_validate(payload)
        elif isinstance(op, ShiftAfter):
            anchor = clips.get(op.from_clip)
            if anchor is None:
                raise DiffError(f"shift_after 找不到: {op.from_clip}")
            threshold = anchor.timeline.in_sec
            for clip in result.clips:
                if clip.timeline.in_sec >= threshold:
                    _assert_mutable(clip, patch.source)
                    shifted = clip.timeline.in_sec + op.delta_sec
                    if shifted < 0:
                        raise DiffError(f"shift_after 会使 clip {clip.id} 落到负时间")
                    clip.timeline.in_sec = shifted

    result.revision = patch.to_version
    # Re-validate the complete model after all mutations; this makes application atomic.
    return EditSpec.model_validate(result.model_dump(mode="python", by_alias=True))


__all__ = [
    "AddClip",
    "DiffError",
    "EditSpecDiff",
    "PatchClip",
    "PatchSpec",
    "RemoveClip",
    "ReplaceClip",
    "ShiftAfter",
    "apply_diff",
    "diff_specs",
]

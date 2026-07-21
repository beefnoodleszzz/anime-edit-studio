"""EditSpec:Python 与 Remotion 之间的剪辑数据契约。

生成后写成 projects/<id>/editspec.json,由 `anime render` 交给 Remotion 执行。
字段需与 renderer/src/schema.ts 保持一致。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from . import config


class Transform(BaseModel):
    scale: float = 1.0
    x: float = 0.0
    y: float = 0.0
    rotate: float = 0.0


class Effect(BaseModel):
    type: str                       # glow | rgbSplit | shake | vignette | flash
    intensity: float = 0.5
    params: dict = Field(default_factory=dict)


class Shot(BaseModel):
    id: str
    src: str                        # 视频路径(代理或母版)
    source_in_sec: float = 0.0      # 源起点
    start_frame: int                # 在成片时间线上的起始帧
    duration_in_frames: int
    speed: float = 1.0              # 播放倍率(<1 慢动作)
    transform: Transform = Field(default_factory=Transform)
    effects: list[Effect] = Field(default_factory=list)
    lut: str | None = None          # kit/luts/*.cube
    matte: str | None = None        # 主体遮罩 PNG(启用主体高亮)
    # M5 运动设计
    camera_move: str = "none"       # none|pushIn|pushOut|panLeft|panRight|panUp|panDown
    camera_amount: float = 0.0      # 运镜幅度(~0.06–0.14)
    transition: str = "none"        # none|flash|whipLeft|whipRight|zoomBlur(入场转场)
    transition_intensity: float = 0.0
    ramp: str = "none"              # none|decel|accel|smooth(速度曲线;配合 speed 作平均倍率)
    reframe_x: float = 0.0          # 主体感知横向重构图偏移(-1..1,跨画幅时防切主体)
    fill_mode: str = "crop"         # crop=满屏 | fit_blur=毛玻璃背景+完整画面居中(保构图)


class AudioLayer(BaseModel):
    id: str
    src: str
    start_frame: int = 0
    trim_start_frames: int = 0   # 从源起点裁掉的帧(卡点对齐用)
    gain_db: float = 0.0


class EditSpec(BaseModel):
    id: str
    fps: int = 60
    width: int
    height: int
    duration_in_frames: int
    shots: list[Shot] = Field(default_factory=list)
    audio: list[AudioLayer] = Field(default_factory=list)


def choose_canvas(candidates: list[dict], *, canvas: str = "4x5",
                  width: int | None = None, height: int | None = None) -> tuple[int, int, str]:
    """Choose a delivery canvas; 4:5 true-4K is the studio default.

    ``auto`` uses the dominant source orientation and its most common native
    resolution.  An explicit orientation still preserves the most common native
    resolution in that orientation; ``--width``/``--height`` are the opt-in
    escape hatch for a deliberate delivery size.
    """
    if (width is None) != (height is None):
        raise ValueError("--width 和 --height 必须同时提供")
    if width is not None and height is not None:
        if width <= 0 or height <= 0:
            raise ValueError("画布尺寸必须大于 0")
        orientation = "landscape" if width > height else "portrait" if height > width else "square"
        return int(width), int(height), orientation

    requested = canvas.casefold()
    if requested not in {"4x5", "auto", "landscape", "portrait", "square"}:
        raise ValueError("canvas 必须是 4x5 / auto / landscape / portrait / square")
    if requested == "4x5":
        # Long edge is 3840px: an actual 4K-class 4:5 delivery, not 1080p
        # enlarged after the fact.
        return 3072, 3840, requested

    from collections import Counter
    from . import db

    counts = Counter(c["asset_id"] for c in candidates)
    conn = db.connect()
    media: list[tuple[int, int, int]] = []
    for asset_id, count in counts.items():
        row = db.asset_by_id(conn, asset_id)
        if row and row["width"] and row["height"]:
            media.append((int(row["width"]), int(row["height"]), count))
    conn.close()
    if not media:
        raise ValueError("候选素材缺少分辨率信息，无法自动决定画幅")

    def orient(w: int, h: int) -> str:
        return "landscape" if w / h > 1.1 else "portrait" if h / w > 1.1 else "square"

    if requested == "auto":
        orientation_counts = Counter()
        for w, h, count in media:
            orientation_counts[orient(w, h)] += count
        requested = orientation_counts.most_common(1)[0][0]

    matching = [(w, h, count) for w, h, count in media if orient(w, h) == requested]
    if matching:
        native = Counter({(w, h): count for w, h, count in matching}).most_common(1)[0][0]
        return native[0], native[1], requested

    # No source has the requested orientation: preserve all pixels by using a
    # conventional canvas; shot fill defaults to fit_blur rather than cropping.
    fallback = {"landscape": (1920, 1080), "portrait": (1080, 1920), "square": (1080, 1080)}
    w, h = fallback[requested]
    return w, h, requested


def from_candidates(project_id: str, candidates: list[dict], *,
                    shot_frames: int = 45, fps: int = 60, gap: int = 0,
                    canvas: str = "4x5", width: int | None = None,
                    height: int | None = None) -> EditSpec:
    """把 search 出的候选拼成一条最简单的顺排 EditSpec(M1 手工装配的起点)。"""
    conn_paths = _resolve_sources(candidates)
    shots: list[Shot] = []
    cursor = 0
    for c in candidates:
        shots.append(Shot(
            id=c["shot_id"], src=conn_paths[c["asset_id"]],
            source_in_sec=c["start_sec"],
            start_frame=cursor, duration_in_frames=shot_frames,
            reframe_x=c.get("reframe_x") or 0.0,
        ))
        cursor += shot_frames + gap
    out_w, out_h, _ = choose_canvas(candidates, canvas=canvas, width=width, height=height)
    return EditSpec(id=project_id, fps=fps, width=out_w, height=out_h,
                    duration_in_frames=cursor, shots=shots)


def _resolve_sources(candidates: list[dict]) -> dict[str, str]:
    """Resolve EditSpec media to the best available source.

    Proxies belong to analysis and interactive review.  Persisting a proxy in an
    EditSpec makes every final render inherit an unnecessary generation of
    compression, even when the original master is available.  Rendering can
    still create lower-resolution previews from this master via ``--preview``.
    """
    from . import db
    conn = db.connect()
    out: dict[str, str] = {}
    for c in candidates:
        if c["asset_id"] not in out:
            a = db.asset_by_id(conn, c["asset_id"])
            if not a:
                out[c["asset_id"]] = ""
                continue
            master = Path(a["path"])
            out[c["asset_id"]] = (str(master) if master.exists()
                                   else a["proxy_path"] or a["path"])
    conn.close()
    return out


def save(spec: EditSpec, name: str | None = None) -> Path:
    proj = config.PROJECTS / spec.id
    proj.mkdir(parents=True, exist_ok=True)
    filename = f"editspec.{name}.json" if name else "editspec.json"
    path = proj / filename
    path.write_text(json.dumps(spec.model_dump(), ensure_ascii=False, indent=2))
    return path

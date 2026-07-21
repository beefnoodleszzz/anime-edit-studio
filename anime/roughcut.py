"""节拍驱动的多版本粗剪装配。

输入:beatmap + 候选镜头池;输出:节奏/情绪/视觉流三个版本的 EditSpec。
- 切点落在 beat(节奏)或 downbeat(情绪)上;
- 按变体附加速度(慢动作)与特效(闪白/抖动/辉光/暗角);
- 视觉流版按运动方向连续性重排,避免相邻镜头方向打架。
"""
from __future__ import annotations

from . import editspec

OPPOSITE = {
    "left": "right", "right": "left", "up": "down", "down": "up",
    "up-left": "down-right", "down-right": "up-left",
    "up-right": "down-left", "down-left": "up-right",
}


def _flow_order(pool: list[dict]) -> list[dict]:
    """贪心重排:尽量让相邻镜头运动方向不相反。"""
    if len(pool) < 2:
        return list(pool)
    remaining = list(pool)
    out = [remaining.pop(0)]
    while remaining:
        prev = out[-1].get("motion_dir")
        idx = next((j for j, c in enumerate(remaining)
                    if c.get("motion_dir") != OPPOSITE.get(prev)), 0)
        out.append(remaining.pop(idx))
    return out


def _rhythm_fx(c: dict, is_down: bool) -> list[editspec.Effect]:
    fast = (c.get("motion_mag") or 0) > 0.8
    fx = [editspec.Effect(type="rgbSplit", intensity=0.5 if fast else 0.25)]  # velocity 招牌色差
    if is_down:
        fx.append(editspec.Effect(type="flash", intensity=0.6))
    if fast:
        fx.append(editspec.Effect(type="shake", intensity=0.4))
    return fx


def _emotion_fx(c: dict, is_down: bool) -> list[editspec.Effect]:
    return [editspec.Effect(type="glow", intensity=0.35)]


def _flow_fx(c: dict, is_down: bool) -> list[editspec.Effect]:
    return [editspec.Effect(type="vignette", intensity=0.3)]


VARIANTS = {
    #            切点步长  速度   特效        视觉流重排
    "rhythm":  dict(step=1, speed=1.0, fx=_rhythm_fx, reorder=False),
    "emotion": dict(step=4, speed=0.5, fx=_emotion_fx, reorder=False),
    "flow":    dict(step=1, speed=1.0, fx=_flow_fx, reorder=True),
}


_PAN = {"left": "panLeft", "right": "panRight", "up": "panUp", "down": "panDown"}


def _camera(c: dict, name: str) -> tuple[str, float]:
    if name == "emotion":
        return ("pushIn", 0.11)                        # 慢推,呼吸感
    pan = _PAN.get(c.get("motion_dir") or "")
    if pan and name == "flow":
        return (pan, 0.10)                             # 跟随主体运动方向平移
    return ("pushIn" if hash(c["shot_id"]) % 3 else "pushOut", 0.08)


def _ramp(name: str, c: dict, i: int) -> str:
    # speed=1 时 ramp 不改镜头时长,只重分配节奏(快进→慢出落地)
    if name == "emotion":
        return "smooth"
    if (c.get("motion_mag") or 0) > 0.8:
        return "decel"                                 # 高速镜头落地慢镜(velocity 招牌)
    if i % 5 == 0:
        return "accel"                                 # 铺垫段加速
    return "none"


def _transition(name: str, is_down: bool, i: int, c: dict) -> tuple[str, float]:
    if name == "emotion":
        return ("flash", 0.28) if is_down else ("none", 0.0)
    if is_down:
        return ("flash", 0.6)                          # 下拍闪切
    if (c.get("motion_mag") or 0) > 0.8:
        return ("whipLeft" if i % 2 else "whipRight", 0.6)   # 高速甩镜
    if i % 3 == 0:
        return ("zoomBlur", 0.5)
    return ("none", 0.0)


def _build(project_id: str, name: str, beatmap: dict, candidates: list[dict],
           sources: dict[str, str], fps: int) -> editspec.EditSpec:
    cfg = VARIANTS[name]
    beats = beatmap["beats"]
    downbeats = set(beatmap["downbeats"])
    cut_times = beats[::cfg["step"]]
    if len(cut_times) < 2:
        cut_times = beats
    pool = _flow_order(candidates) if cfg["reorder"] else list(candidates)

    t0 = cut_times[0]  # 首个切点前移到 0,避免开头黑场;音频同步裁掉这段
    shots: list[editspec.Shot] = []
    for i in range(len(cut_times) - 1):
        c = pool[i % len(pool)]
        start_f = round((cut_times[i] - t0) * fps)
        dur = max(round((cut_times[i + 1] - cut_times[i]) * fps), 1)
        is_down = cut_times[i] in downbeats
        cam_move, cam_amt = _camera(c, name)
        tr, ti = _transition(name, is_down, i, c)
        shots.append(editspec.Shot(
            id=f"{c['shot_id']}@{i}", src=sources[c["asset_id"]],
            source_in_sec=c["start_sec"], start_frame=start_f,
            duration_in_frames=dur, speed=cfg["speed"],
            effects=cfg["fx"](c, is_down),
            camera_move=cam_move, camera_amount=cam_amt,
            transition=tr, transition_intensity=ti,
            ramp=_ramp(name, c, i),
            reframe_x=c.get("reframe_x", 0.0),
        ))
    total = (shots[-1].start_frame + shots[-1].duration_in_frames) if shots else fps
    width, height, _ = editspec.choose_canvas(candidates)
    return editspec.EditSpec(
        id=project_id, fps=fps, width=width, height=height,
        duration_in_frames=total, shots=shots,
        audio=[editspec.AudioLayer(id="bgm", src=beatmap["audio"],
                                   trim_start_frames=round(t0 * fps))],
    )


def generate(project_id: str, beatmap: dict, candidates: list[dict], *,
             fps: int = 60, variants: list[str] | None = None) -> dict:
    if not candidates:
        raise ValueError("候选池为空,先 anime search 出候选")
    sources = editspec._resolve_sources(candidates)
    out = {}
    for name in (variants or list(VARIANTS)):
        spec = _build(project_id, name, beatmap, candidates, sources, fps)
        path = editspec.save(spec, name=name)
        out[name] = {"path": str(path), "shots": len(spec.shots),
                     "duration_in_frames": spec.duration_in_frames}
    return out

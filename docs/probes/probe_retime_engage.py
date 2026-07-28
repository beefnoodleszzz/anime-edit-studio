"""Explore how to actually engage Resolve's retime interpolation engine for
a clip whose native fps differs from the timeline fps but whose playback
speed should stay 100% (frame-rate up-conversion / motion smoothing, same
use case RIFE was doing). Passive AppendToTimeline did NOT engage it (see
retime_mapping_probe.json: RetimeProcess 0/1 identical, 2/3 identical only
two behaviours). Try known API entry points for "changed speed" state.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from studio.execution.resolve import connection  # noqa: E402

CLIP = pathlib.Path(
    "/private/tmp/claude-501/-Users-zhangxiaolong-Desktop-anime-edit-studio/"
    "f7ab516f-0c5e-4724-b694-af96fba0edeb/scratchpad/retime_probe_clip.mov"
)


def main() -> None:
    resolve = connection.connect()
    pm = resolve.GetProjectManager()
    pm.LoadProject("_aes_retime_probe")
    project = pm.GetCurrentProject()
    tl = project.GetCurrentTimeline() or project.GetTimelineByIndex(1)
    project.SetCurrentTimeline(tl)
    item = tl.GetItemListInTrack("video", 1)[0]

    print("== 全部方法/属性名（含 speed/retime/flow/frame）==")
    names = [m for m in dir(item) if any(k in m.lower() for k in
             ("speed", "retime", "flow", "frame", "stretch", "warp"))]
    print(names)

    print("\n== 当前状态 ==")
    for prop in ("Speed", "SpeedPercent", "RetimeProcess", "MotionEstimation"):
        try:
            print(f"  {prop} = {item.GetProperty(prop)!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {prop} ERR {exc}")
    print(f"  Duration(frames) = {item.GetDuration()}")
    print(f"  GetLeftOffset/RightOffset = {item.GetLeftOffset()}/{item.GetRightOffset()}")

    print("\n== 尝试 SetProperty('Speed', 100.0) 显式写入 ==")
    r = item.SetProperty("Speed", 100.0)
    print(f"  返回 {r}, 回读 Speed={item.GetProperty('Speed')!r} "
          f"Duration={item.GetDuration()}")

    print("\n== 尝试 SetProperty('SpeedPercent', 100.0) ==")
    r = item.SetProperty("SpeedPercent", 100.0)
    print(f"  返回 {r}, 回读 SpeedPercent={item.GetProperty('SpeedPercent')!r}")

    print("\n== GetFusionCompNames / GetNumNodes（有无隐藏retime节点） ==")
    print(f"  NumNodes={item.GetNumNodes()}")

    print("\n== timeline / project 帧率设定 ==")
    print(f"  timeline TimelineFrameRate: {project.GetSetting('timelineFrameRate')}")
    mpi = item.GetMediaPoolItem()
    print(f"  clip FPS(MediaPoolItem): {mpi.GetClipProperty('FPS')}")


if __name__ == "__main__":
    main()

"""Set up a probe timeline so the owner can pick Optical Flow / Speed Warp by
hand in the Inspector, then probe_retime_readback.py reads back the exact
RetimeProcess / MotionEstimation integers the GUI wrote.

Why manual: Phase 1.14 confirmed SetProperty('RetimeProcess', int) accepts
0-3 but never confirmed which int is which semantic mode (verified: false in
config/resolve_capabilities.yaml -> retime_interpolation_mapping). Guessing
integers and shipping a wrong interpolation mode silently is worse than
asking for one GUI click.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from studio.execution.resolve import connection  # noqa: E402

PROJECT = "_aes_retime_probe"
REPO = pathlib.Path(__file__).resolve().parent.parent.parent
CLIP = pathlib.Path(
    "/private/tmp/claude-501/-Users-zhangxiaolong-Desktop-anime-edit-studio/"
    "f7ab516f-0c5e-4724-b694-af96fba0edeb/scratchpad/retime_probe_clip.mov"
)


def main() -> None:
    resolve = connection.connect()
    pm = resolve.GetProjectManager()
    existing = pm.GetCurrentProject()
    print(f"当前工程（探测完会切回来）: {existing.GetName() if existing else None}")

    pm.DeleteProject(PROJECT)
    project = pm.CreateProject(PROJECT)
    project.SetSetting("timelineFrameRate", "47.952")
    project.SetSetting("timelineResolutionWidth", "3072")
    project.SetSetting("timelineResolutionHeight", "3072")
    project.SetSetting("timelineOutputResolutionWidth", "3072")
    project.SetSetting("timelineOutputResolutionHeight", "3072")

    mp = project.GetMediaPool()
    media = mp.ImportMedia([str(CLIP)])[0]
    tl = mp.CreateEmptyTimeline("retime-probe")
    mp.AppendToTimeline([{"mediaPoolItem": media, "trackIndex": 1,
                           "startFrame": 0, "endFrame": 71}])
    item = tl.GetItemListInTrack("video", 1)[0]
    print(f"当前 RetimeProcess={item.GetProperty('RetimeProcess')!r} "
          f"MotionEstimation={item.GetProperty('MotionEstimation')!r}")

    resolve.OpenPage("edit")
    print(
        "\n设置完成。请在 Resolve 里：\n"
        "  1. 确认在 Edit 页，选中时间线上唯一那段素材（3072x3072, ~3秒）\n"
        "  2. 打开右侧 Inspector -> Video 面板，找到 'Retime and Scaling' 区域\n"
        "  3. Retime Process 选 'Optical Flow'（先探测这个），"
        "Motion Estimation 选 'Speed Warp'（若下拉里存在这个选项）\n"
        "  4. 设置好后回来告诉我，我读取写回的整数值\n"
    )


if __name__ == "__main__":
    main()

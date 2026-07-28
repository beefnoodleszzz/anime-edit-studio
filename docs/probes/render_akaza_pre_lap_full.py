"""Render the accepted four-shot Pre-Lap model across the Akaza body section."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from studio.core.timecode import Timebase
from studio.editspec.migrations import load_migrated
from studio.execution.compiler import ResolveCompiler
from studio.execution.resolve.adapter import ResolveAdapter, ResolveOperationError

from docs.probes.probe_pre_lap_flow import _build_v1_motion, _build_v2_intrusion


ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "projects" / "akaza-matadora-v1" / "editspec.r24.json"
DB_PATH = ROOT / "library" / "engine.v2.sqlite"
OUT = ROOT / "projects" / "renders"
BODY_START = 5.642
BODY_END = 17.56
PRELAP_FRAMES = 5


def main() -> None:
    spec = load_migrated(json.loads(SPEC_PATH.read_text()))
    delivery_tb = Timebase(num=spec.timebase.num, den=spec.timebase.den)
    conn = sqlite3.connect(DB_PATH)
    assets = {
        row[0]: Path(row[1])
        for row in conn.execute("SELECT id,coalesce(proxy_path,path) FROM assets")
    }

    def resolve_asset(asset_id: str) -> Path | None:
        return assets.get(asset_id)

    def entry_for(shot_id: str | None) -> tuple[float, float]:
        if not shot_id:
            return (0.5, 0.88)
        row = conn.execute(
            "SELECT lower(coalesce(motion_dir,'')) FROM shots WHERE id=?",
            (shot_id,),
        ).fetchone()
        direction = row[0] if row else ""
        if "left" in direction:
            return (0.78, 0.62)
        if "right" in direction:
            return (0.22, 0.62)
        if "down" in direction:
            return (0.50, 0.20)
        return (0.50, 0.88)

    def stable_intrusion(shot_id: str | None) -> bool:
        if not shot_id:
            return False
        row = conn.execute(
            """
            SELECT coalesce(motion_mag,999), coalesce(face_visibility,0),
                   coalesce(blur_score,1)
            FROM shots WHERE id=?
            """,
            (shot_id,),
        ).fetchone()
        if row is None:
            return False
        motion, face, blur = (float(value) for value in row)
        return motion <= 5.1 and face >= 0.80 and blur <= 0.55

    rv = ResolveAdapter.open(auto_launch=False)
    compiler = ResolveCompiler(
        rv,
        resolve_asset,
        state_dir=SPEC_PATH.parent,
    )
    compiler.build(spec, timeline_name="main", reset_project=True)
    rv.ensure_video_tracks(2)

    ordered_clips = sorted(
        (clip for clip in spec.clips if clip.timeline.track == "V1"),
        key=lambda clip: clip.timeline.in_sec,
    )
    v1_items = sorted(rv.timeline_items(1), key=lambda item: item.GetStart())
    if len(v1_items) != len(ordered_clips):
        raise ResolveOperationError("V1 item count does not match EditSpec")
    item_by_clip = dict(zip((clip.id for clip in ordered_clips), v1_items, strict=True))

    body = [
        clip
        for clip in ordered_clips
        if BODY_START <= clip.timeline.in_sec <= BODY_END
    ]
    for index, clip in enumerate(body):
        if not stable_intrusion(clip.shot_id):
            continue
        item = item_by_clip[clip.id]
        for name in item.GetFusionCompNameList() or []:
            if not item.DeleteFusionCompByName(name):
                raise ResolveOperationError(f"cannot clear body comp: {clip.id}")
        direction = 1.0 if index % 2 == 0 else -1.0
        _build_v1_motion(item, name=f"BodyV1{index + 1}", direction=direction)

    prelap_sec = PRELAP_FRAMES / delivery_tb.fps_float
    requests = []
    target_clips = [
        clip for clip in body[1:] if stable_intrusion(clip.shot_id)
    ]
    for clip in target_clips:
        media_path = resolve_asset(clip.asset_id)
        if media_path is None:
            raise ResolveOperationError(f"asset unresolved: {clip.asset_id}")
        info = rv.import_media([media_path], bin_name="pre-lap")[str(media_path)]
        requests.append(
            {
                "media_path": media_path,
                "source_in_sec": clip.source.in_sec,
                "source_out_sec": clip.source.in_sec + prelap_sec,
                "timeline_in_sec": clip.timeline.in_sec - prelap_sec,
                "timeline_duration_sec": prelap_sec,
                "track_index": 2,
                "media_fps": info.fps,
                "timeline_fps": delivery_tb,
                "media_type": 1,
            }
        )
    v2_items = rv.append_clips(requests)
    if len(v2_items) != len(target_clips):
        raise ResolveOperationError("V2 Pre-Lap count mismatch")
    for index, (item, clip) in enumerate(zip(v2_items, target_clips, strict=True)):
        info = rv.import_media(
            [resolve_asset(clip.asset_id)], bin_name="pre-lap"
        )[str(resolve_asset(clip.asset_id))]
        cover = max(
            info.width / info.height,
            info.height / info.width,
        )
        results = rv.set_properties(
            item,
            {"ZoomX": cover, "ZoomY": cover, "ZoomGang": True},
        )
        if not all(results.values()):
            raise ResolveOperationError(f"V2 cover failed: {clip.id}")
        _build_v2_intrusion(
            item,
            name=f"FullPreLap{index + 1}",
            entry=entry_for(clip.shot_id),
        )

    if not rv._pm.SaveProject():
        raise ResolveOperationError("SaveProject failed")
    result = rv.render(
        output_dir=OUT,
        name="akaza-matadora-v1-r24-refined-pre-lap-preview",
        preset="H.264 Master",
        timeout_sec=600,
    )
    print(result.output)


if __name__ == "__main__":
    main()

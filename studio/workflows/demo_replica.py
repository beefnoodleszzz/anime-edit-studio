"""Deterministic demo-rhythm replica for the Nezuko owner-review project."""
from __future__ import annotations

from pathlib import Path

from studio.core.database import connect
from studio.editspec.schema import (
    Canvas, Clip, CutRelation, Decision, EditSpec, Marker, Retime,
    SourceRange, SourceSelection, Timebase, TimelinePlacement,
)

MAIN_MARKERS = (
    3.437, 4.133, 4.853, 5.550, 6.258, 6.966, 7.674, 8.382,
    9.091, 9.787, 10.507, 11.204, 11.923, 12.608, 13.328, 14.025, 14.745,
)
HERO_SHOTS = (
    ("front", "a4e290281f70-618"),
    ("calm", "76e39c9b5fdb-216"),
    ("attack", "93f0ce0403d8-288"),
    ("profile", "a4e290281f70-627"),
    ("eyes", "e51e5b99e462-193"),
    ("angry", "93f0ce0403d8-151"),
    ("standing", "72d187a7caed-509"),
    ("enhanced", "f55f448a05d1-315"),
    ("front", "93f0ce0403d8-370"),
    ("calm", "a4e290281f70-631"),
    ("attack", "93f0ce0403d8-218"),
    ("profile", "e51e5b99e462-183"),
    ("eyes", "93f0ce0403d8-274"),
    ("angry", "93f0ce0403d8-157"),
    ("standing", "93f0ce0403d8-148"),
    ("enhanced", "9d0c854e702e-273"),
)
SETUP_SHOTS = (
    ("setup", "a4e290281f70-647"),
    ("predrop", "a4e290281f70-635"),
)


def build_demo_replica(base_path: Path, output_path: Path) -> EditSpec:
    base = EditSpec.model_validate_json(base_path.read_text())
    conn = connect()
    try:
        rows = {
            row["id"]: row
            for row in conn.execute(
                "SELECT * FROM shots WHERE id IN (%s)"
                % ",".join("?" for _ in (*SETUP_SHOTS, *HERO_SHOTS)),
                [shot_id for _, shot_id in (*SETUP_SHOTS, *HERO_SHOTS)],
            )
        }
    finally:
        conn.close()
    boundaries = (0.0, 2.633, *MAIN_MARKERS[:-1], 14.767)
    assignments = (*SETUP_SHOTS, *HERO_SHOTS)
    clips: list[Clip] = []
    for index, ((category, shot_id), start, end) in enumerate(
        zip(assignments, boundaries[:-1], boundaries[1:], strict=True)
    ):
        row = rows[shot_id]
        duration = end - start
        available = float(row["end_sec"]) - float(row["start_sec"])
        anchor = (float(row["start_sec"]) + float(row["end_sec"])) / 2
        source_in = max(float(row["start_sec"]), anchor - duration / 2)
        source_in = min(source_in, float(row["end_sec"]) - min(duration, available))
        source_out = source_in + duration
        clips.append(
            Clip(
                id=f"demo-{index:02d}-{category}",
                asset_id=row["asset_id"],
                shot_id=shot_id,
                source=SourceRange(in_sec=source_in, out_sec=source_out),
                timeline=TimelinePlacement(in_sec=start, duration_sec=duration),
                role="opening" if index == 0 else "pre_drop" if index == 1 else "impact",
                retime=Retime(
                    type="speed_ramp" if category == "attack" else "constant",
                    speed=1.0,
                    entry_speed=0.5,
                    impact_speed=2.0,
                    exit_speed=0.5,
                    impact_at_sec=min(duration / 2, 0.36),
                    interpolation="optical_flow",
                ),
                color=base.clips[0].color,
                decision=Decision(
                    source="rule", confidence=1.0,
                    reasoning=f"demo replica hero category: {category}",
                ),
                incoming_cut=CutRelation(
                    kind="establish" if index == 0 else "continuation" if index == 1 else "contrast",
                    motivation=f"demo replica {category}",
                    confidence=1.0,
                    matched_features=[f"hero_category:{category}"],
                ),
                source_selection=SourceSelection(
                    phase="impact" if category == "attack" else "representative",
                    anchor_sec=anchor,
                    confidence=1.0,
                    evidence=[f"hero_category:{category}", f"shot:{shot_id}"],
                ),
            )
        )
    markers = [
        Marker(sec=value, kind="demo_replica_impact", note="primary visual impact")
        for value in MAIN_MARKERS
    ]
    for clip in clips[2:]:
        category = clip.id.rsplit("-", 1)[-1]
        markers.append(
            Marker(
                sec=clip.timeline.in_sec,
                duration_sec=clip.timeline.duration_sec,
                kind="demo_replica_clip",
                note=f"hero_category:{category}",
                clip_id=clip.id,
            )
        )
    spec = base.model_copy(
        update={
            "id": "nezuko-demo-replica-v1",
            "revision": 1,
            "timebase": Timebase(num=30, den=1),
            "canvas": Canvas(width=1080, height=1080, aspect="1:1"),
            "clips": clips,
            "motion_phrases": [],
            "markers": markers,
            "audio": [
                base.audio[0].model_copy(
                    update={
                        "path": str(
                            (base_path.parent / "uploads" / "music.wav").resolve()
                        ),
                        "duration_sec": 14.767,
                    }
                )
            ],
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(spec.model_dump_json(by_alias=True, indent=2))
    return spec

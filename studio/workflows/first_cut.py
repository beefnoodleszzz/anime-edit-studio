"""Music/reference to persisted DirectorPlan, A/B/C groups, and EditSpec."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from studio.core.database import DEFAULT_V2_DB, connect
from studio.critic.creative import (
    evaluate_edit_grammar,
    evaluate_rhythm,
    evaluate_sound_design,
)
from studio.creative.director import DirectorBrief, generate_director_plan
from studio.creative.preference import PreferenceModel, preference_signal
from studio.creative.reference import analyze_reference
from studio.editing.candidates import create_group, generate_review_assets
from studio.editing.music import analyze_music
from studio.editing.ranking import CandidateContext, rank_candidates
from studio.editing.retrieval import RetrievalQuery, retrieve
from studio.asset_intelligence.motion import load_action_peaks
from studio.editing.sequence import (
    canonical_role,
    apply_recipe_plan,
    plan_sequence,
    role_source_duration_requirements,
    plan_visual_phrases,
)
from studio.editing.camera import assign_camera_moves
from studio.editing.readiness import (
    ProductionNotReady,
    evaluate_production_readiness,
)
from studio.editing.timing import apply_action_sync
from studio.editing.sound import apply_sound_design
from studio.editspec.schema import AudioLayer, EditSpec, Marker, Timebase
from studio.execution.ffmpeg import DELIVERY_TARGET_LUFS, measure_integrated_lufs

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "library" / "cache"


class FirstCutResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    plan_path: str
    spec_path: str
    style_profile_path: str
    rhythm_qa_path: str
    edit_grammar_qa_path: str
    visual_phrase_path: str
    cut_accuracy_path: str
    camera_assignment_path: str | None = None
    sound_design_path: str | None = None
    candidate_group_ids: list[str]
    clip_count: int
    duration_sec: float


def _load_action_peaks(conn, *, shot_ids: list[str]) -> tuple[dict, dict]:
    """Fetch measured action peaks and shot starts for the planned clips."""
    peaks_by_shot: dict[str, list] = {}
    shot_starts: dict[str, float] = {}
    unique = sorted(set(shot_ids))
    if not unique:
        return peaks_by_shot, shot_starts
    placeholders = ",".join("?" for _ in unique)
    rows = conn.execute(
        f"SELECT id,start_sec,action_peaks FROM shots WHERE id IN ({placeholders})",
        unique,
    ).fetchall()
    for row in rows:
        shot_starts[row["id"]] = float(row["start_sec"])
        peaks = load_action_peaks(row["action_peaks"])
        if peaks:
            peaks_by_shot[row["id"]] = peaks
    return peaks_by_shot, shot_starts


def _reference_timebase(reference_path: Path | None) -> Timebase | None:
    """Deliver at the reference's native frame rate.

    A whip edit's fluidity and motion blur read differently at 30fps than at
    23.976; matching the reference (a.mp4 is 30fps) is part of learning its
    method, not a cosmetic choice.  ``r_frame_rate`` is an exact rational, so the
    timebase stays precise (30/1, 24000/1001, 30000/1001, …).
    """
    if reference_path is None:
        return None
    try:
        import subprocess

        raw = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate", "-of",
                "default=noprint_wrappers=1:nokey=1", str(reference_path),
            ],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        num_s, _, den_s = raw.partition("/")
        num, den = int(num_s), int(den_s or "1")
        if num <= 0 or den <= 0:
            return None
        return Timebase(num=num, den=den)
    except Exception:  # noqa: BLE001
        return None


def _tone_allows_menacing_expression(tone: list[str] | None) -> bool:
    """Keep villain performance signals when the director explicitly asks for them."""
    normalized = {item.strip().lower() for item in (tone or [])}
    return bool(normalized & {"menacing", "dominant", "villainous", "ferocious"})


def _write_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def create_first_cut(
    *,
    project_id: str,
    music_path: Path,
    duration_sec: float,
    reference_path: Path | None = None,
    primary_characters: list[str] | None = None,
    tone: list[str] | None = None,
    database: Path = DEFAULT_V2_DB,
    output_dir: Path | None = None,
    reuse_candidate_groups: bool = False,
    naked_cut: bool = False,
) -> FirstCutResult:
    if not music_path.is_file():
        raise FileNotFoundError(f"音乐不存在: {music_path}")
    if reference_path is not None and not reference_path.is_file():
        raise FileNotFoundError(f"参考片不存在: {reference_path}")
    root = output_dir or REPO / "projects" / project_id
    conn = connect(database)
    try:
        music = analyze_music(music_path, cache_root=CACHE)
        style = (
            analyze_reference(reference_path, cache_root=CACHE)
            if reference_path is not None
            else None
        )
        _write_atomic(root / "music_map.json", music.model_dump_json(indent=2))
        if style is not None:
            _write_atomic(
                root / "style_fingerprint.json",
                style.model_dump_json(indent=2),
            )
        plan_path = root / "director_plan.yaml"
        plan = generate_director_plan(
            DirectorBrief(
                project_id=project_id,
                duration_sec=duration_sec,
                primary_characters=primary_characters or [],
                tone=tone or [],
            ),
            music,
            style,
            conn=conn,
            output_path=plan_path,
        )
        style_profile_path = root / "editing_style_profile.json"
        _write_atomic(
            style_profile_path,
            plan.editing_style.model_dump_json(indent=2),
        )
        visual_phrase_path = root / "visual_phrase_plan.json"
        visual_phrase = plan_visual_phrases(
            music, duration_sec=plan.duration_sec
        )
        _write_atomic(
            visual_phrase_path,
            visual_phrase.model_dump_json(indent=2),
        )
        candidates_by_role = {}
        group_ids = []
        duration_requirements = role_source_duration_requirements(plan, music)
        active_groups = {
            row["role"]: row
            for row in conn.execute(
                """
                SELECT id,role,shot_ids_json,selected_shot_id
                FROM candidate_groups
                WHERE project_id=? AND active=1
                """,
                (project_id,),
            )
        } if reuse_candidate_groups else {}
        model_row = conn.execute(
            """
            SELECT model_json FROM preference_models
            WHERE scope IN (?, 'global')
            ORDER BY CASE WHEN scope=? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (f"project:{project_id}", f"project:{project_id}"),
        ).fetchone()
        preference_model = (
            PreferenceModel.model_validate_json(model_row["model_json"])
            if model_row else None
        )
        for section in plan.structure:
            role = canonical_role(section.role)
            if role in candidates_by_role:
                continue
            front_facing = "front_facing" in (tone or [])
            menacing_expression = _tone_allows_menacing_expression(tone)
            character = (primary_characters or [None])[0]
            zenitsu = character == "agatsuma_zenitsu"
            query = RetrievalQuery(
                project_id=project_id,
                character=character,
                subtitle_allowed=False,
                # A high face threshold over-selects face-forward close-ups and
                # suppresses the stronger medium/wide action compositions.  This
                # starves the impact role for action-forward characters whose
                # dynamic footage is framed wide (Zenitsu's speed, an action
                # villain's fights), so relax the gate for those.
                min_face=0.30 if (zenitsu or menacing_expression) else 0.40,
                min_pose=0.30,
                min_eye=0.10,
                min_image_quality=0.72,
                min_cutability=0.42,
                min_aesthetic=4.5,
                required_any_tags=[],
                excluded_tags=[
                    "fake_screenshot",
                    "chibi",
                    "parody",
                    "muscular",
                    "monochrome",
                    "logo",
                    "watermark",
                    "twitter_username",
                    "artist_name",
                    "credits",
                    "black_background",
                    *(
                        [
                            "bubble_blowing",
                            "nose_bubble",
                            "meme",
                            "heart",
                            "snot",
                            "drooling",
                            "fangs",
                            "yellow_sweater",
                            "school_uniform",
                            "holding_hair",
                            "grabbing_another's_hair",
                            "crawling",
                            "death",
                            "corpse",
                            "topless_male",
                            "open_mouth",
                            "wide-eyed",
                            "teeth",
                            "clenched_teeth",
                            "anger_vein",
                            "horror_(theme)",
                            "spider",
                        ]
                        if zenitsu else []
                    ),
                    "1girl",
                    "2girls",
                    "multiple_girls",
                    *(
                        []
                        if zenitsu
                        else [
                            "multiple_boys",
                            "2boys",
                            "kamado_nezuko",
                            "topless_male",
                        ]
                    ),
                    "lying",
                    "head_out_of_frame",
                    "upside-down",
                    "upside_down",
                    "blood_on_face",
                    "one_eye_closed",
                    "crying",
                    "crying_with_eyes_open",
                    "tears",
                    "sad",
                    "scared",
                    *(
                        []
                        if zenitsu or menacing_expression
                        else [
                            "closed_eyes",
                            "wide-eyed",
                            "open_mouth",
                            "sweat",
                            "sweatdrop",
                            "blood",
                        ]
                    ),
                    "bandages",
                    "bandaged_head",
                    "from_behind",
                    "facing_away",
                    "head_out_of_frame",
                    *(
                        [
                            "looking_away",
                            "looking_back",
                            "from_behind",
                            "facing_away",
                        ]
                        if front_facing else []
                    ),
                ],
                min_duration_sec=duration_requirements[role],
                max_duration_sec=10.0,
                limit=200,
            )
            recalled = retrieve(conn, query)
            preference_by_shot = {}
            if preference_model is not None and recalled:
                placeholders = ",".join("?" for _ in recalled)
                preference_by_shot = {
                    row["id"]: preference_signal(preference_model, row)
                    for row in conn.execute(
                        f"SELECT * FROM shots WHERE id IN ({placeholders})",
                        recalled,
                    )
                }
            ranked = rank_candidates(
                conn,
                recalled,
                CandidateContext(
                    project_id=project_id,
                    role=role,
                    target_energy=section.energy,
                    target_shot_scale=0.56 if front_facing else None,
                    character=(primary_characters or [None])[0],
                    preference_by_shot=preference_by_shot,
                ),
                limit=200,
            )
            existing = active_groups.get(role)
            if existing is not None and existing["selected_shot_id"]:
                selected_id = existing["selected_shot_id"]
                if selected_id not in {item.shot_id for item in ranked}:
                    selected_rank = rank_candidates(
                        conn,
                        [selected_id],
                        CandidateContext(
                            project_id=project_id,
                            role=role,
                            target_energy=section.energy,
                            target_shot_scale=0.56 if front_facing else None,
                            character=(primary_characters or [None])[0],
                            preference_by_shot=preference_by_shot,
                        ),
                        limit=1,
                    )
                    if not selected_rank:
                        raise ValueError(f"{role} 已选镜头不再可用: {selected_id}")
                    ranked.extend(selected_rank)
            if len(ranked) < 3:
                raise ValueError(f"{section.role} 候选不足 3 个")
            candidates_by_role[role] = ranked
            if existing is not None:
                group_ids.append(existing["id"])
                continue
            group = create_group(
                conn,
                project_id=project_id,
                role=role,
                ranked=ranked,
                plan_revision=plan.revision,
            )
            group_ids.append(group.id)
            generate_review_assets(conn, group, output_dir=root / "previews")
        # R11 gate: refuse before planning, not after rendering.  This runs on
        # the assembled candidate pool because that is where the failure it
        # guards against is visible — a name that resolved to nothing, or a pool
        # that spans several works because the query matched a look.
        readiness = evaluate_production_readiness(
            conn,
            project_id=project_id,
            character=(primary_characters or [None])[0],
            candidate_shot_ids=sorted(
                {
                    item.shot_id
                    for ranked in candidates_by_role.values()
                    for item in ranked
                }
            ),
        )
        _write_atomic(
            root / "production_readiness.json", readiness.model_dump_json(indent=2)
        )
        if not readiness.ready:
            raise ProductionNotReady(readiness)
        selected_by_role = {
            row["role"]: row["selected_shot_id"]
            for row in conn.execute(
                """
                SELECT role,selected_shot_id FROM candidate_groups
                WHERE project_id=? AND active=1 AND selected_shot_id IS NOT NULL
                """,
                (project_id,),
            )
        }
        delivery_timebase = _reference_timebase(reference_path)
        plan_kwargs = {}
        if delivery_timebase is not None:
            plan_kwargs["timebase"] = delivery_timebase
        spec = plan_sequence(
            conn,
            plan=plan,
            music=music,
            candidates_by_role=candidates_by_role,
            selected_by_role=selected_by_role,
            **plan_kwargs,
        )
        spec.audio.append(
            AudioLayer(
                id="music-main",
                path=str(music_path),
                track="A1",
                duration_sec=plan.duration_sec,
                gain_db=max(
                    -12.0,
                    min(
                        12.0,
                        DELIVERY_TARGET_LUFS
                        - measure_integrated_lufs(music_path),
                    ),
                ),
            )
        )
        spec.markers.extend(
            Marker(
                sec=item.start,
                duration_sec=min(item.end, plan.duration_sec) - item.start,
                kind="expected_silence",
                note="MusicMap intentional silence",
            )
            for item in music.silences
            if item.start < plan.duration_sec and min(item.end, plan.duration_sec) > item.start
        )
        spec.markers.extend(
            Marker(sec=sec, kind="impact", note="MusicMap impact point")
            for sec in music.impact_points
            if sec <= plan.duration_sec
        )
        if not naked_cut:
            spec = apply_recipe_plan(spec, plan=plan)
        # Action Sync: land measured action peaks on musical targets and emit a
        # deterministic acceptance table.  Runs after recipe planning so it can
        # override the style-density speed ramp with a peak-anchored one.
        peaks_by_shot, shot_starts = _load_action_peaks(
            conn, shot_ids=[clip.shot_id for clip in spec.clips]
        )
        spec, cut_accuracy = apply_action_sync(
            spec,
            music=music,
            peaks_by_shot=peaks_by_shot,
            shot_starts=shot_starts,
        )
        cut_accuracy_path = root / "cut_accuracy_report.json"
        _write_atomic(cut_accuracy_path, cut_accuracy.model_dump_json(indent=2))
        # Camera assignment: give every shot that still owns its Fusion slot a
        # move derived from its own measured flow.  Runs last among the motion
        # stages so it can see which clips recipe planning and Action Sync
        # already claimed, and skip those instead of writing a silent no-op.
        camera_assignment_path = None
        if not naked_cut:
            spec, camera_assignment = assign_camera_moves(
                spec, conn=conn, music=music
            )
            camera_assignment_path = root / "camera_assignment.json"
            _write_atomic(
                camera_assignment_path,
                camera_assignment.model_dump_json(indent=2),
            )
        # Sound design: lay a designed three-piece SFX layer on the drum
        # targets, then score it.  Runs after Action Sync so cues key off the
        # final cut/retime timing.
        if not naked_cut:
            spec, sound_design = apply_sound_design(spec, music=music, plan=plan)
            sound_design_path = root / "sound_design.json"
            _write_atomic(sound_design_path, sound_design.model_dump_json(indent=2))
            sound_qa = evaluate_sound_design(spec, music)
            sound_qa_path = root / "sound_qa.json"
            _write_atomic(sound_qa_path, sound_qa.model_dump_json(indent=2))
        else:
            sound_design_path = None
        # Revalidate after attaching the external audio layer.
        spec = EditSpec.model_validate(spec.model_dump(mode="python", by_alias=True))
        with conn:
            conn.execute(
                "UPDATE edit_specs SET spec_json=? WHERE project_id=? AND version=?",
                (spec.model_dump_json(by_alias=True), project_id, spec.revision),
            )
        spec_path = root / "editspec.json"
        _write_atomic(spec_path, spec.model_dump_json(by_alias=True, indent=2))
        rhythm_qa = evaluate_rhythm(spec, music, plan.editing_style)
        rhythm_qa_path = root / "rhythm_qa.json"
        _write_atomic(rhythm_qa_path, rhythm_qa.model_dump_json(indent=2))
        edit_grammar_qa = evaluate_edit_grammar(spec)
        edit_grammar_qa_path = root / "edit_grammar_qa.json"
        _write_atomic(
            edit_grammar_qa_path,
            edit_grammar_qa.model_dump_json(indent=2),
        )
        return FirstCutResult(
            project_id=project_id,
            plan_path=str(plan_path),
            spec_path=str(spec_path),
            style_profile_path=str(style_profile_path),
            rhythm_qa_path=str(rhythm_qa_path),
            edit_grammar_qa_path=str(edit_grammar_qa_path),
            visual_phrase_path=str(visual_phrase_path),
            cut_accuracy_path=str(cut_accuracy_path),
            camera_assignment_path=(
                str(camera_assignment_path) if camera_assignment_path else None
            ),
            sound_design_path=str(sound_design_path) if sound_design_path else None,
            candidate_group_ids=group_ids,
            clip_count=len(spec.clips),
            duration_sec=spec.duration_sec,
        )
    finally:
        conn.close()

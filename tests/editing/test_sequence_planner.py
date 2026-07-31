from studio.core.database import connect
from studio.creative.director.plan import (
    DirectorPlan,
    DirectorSection,
    ImpactBudget,
)
from studio.creative.reference import EditingStyleProfile
from studio.editing.music.map import MusicMap, MusicSection
from studio.editing.ranking import RankedCandidate
from studio.editing.sequence import (
    plan_sequence,
    planned_rhythm_metrics,
    role_source_duration_requirements,
)
from studio.editing.sequence.planner import (
    MIN_REPEAT_GAP,
    _cut_relation,
    _source_intent,
    _source_window,
    _slots,
    _snap_cuts_to_music,
)


def test_sequence_planner_uses_beam_and_emits_versioned_spec(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,20)"
        )
        conn.executemany(
            """
            INSERT INTO shots(
              id,asset_id,idx,start_sec,end_sec,character,motion_dir,
              shot_scale,visual_energy
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    f"s{i}", "a", i, i * 3, i * 3 + 2.5,
                    "tanjiro", "right" if i % 2 else "left",
                    0.2 + i * 0.1, 0.3 + i * 0.1,
                )
                for i in range(5)
            ],
        )
    plan = DirectorPlan(
        project_id="p",
        revision=1,
        duration_sec=3,
        primary_characters=["tanjiro"],
        tone=["cinematic"],
        structure=[
            DirectorSection(
                role="buildup", start=0, end=3, energy=0.6,
                average_shot_length=1,
            )
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    music = MusicMap(
        duration_sec=3,
        bpm=120,
        beats=[0, 0.5, 1, 1.5, 2, 2.5],
        bars=[0],
        downbeats=[0],
        onsets=[],
        beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=3, energy=0.6)],
        impact_points=[],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )
    candidates = [
        RankedCandidate(
            shot_id=f"s{i}",
            intrinsic=0.7,
            contextual=0.8 - i * 0.02,
            total=0.75 - i * 0.01,
            intrinsic_components={},
            contextual_components={},
        )
        for i in range(5)
    ]
    spec = plan_sequence(
        conn, plan=plan, music=music, candidates_by_role={"build": candidates}
    )
    assert len(spec.clips) == 3
    assert len({clip.shot_id for clip in spec.clips}) == 3
    assert spec.duration_sec == 3
    assert (spec.canvas.width, spec.canvas.height, spec.canvas.aspect) == (
        1080, 1080, "1:1",
    )
    assert all(clip.role == "build" for clip in spec.clips)
    assert all(clip.decision.alternatives for clip in spec.clips)
    assert spec.clips[0].incoming_cut.kind == "establish"
    assert all(clip.incoming_cut is not None for clip in spec.clips)
    assert all(clip.source_selection is not None for clip in spec.clips)
    assert all(
        clip.source.in_sec <= clip.source_selection.anchor_sec <= clip.source.out_sec
        for clip in spec.clips
    )
    assert conn.execute("SELECT count(*) FROM edit_specs").fetchone()[0] == 1
    requirements = role_source_duration_requirements(plan, music)
    assert requirements["build"] >= 1


def test_thin_pool_holds_rhythm_by_reuse_not_long_shots(tmp_path):
    """A thin pool must keep the beat-locked cut density by reusing shots on
    later beats, never by hanging one shot across many beats (the slideshow bug).
    """
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,40)"
        )
        # Only 6 distinct shots, each long enough to satisfy any short slot.
        conn.executemany(
            """
            INSERT INTO shots(
              id,asset_id,idx,start_sec,end_sec,character,motion_dir,
              shot_scale,visual_energy,motion_mag
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    f"s{i}", "a", i, i * 4, i * 4 + 3.5, "akaza",
                    "right" if i % 2 else "left", 0.3 + i * 0.08,
                    0.4 + i * 0.08, 0.5 + i * 0.05,
                )
                for i in range(6)
            ],
        )
    plan = DirectorPlan(
        project_id="thin", revision=1, duration_sec=8,
        primary_characters=["akaza"], tone=["aggressive"],
        structure=[
            DirectorSection(
                role="drop", start=0, end=8, energy=0.8, average_shot_length=0.5,
            )
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=2, flash_max=2, shake_max=2),
        generation={"llm_used": False},
    )
    beats = [round(0.5 * i, 2) for i in range(16)]
    music = MusicMap(
        duration_sec=8, bpm=120, beats=beats, bars=[0], downbeats=[0, 2, 4, 6],
        onsets=[], beat_energy=[],
        sections=[MusicSection(type="drop", start=0, end=8, energy=0.8)],
        impact_points=[2, 4, 6], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    candidates = [
        RankedCandidate(
            shot_id=f"s{i}", intrinsic=0.7, contextual=0.8 - i * 0.02,
            total=0.75 - i * 0.01, intrinsic_components={}, contextual_components={},
        )
        for i in range(6)
    ]
    spec = plan_sequence(
        conn, plan=plan, music=music, candidates_by_role={"impact": candidates}
    )
    # Density is held: far more clips than the 6-shot pool, not collapsed to it.
    assert len(spec.clips) >= 10
    # No slideshow: every shot stays short (the bug hung shots at 5.5s).
    assert all(clip.timeline.duration_sec <= 1.5 for clip in spec.clips)
    # Reuse happened, but never within the repeat gap.
    ordered = sorted(spec.clips, key=lambda c: c.timeline.in_sec)
    ids = [c.shot_id for c in ordered]
    assert len(ids) > len(set(ids))
    for i, shot in enumerate(ids):
        assert shot not in ids[max(0, i - MIN_REPEAT_GAP):i]


def test_forced_role_shot_is_not_consumed_by_an_earlier_role(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,20)"
        )
        conn.executemany(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,character,"
            "motion_dir,shot_scale,visual_energy) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (f"s{i}", "a", i, i * 3, i * 3 + 2.5, "tanjiro", "right", .5, .5)
                for i in range(3)
            ],
        )
    plan = DirectorPlan(
        project_id="reserved",
        revision=1,
        duration_sec=2,
        primary_characters=["tanjiro"],
        tone=["clean"],
        structure=[
            DirectorSection(role="opening", start=0, end=1, energy=.5, average_shot_length=1),
            DirectorSection(role="ending", start=1, end=2, energy=.5, average_shot_length=1),
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    music = MusicMap(
        duration_sec=2, bpm=120, beats=[0, .5, 1, 1.5], bars=[0],
        downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=2, energy=.5)],
        impact_points=[], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    ranked = [
        RankedCandidate(
            shot_id=f"s{i}", intrinsic=.8, contextual=.8,
            total=.9 - i * .1, intrinsic_components={}, contextual_components={},
        )
        for i in range(3)
    ]
    spec = plan_sequence(
        conn,
        plan=plan,
        music=music,
        candidates_by_role={"opening": ranked, "ending": ranked},
        selected_by_role={"ending": "s0"},
    )
    assert spec.clips[0].shot_id != "s0"
    assert spec.clips[1].shot_id == "s0"


def test_planner_preserves_long_source_for_later_long_slot(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,20)"
        )
        conn.executemany(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,character,"
            "motion_dir,shot_scale,visual_energy) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("short", "a", 0, 0, 1.05, "zenitsu", "right", .5, .5),
                ("long", "a", 1, 2, 4.0, "zenitsu", "right", .5, .5),
            ],
        )
    plan = DirectorPlan(
        project_id="duration-conservation", revision=1, duration_sec=2.5,
        primary_characters=["zenitsu"], tone=["clean"],
        structure=[
            DirectorSection(
                role="opening", start=0, end=2.5, energy=.5,
                average_shot_length=1.25,
            )
        ],
        visual_rules={"prefer": [], "avoid": []}, sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=0, flash_max=0, shake_max=0),
        generation={"llm_used": False},
        editing_style=EditingStyleProfile(
            target_cut_density=.4,
            normalized_cut_positions=[.4],
        ),
    )
    music = MusicMap(
        duration_sec=2.5, bpm=120, beats=[], bars=[], downbeats=[],
        onsets=[], beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=2.5, energy=.5)],
        impact_points=[], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    ranked = [
        RankedCandidate(
            shot_id=shot_id, intrinsic=.8, contextual=.8, total=total,
            intrinsic_components={}, contextual_components={},
        )
        for shot_id, total in [("long", .9), ("short", .89)]
    ]
    spec = plan_sequence(
        conn, plan=plan, music=music,
        candidates_by_role={"opening": ranked}, beam_width=1,
    )
    assert [clip.shot_id for clip in spec.clips] == ["short", "long"]


def test_source_window_is_centered_on_scored_keyframe(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,20)"
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,keyframe,"
            "character,motion_dir,shot_scale,visual_energy) "
            "VALUES ('s','a',0,10,20,'shot_0000_c4.jpg','tanjiro','right',.5,.5)"
        )
    plan = DirectorPlan(
        project_id="window", revision=1, duration_sec=2,
        primary_characters=["tanjiro"], tone=["clean"],
        structure=[
            DirectorSection(role="opening", start=0, end=2, energy=.5, average_shot_length=2)
        ],
        visual_rules={"prefer": [], "avoid": []}, sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    music = MusicMap(
        duration_sec=2, bpm=120, beats=[0, .5, 1, 1.5], bars=[0],
        downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=2, energy=.5)],
        impact_points=[], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    candidate = RankedCandidate(
        shot_id="s", intrinsic=.8, contextual=.8, total=.8,
        intrinsic_components={}, contextual_components={},
    )
    spec = plan_sequence(
        conn, plan=plan, music=music, candidates_by_role={"opening": [candidate]}
    )
    assert spec.clips[0].source.in_sec == 17.5
    assert spec.clips[0].source.out_sec == 19.5


def test_density_prune_does_not_reopen_a_max_shot_length_gap():
    """A section whose pattern cuts all sit off-anchor must not be starved.

    Regression for the "踩点失败" bug: the global density-budget prune in
    ``_slots`` ranks every candidate cut by proximity to a beat/impact anchor
    and keeps only the top ``target_cut_count``. If one section's candidates
    are all farther from an anchor than another section's, the prune can keep
    zero cuts from it — silently reopening a gap far past
    ``max_shot_length``, even though the earlier gap-subdivision loop closed
    it. Here every "carry" cut sits exactly on a beat (distance 0) and every
    "impact" cut sits 1s+ from the nearest anchor, so a tiny cut budget
    starves "impact" entirely unless the prune step repairs the gap it opens.
    """
    profile = EditingStyleProfile(
        source="reference",
        target_cut_density=0.3,
        max_shot_length=1.5,
        min_shot_length=0.3,
        duration_pattern=[1.0],
        hook_event_count=1,
        ending_duration_ratio=0.08,
        ending_deceleration_pattern=[1.0, 1.0],
        beat_sync_target=0.0,
        beat_grid_subdivision="adaptive",
    )
    plan = DirectorPlan(
        project_id="gap-repro", revision=1, duration_sec=20,
        primary_characters=[], tone=[],
        structure=[
            DirectorSection(
                role="carry", start=0, end=10, energy=.5,
                average_shot_length=0.3,
            ),
            DirectorSection(
                role="impact", start=10, end=20, energy=.8,
                average_shot_length=0.3,
            ),
        ],
        visual_rules={"prefer": [], "avoid": []}, sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
        editing_style=profile,
    )
    music = MusicMap(
        duration_sec=20, bpm=120,
        beats=[1, 2, 3, 4, 5, 6, 7, 8, 9], bars=[0], downbeats=[0],
        onsets=[], beat_energy=[],
        sections=[
            MusicSection(type="carry", start=0, end=10, energy=.5),
            MusicSection(type="impact", start=10, end=20, energy=.8),
        ],
        impact_points=[15.4], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    slots = _slots(plan, music)
    maximum = min(1.2, profile.max_shot_length)
    assert max(slot.duration for slot in slots) <= maximum + 1e-6
    assert any(slot.role == "impact" for slot in slots)


def test_reference_positions_keep_measured_density_with_music_and_sections():
    """A same-duration reference owns cadence, including its long opening hold."""
    profile = EditingStyleProfile(
        source="reference",
        target_cut_density=0.4,
        normalized_cut_positions=[0.25, 0.5, 0.75],
        min_shot_length=0.4,
        max_shot_length=0.8,
        beat_sync_target=0.0,
        beat_grid_subdivision="section_1_2_4",
    )
    plan = DirectorPlan(
        project_id="reference-density",
        revision=1,
        duration_sec=10,
        primary_characters=[],
        tone=[],
        structure=[
            DirectorSection(
                role="opening", start=0, end=3, energy=.4,
                average_shot_length=1,
            ),
            DirectorSection(
                role="impact", start=3, end=7, energy=.9,
                average_shot_length=.5,
            ),
            DirectorSection(
                role="ending", start=7, end=10, energy=.4,
                average_shot_length=1,
            ),
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
        editing_style=profile,
    )
    music = MusicMap(
        duration_sec=10,
        bpm=120,
        beats=[value / 2 for value in range(20)],
        bars=[0],
        downbeats=[0],
        onsets=[],
        beat_energy=[],
        sections=[MusicSection(type="drop", start=0, end=10, energy=.8)],
        impact_points=[],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )

    slots = _slots(plan, music)

    # Comparable references own all measured boundaries. Director sections
    # classify the resulting slots but do not inject new cuts into the cadence.
    assert [slot.start for slot in slots] == [0.0, 2.5, 5.0, 7.5]
    assert max(slot.duration for slot in slots) >= 2.0


def test_music_snap_does_not_collapse_distinct_reference_cuts():
    profile = EditingStyleProfile(
        source="reference",
        beat_sync_target=1.0,
    )
    plan = DirectorPlan(
        project_id="snap-density",
        revision=1,
        duration_sec=3,
        primary_characters=[],
        tone=[],
        structure=[
            DirectorSection(
                role="opening", start=0, end=3, energy=.5,
                average_shot_length=1,
            ),
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=0, flash_max=0, shake_max=0),
        generation={"llm_used": False},
        editing_style=profile,
    )
    music = MusicMap(
        duration_sec=3,
        bpm=120,
        beats=[1.0, 1.5, 2.0],
        bars=[],
        downbeats=[],
        onsets=[],
        beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=3, energy=.5)],
        impact_points=[],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )

    snapped = _snap_cuts_to_music(
        [0.9, 1.1, 1.9],
        plan=plan,
        music=music,
        profile=profile,
    )

    assert len(set(snapped)) == 3


def test_comparable_reference_preserves_exact_normalized_cut_skeleton():
    """Measured reference boundaries own timing, including intentional microcuts."""
    profile = EditingStyleProfile(
        source="reference",
        target_cut_density=0.4,
        normalized_cut_positions=[0.2, 0.4, 0.42, 0.8],
        min_shot_length=0.35,
        max_shot_length=1.0,
        beat_sync_target=1.0,
        beat_grid_subdivision="section_1_2_4",
    )
    plan = DirectorPlan(
        project_id="reference-skeleton",
        revision=1,
        duration_sec=10,
        primary_characters=[],
        tone=["vibe"],
        structure=[
            DirectorSection(
                role="opening", start=0, end=3, energy=.4,
                average_shot_length=1,
            ),
            DirectorSection(
                role="impact", start=3, end=7, energy=.9,
                average_shot_length=.5,
            ),
            DirectorSection(
                role="ending", start=7, end=10, energy=.4,
                average_shot_length=1,
            ),
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
        editing_style=profile,
    )
    music = MusicMap(
        duration_sec=10,
        bpm=120,
        beats=[value / 2 for value in range(20)],
        bars=[0],
        downbeats=[0],
        onsets=[3.0, 7.0],
        beat_energy=[],
        sections=[
            MusicSection(type="intro", start=0, end=3, energy=.4),
            MusicSection(type="drop", start=3, end=7, energy=.9),
            MusicSection(type="release", start=7, end=10, energy=.4),
        ],
        impact_points=[3.0, 7.0],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )

    slots = _slots(plan, music)

    assert [slot.start for slot in slots[1:]] == [2.0, 4.0, 4.2, 8.0]


def test_reference_cut_vectors_become_carry_and_reverse_slot_intents():
    profile = EditingStyleProfile(
        source="reference",
        normalized_cut_positions=[0.25, 0.5, 0.75],
        cut_carry_vectors=[
            {"same_direction": True},
            {"same_direction": False},
            {"same_direction": True},
        ],
    )
    plan = DirectorPlan(
        project_id="reference-intents",
        revision=1,
        duration_sec=4,
        primary_characters=[],
        tone=[],
        structure=[
            DirectorSection(
                role="impact", start=0, end=4, energy=.8,
                average_shot_length=1,
            ),
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=0, flash_max=0, shake_max=0),
        generation={"llm_used": False},
        editing_style=profile,
    )
    music = MusicMap(
        duration_sec=4,
        bpm=0,
        beats=[],
        bars=[],
        downbeats=[],
        onsets=[],
        beat_energy=[],
        sections=[MusicSection(type="drop", start=0, end=4, energy=.8)],
        impact_points=[],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )

    assert [slot.intent for slot in _slots(plan, music)] == [
        "establish", "carry", "reverse", "carry",
    ]


def _relation_row(**updates):
    return {
        "action": None,
        "emotion": None,
        "tags": None,
        "motion_dir": "static",
        "shot_scale": .5,
        "brightness": .5,
        "character": "nezuko",
        **updates,
    }


def test_cut_relation_does_not_claim_graphic_match_without_geometry_evidence():
    relation = _cut_relation(
        _relation_row(shot_scale=.52, brightness=.48),
        _relation_row(shot_scale=.5, brightness=.5),
        intent="carry",
    )

    assert relation.kind == "continuation"
    assert "shot_scale_similarity" in relation.matched_features
    assert "brightness_similarity" in relation.matched_features


def test_cut_relation_records_directional_carry_and_true_reversal():
    carry = _cut_relation(
        _relation_row(motion_dir="pan_right"),
        _relation_row(motion_dir="right"),
        intent="carry",
    )
    reversal = _cut_relation(
        _relation_row(motion_dir="left"),
        _relation_row(motion_dir="right"),
        intent="reverse",
    )
    false_reversal = _cut_relation(
        _relation_row(motion_dir="right"),
        _relation_row(motion_dir="right"),
        intent="reverse",
    )

    assert carry.kind == "match_action"
    assert "motion_direction:right" in carry.matched_features
    assert reversal.kind == "match_action"
    assert "motion_reversal:left->right" in reversal.matched_features
    assert false_reversal.kind != "match_action"


def test_carry_cut_intent_does_not_invent_an_action_source_phase():
    row = _relation_row(
        start_sec=10,
        end_sec=14,
        keyframe="shot_0001_c2.jpg",
    )

    _, _, selection = _source_window(row, 1.0, intent="carry")

    assert selection.phase == "representative"
    assert "action_semantics" not in selection.evidence


def test_source_intent_comes_from_narrative_role_not_cut_direction():
    assert _source_intent(
        type("Slot", (), {"role": "pre_drop", "intent": "reverse"})()
    ) == "anticipation"
    assert _source_intent(
        type("Slot", (), {"role": "impact", "intent": "carry"})()
    ) == "impact"
    assert _source_intent(
        type("Slot", (), {"role": "release", "intent": "reverse"})()
    ) == "hold"
    assert _source_intent(
        type("Slot", (), {"role": "ending", "intent": "carry"})()
    ) == "settle"


def test_sequence_planner_corrects_a_skewed_pan_pool(tmp_path):
    """A pool dominated by one panning direction must not produce a sequence
    that is all pans-in-one-direction, even when that side scores slightly
    higher — matching the QA's direction_balance metric (motion_qa), which
    measures min(left,right)/max(left,right) over the whole edit rather than
    per-cut continuity (already covered by _transition/cross_cut_continuity).
    """
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,40)"
        )
        conn.executemany(
            """
            INSERT INTO shots(
              id,asset_id,idx,start_sec,end_sec,character,motion_dir,
              shot_scale,visual_energy
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    f"s{i}", "a", i, i * 3, i * 3 + 2.5,
                    "tanjiro", "right" if i < 8 else "left",
                    0.5, 0.6,
                )
                for i in range(10)
            ],
        )
    plan = DirectorPlan(
        project_id="balance",
        revision=1,
        duration_sec=6,
        primary_characters=["tanjiro"],
        tone=["cinematic"],
        structure=[
            DirectorSection(
                role="buildup", start=0, end=6, energy=0.6,
                average_shot_length=1,
            )
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    music = MusicMap(
        duration_sec=6,
        bpm=120,
        beats=[value / 2 for value in range(12)],
        bars=[0],
        downbeats=[0],
        onsets=[],
        beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=6, energy=0.6)],
        impact_points=[],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )
    # A small, deliberate scoring edge for "right" candidates: without the
    # balance term, the beam would greedily pick right shots every time.
    candidates = [
        RankedCandidate(
            shot_id=f"s{i}",
            intrinsic=0.7,
            contextual=0.7,
            total=0.70 if i < 8 else 0.69,
            intrinsic_components={},
            contextual_components={},
        )
        for i in range(10)
    ]
    spec = plan_sequence(
        conn, plan=plan, music=music, candidates_by_role={"buildup": candidates}
    )
    directions = [
        conn.execute(
            "SELECT motion_dir FROM shots WHERE id=?", (clip.shot_id,)
        ).fetchone()[0]
        for clip in spec.clips
    ]
    assert "left" in directions, (
        "sequence never corrected the pan skew despite an under-represented "
        "direction being available"
    )


def test_sequence_planner_prefers_blur_at_impact_and_clarity_at_establish(tmp_path):
    """Drawn motion blur is a cut-point asset for high-motion intents and a
    liability for establishing ones — the opposite of scoring it as uniformly
    bad. This is the planner-side half of the blur_ratio/hold_ratio QA gap;
    the retrieval-side half is the "motion_blur" removal from pose_bad in
    studio/asset_intelligence/visual/analyzer.py.
    """
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,40)"
        )
        conn.executemany(
            """
            INSERT INTO shots(
              id,asset_id,idx,start_sec,end_sec,character,motion_dir,
              shot_scale,visual_energy,blur_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    f"sHigh{i}", "a", i, i * 3, i * 3 + 2.5,
                    "tanjiro", "right", 0.5, 0.6, 0.6,
                )
                for i in range(3)
            ] + [
                (
                    f"sLow{i}", "a", i + 3, (i + 3) * 3, (i + 3) * 3 + 2.5,
                    "tanjiro", "right", 0.5, 0.6, 0.03,
                )
                for i in range(3)
            ],
        )
    plan = DirectorPlan(
        project_id="blur",
        revision=1,
        duration_sec=3,
        primary_characters=["tanjiro"],
        tone=["cinematic"],
        structure=[
            DirectorSection(
                role="impact", start=0, end=3, energy=0.6,
                average_shot_length=1,
            )
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    music = MusicMap(
        duration_sec=3,
        bpm=120,
        beats=[0, 0.5, 1, 1.5, 2, 2.5],
        bars=[0],
        downbeats=[0],
        onsets=[],
        beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=3, energy=0.6)],
        impact_points=[],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )
    candidates = [
        RankedCandidate(
            shot_id=shot_id, intrinsic=0.7, contextual=0.7, total=0.70,
            intrinsic_components={}, contextual_components={},
        )
        for shot_id in (
            [f"sHigh{i}" for i in range(3)] + [f"sLow{i}" for i in range(3)]
        )
    ]
    spec = plan_sequence(
        conn, plan=plan, music=music, candidates_by_role={"impact": candidates}
    )
    # slot 0 is always forced to "establish" regardless of section role; any
    # later slot in an "impact"-role section gets intent="impact".
    assert spec.clips[0].shot_id.startswith("sLow"), (
        "the opening/establish slot picked a blurry shot over a clear one"
    )
    assert any(clip.shot_id.startswith("sHigh") for clip in spec.clips[1:]), (
        "no later impact-intent slot ever picked a motion-blurred shot"
    )


def test_editing_styles_create_distinct_reusable_rhythm_grammars():
    music = MusicMap(
        duration_sec=10, bpm=120,
        beats=[value / 2 for value in range(20)], bars=[0],
        downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=10, energy=.5)],
        impact_points=[2, 6], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    base = dict(
        project_id="styles", revision=1, duration_sec=10,
        primary_characters=[], tone=[],
        structure=[
            DirectorSection(
                role="buildup", start=0, end=10, energy=.5,
                average_shot_length=1,
            )
        ],
        visual_rules={"prefer": [], "avoid": []}, sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    calm = DirectorPlan(
        **base,
        editing_style=EditingStyleProfile(
            id="calm", name="Calm", source="curated",
            target_cut_density=.5, median_shot_length=2,
            min_shot_length=1, max_shot_length=3,
            duration_pattern=[1, 1], beat_sync_target=.2,
        ),
    )
    punchy = DirectorPlan(
        **base,
        editing_style=EditingStyleProfile(
            id="punchy", name="Punchy", source="curated",
            target_cut_density=2, median_shot_length=.5,
            min_shot_length=.25, max_shot_length=1,
            duration_pattern=[1, .5, 1.5, .5], beat_sync_target=.7,
        ),
    )
    calm_metrics = planned_rhythm_metrics(calm, music)
    punchy_metrics = planned_rhythm_metrics(punchy, music)
    assert punchy_metrics["shot_count"] > calm_metrics["shot_count"]
    assert punchy_metrics["median_shot_length"] < calm_metrics["median_shot_length"]
    assert punchy_metrics["beat_sync_ratio"] >= .6

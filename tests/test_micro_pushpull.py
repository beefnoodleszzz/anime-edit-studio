from anime import director, editspec


def _candidate(**over) -> dict:
    c = {
        "shot_id": "shot",
        "asset_id": "asset",
        "start_sec": 1.0,
        "clip_dur": 20.0,
        "motion_mag": 0.2,
        "reframe_x": 0.0,
        "fill_mode": "crop",
        "tags": "",
    }
    c.update(over)
    return c


def _shot(k, section="build", technique="micro-pushpull", dur=60):
    return director._make_shot(
        _candidate(), {"asset": "/tmp/source.mp4"}, k * dur, dur, k,
        section, False, 0.4, 60, technique=technique,
    )


def test_micro_pushpull_is_a_supported_technique():
    assert director._normalize_technique("micro_pushpull") == "micro-pushpull"


def test_micro_pushpull_uses_continuous_linear_push_not_pendulum():
    # 连续推镜:走 camera_from/to 线性,不用 ease-in-out 的 camera_move;无闪白/甩镜/特效
    first, second = _shot(0), _shot(1)
    for s in (first, second):
        assert s.camera_move == "none" and s.camera_amount == 0.0
        assert s.camera_from > 0 and s.camera_to > 0
        assert s.transition == "none" and s.effects == []
    # 推近为主:起 1.0 → 收更大
    assert first.camera_from == 1.0 and first.camera_to > 1.0


def test_micro_pushpull_has_intermittent_pull():
    pull = _shot(2)                                  # k%3==2 → 拉远
    assert pull.camera_from > pull.camera_to == 1.0


def test_micro_pushpull_inserts_a_true_still_shot():
    still = _shot(6)                                 # k%7==6 → 真静止
    assert still.camera_from == 0.0 and still.camera_to == 0.0
    assert still.camera_move == "none" and still.camera_amount == 0.0


def test_pushpull_hook_accelerates_into_a_closeup():
    shots = [_shot(k, section="opening") for k in range(3)]
    director._apply_pushpull_hook(shots, ["opening"] * 3, 60)
    tos = [s.camera_to for s in shots]
    assert all(s.camera_from == 1.0 for s in shots)  # 每镜从 1.0 起,同向接力
    assert tos == sorted(tos) and tos[0] < tos[-1]   # 速度递增 → 推进幅度递增
    assert shots[-1].ramp == "decel"                 # 末镜落地微减速


def test_tightness_orders_wide_to_closeup():
    assert director._tightness(_candidate(tags="wide")) < \
        director._tightness(_candidate(tags="close-up"))


def test_whip_drag_is_a_supported_technique():
    assert director._normalize_technique("whip-drag") == "whip-drag"


def test_whip_drag_whips_on_build_and_climax():
    build = _shot(1, section="build", technique="whip-drag")
    climax = _shot(1, section="climax", technique="whip-drag")
    assert build.transition in ("whipLeft", "whipRight")
    assert climax.transition in ("whipLeft", "whipRight")


def test_exit_smear_matches_the_next_entrance_whip():
    prev = _shot(0, technique="whip-drag")
    prev.transition, prev.transition_intensity = "none", 0.0   # 上一镜无入场转场
    nxt = editspec.Shot(id="n", src="/tmp/s.mp4", start_frame=60,
                        duration_in_frames=60, transition="whipLeft",
                        transition_intensity=0.6)
    director._apply_exit_smears([prev, nxt])
    assert prev.exit_transition == "whipLeft" and prev.exit_intensity > 0


def test_exit_smear_skips_too_short_previous_shot():
    prev = editspec.Shot(id="p", src="/tmp/s.mp4", start_frame=0, duration_in_frames=5)
    nxt = editspec.Shot(id="n", src="/tmp/s.mp4", start_frame=5,
                        duration_in_frames=60, transition="whipRight",
                        transition_intensity=0.6)
    director._apply_exit_smears([prev, nxt])
    assert prev.exit_transition == "none"

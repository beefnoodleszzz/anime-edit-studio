import json
from pathlib import Path

from studio.critic.technical import (
    CameraFlowMeasurement,
    compare_camera_flow,
    measure_camera_flow,
)
from studio.critic.technical.camera_flow import CutCarry, ShotFlow

REFERENCE_A = Path(__file__).resolve().parent / "fixtures" / "camera_flow_reference_a.json"


def _shot(index: int, vx: float, vy: float, magnitude: float) -> ShotFlow:
    from studio.critic.technical.camera_flow import _direction_name

    return ShotFlow(
        index=index, start_sec=index * 0.5, end_sec=index * 0.5 + 0.5,
        vx=vx, vy=vy, magnitude=magnitude,
        direction=_direction_name(vx, vy) if magnitude >= 0.45 else "static",
        directional=magnitude >= 0.45,
    )


def _measurement(shots: list[ShotFlow], source: str) -> CameraFlowMeasurement:
    """Rebuild the derived metrics the same way ``measure_camera_flow`` does."""
    from studio.critic.technical.camera_flow import (
        CARRY_ANGLE_DEG,
        REVERSAL_ANGLE_DEG,
        _angle_delta,
        _normalized_entropy,
    )
    import numpy as np

    cuts: list[CutCarry] = []
    for left, right in zip(shots, shots[1:]):
        if not (left.directional and right.directional):
            cuts.append(CutCarry(sec=right.start_sec, angle_delta_deg=None,
                                 carried=False, reversed=False))
            continue
        delta = _angle_delta(left, right)
        cuts.append(CutCarry(
            sec=right.start_sec, angle_delta_deg=delta,
            carried=delta <= CARRY_ANGLE_DEG,
            reversed=delta >= REVERSAL_ANGLE_DEG,
        ))
    directional = [shot for shot in shots if shot.directional]
    return CameraFlowMeasurement(
        source=source, duration_sec=len(shots) * 0.5 or 1.0,
        shot_count=len(shots), shots=shots, cuts=cuts,
        directional_shot_frac=len(directional) / len(shots),
        carry_rate=sum(cut.carried for cut in cuts) / len(cuts),
        reversal_rate=sum(cut.reversed_ for cut in cuts) / len(cuts),
        direction_entropy=_normalized_entropy(
            [shot.direction for shot in directional]
        ),
        median_magnitude=float(np.median([shot.magnitude for shot in shots])),
        mean_abs_vx=float(np.mean([abs(shot.vx) for shot in shots])),
    )


def _reference() -> CameraFlowMeasurement:
    return CameraFlowMeasurement.model_validate_json(REFERENCE_A.read_text())


def test_reference_baseline_is_recorded_and_loadable():
    reference = _reference()
    assert reference.shot_count > 20
    assert reference.direction_entropy > 0.7
    assert reference.reversal_rate > reference.carry_rate


def test_uniform_pan_fails_the_gate():
    """The exact defect this gate exists to catch: every shot panned the same.

    It scores a perfect carry rate and plenty of motion, so a motion-magnitude
    check alone would pass it.  Entropy and reversal must fail it.
    """
    uniform = _measurement(
        [_shot(index, 2.5, 0.0, 2.5) for index in range(20)], "uniform_pan"
    )
    assert uniform.carry_rate == 1.0
    assert uniform.direction_entropy == 0.0
    result = compare_camera_flow(uniform, _reference())
    assert not result.passed
    failed = " ".join(result.failures)
    assert "direction_entropy" in failed
    assert "reversal_rate" in failed
    assert "carry_rate" in failed


def test_frozen_slideshow_fails_the_gate():
    frozen = _measurement(
        [_shot(index, 0.0, 0.0, 0.02) for index in range(20)], "frozen"
    )
    result = compare_camera_flow(frozen, _reference())
    assert not result.passed
    assert any("directional_shot_frac" in item for item in result.failures)
    assert any("median_magnitude" in item for item in result.failures)


def test_reference_passes_against_itself():
    reference = _reference()
    result = compare_camera_flow(reference, reference)
    assert result.passed, result.failures


def test_varied_directional_cut_passes():
    """Riding varied footage directions is what the gate is meant to accept.

    Note the two adjacent same-direction pairs: the reference carries 17.5% of
    its cuts, so an edit that reverses at *every* join is its own defect and the
    gate correctly rejects it.
    """
    vectors = [
        (2.5, 0.0), (2.4, 0.4), (-2.4, 0.3), (0.2, 2.6),
        (0.3, 2.5), (-0.1, -2.5), (1.8, 1.8), (-1.9, 1.7),
        (1.7, -1.8), (-1.8, -1.7),
    ]
    shots = [
        _shot(index, vx, vy, 2.5)
        for index, (vx, vy) in enumerate(vectors * 3)
    ]
    result = compare_camera_flow(_measurement(shots, "varied"), _reference())
    assert result.passed, result.failures


def test_measure_is_deterministic_on_the_reference(tmp_path):
    """Same input, same numbers — no sampling jitter allowed in a gate."""
    source = Path("study-center/a.mp4")
    if not source.is_file():
        import pytest

        pytest.skip("参考片不在工作区")
    first = measure_camera_flow(source)
    second = measure_camera_flow(source)
    assert first.model_dump_json() == second.model_dump_json()


def test_recorded_baseline_matches_current_measurement_shape():
    payload = json.loads(REFERENCE_A.read_text())
    assert payload["version"] == CameraFlowMeasurement.model_fields[
        "version"
    ].default
    assert len(payload["cuts"]) == payload["shot_count"] - 1

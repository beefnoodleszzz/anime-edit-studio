"""Real-machine regression for BezierSpline keyframe-position precision.

Needs a running local DaVinci Resolve, skipped otherwise:
    .venv/bin/python -m pytest -m requires_resolve tests/execution/test_resolve_adapter_splines.py -v
"""
from __future__ import annotations

import pytest

from studio.execution.resolve.adapter import ResolveAdapter

pytestmark = pytest.mark.requires_resolve


@pytest.fixture
def comp():
    from studio.execution.resolve import ResolveAdapter as _Adapter, ResolveUnavailable
    from studio.execution.resolve import connection

    try:
        connection.connect(auto_launch=True)
    except ResolveUnavailable as exc:
        pytest.skip(f"Resolve 不可用: {str(exc).splitlines()[0]}")
    fusion = connection.connect(auto_launch=True).Fusion()
    return fusion.NewComp()


def test_create_scalar_spline_survives_six_decimal_frame_positions(comp):
    # Regression: Resolve's BezierSpline only keeps 4 decimal places on a
    # keyframe's *position* (not its value) — every frame computed in this
    # codebase as round(sec * fps, 6) used to fail the read-back check the
    # moment fps/sec produced a genuinely 5-6 decimal frame number (e.g.
    # 17.167347), which is common, not an edge case, once fps isn't a round
    # number relative to the cut times involved.
    values = {0.0: 0.5, 17.167347: 0.6, 23.167347: 0.7, 26.0: 0.8}
    spline = ResolveAdapter._create_scalar_spline(comp, "PrecisionRegression", values)
    actual = spline.GetKeyFrames() or {}
    assert set(actual) == {0.0, 17.1673, 23.1673, 26.0}
    for frame, expected_value in {0.0: 0.5, 17.1673: 0.6, 23.1673: 0.7, 26.0: 0.8}.items():
        assert actual[frame][1] == pytest.approx(expected_value)


def test_create_scalar_spline_survives_readback_floating_point_noise(comp):
    # Regression: even after rounding the *submitted* frame to 4 decimals,
    # Resolve's own read-back can reintroduce ULP-level floating point
    # noise (0.3001 submitted -> 0.30010000000000003 read back), which
    # fails an exact `set()` comparison even though the values are the same
    # position. Found on a real 30fps timeline where a transition's own
    # keyframe timing (not derived from `sec * fps`) produced exactly this.
    values = {0.0: 0.5, 0.300062: 0.5, 5.0: 0.5}
    spline = ResolveAdapter._create_scalar_spline(comp, "FloatNoiseRegression", values)
    actual = spline.GetKeyFrames() or {}
    assert {round(frame, 4) for frame in actual} == {0.0, 0.3001, 5.0}

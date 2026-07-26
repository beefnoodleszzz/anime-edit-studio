import cv2
import numpy as np

from studio.asset_intelligence.visual.analyzer import VisualAnalyzer
from studio.core.cache import JsonCache


def test_visual_analysis_is_structured_cached_and_bounded(tmp_path):
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[:] = (40, 80, 120)
    cv2.rectangle(image, (100, 30), (220, 220), (220, 180, 80), -1)
    cv2.putText(image, "SUBTITLE", (70, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    path = tmp_path / "frame.jpg"
    cv2.imwrite(str(path), image)
    analyzer = VisualAnalyzer(JsonCache(tmp_path / "cache"))
    kwargs = dict(
        shot_id="s",
        keyframe=path,
        tags="solo,portrait,eyes,upper_body",
        motion_mag=2.0,
        duration_sec=2.0,
        asset_hash="a" * 64,
    )
    first = analyzer.analyze(**kwargs)
    second = analyzer.analyze(**kwargs)
    assert first == second
    assert 0 <= first.analysis.shot_scale.value <= 1
    assert 0 <= first.analysis.visual_energy.value <= 1
    assert len(first.analysis.color_palette.value) == 5
    assert 0 <= first.subject_box.x <= 1
    assert first.analysis.pose_quality.method == "wd_tag_heuristic"

from pathlib import Path

import cv2
import numpy as np

from studio.core.database import connect
from studio.execution.external_ai.subject_mask import (
    SUBJECT_MASK_VERSION,
    SubjectMaskAnalyzer,
    analyze_pending_subject_layers,
    _bbox_from_alpha,
)


class BrightBoxSegmenter:
    """Deterministic stub: the bright rectangle is the foreground subject."""

    def segment(self, frame_bgr: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(np.float64) / 255.0
        return (gray > 0.5).astype(np.float64)


def _write_sweep_video(path: Path, *, fps=10, seconds=1.0):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    total = int(fps * seconds)
    for index in range(total):
        frame = np.zeros((120, 160, 3), np.uint8)
        x = int(10 + (index / total) * 120)  # subject sweeps left -> right
        cv2.rectangle(frame, (x, 40), (x + 20, 80), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def test_bbox_from_alpha_locates_subject():
    alpha = np.zeros((100, 100))
    alpha[20:60, 30:50] = 1.0
    sample = _bbox_from_alpha(alpha)
    assert sample.coverage > 0
    assert 0.29 <= sample.x <= 0.31
    assert 0.19 <= sample.y <= 0.21
    assert 0.35 <= sample.cx <= 0.45


def test_empty_alpha_reports_zero_coverage():
    sample = _bbox_from_alpha(np.zeros((50, 50)))
    assert sample.coverage == 0.0
    assert sample.x is None


def test_analyzer_measures_horizontal_sweep(tmp_path: Path):
    video = tmp_path / "sweep.mp4"
    _write_sweep_video(video)
    analyzer = SubjectMaskAnalyzer(segmenter=BrightBoxSegmenter(), sample_fps=10)
    layer = analyzer.analyze(video, start_sec=0.0, end_sec=1.0)
    assert layer.mean_coverage > 0
    # Subject travelled most of the frame width.
    assert layer.horizontal_sweep > 0.4
    assert len(layer.samples) >= 5


def test_analyze_pending_persists_and_is_idempotent(tmp_path: Path):
    video = tmp_path / "sweep.mp4"
    _write_sweep_video(video)
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec,proxy_path) "
            "VALUES ('a',?,'h',10,1,1,?)",
            (str(video), str(video)),
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec) VALUES ('s','a',0,0,1)"
        )
    analyzer = SubjectMaskAnalyzer(segmenter=BrightBoxSegmenter(), sample_fps=10)
    report = analyze_pending_subject_layers(conn, analyzer=analyzer)
    assert report["analyzed"] == 1
    assert report["version"] == SUBJECT_MASK_VERSION
    again = analyze_pending_subject_layers(conn, analyzer=analyzer)
    assert again["analyzed"] == 0

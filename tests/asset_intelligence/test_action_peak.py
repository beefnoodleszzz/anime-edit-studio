from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.motion.action_peak import (
    ACTION_PEAK_VERSION,
    ActionPeakDetector,
    analyze_pending_action_peaks,
    load_action_peaks,
)
from studio.core.database import connect


def _write_burst_video(path: Path, *, fps: int = 30, seconds: float = 2.0) -> None:
    """A subject that sits still, snaps across the frame mid-clip, then settles.

    The mid-clip burst is the action peak the detector must find.
    """
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120)
    )
    total = int(fps * seconds)
    for index in range(total):
        frame = np.zeros((120, 160, 3), np.uint8)
        t = index / total
        if t < 0.4:
            x = 20
        elif t < 0.6:
            # Fast traverse in the middle 20% of the clip.
            x = int(20 + (t - 0.4) / 0.2 * 110)
        else:
            x = 130
        cv2.rectangle(frame, (x, 40), (x + 20, 80), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def test_detector_finds_midclip_peak(tmp_path: Path):
    video = tmp_path / "burst.mp4"
    _write_burst_video(video)
    peaks = ActionPeakDetector().detect(video, start_sec=0.0, end_sec=2.0)
    assert peaks, "expected at least one action peak"
    strongest = max(peaks, key=lambda peak: peak.magnitude)
    # The burst is centred around 1.0s (the 0.4–0.6 window of a 2s clip).
    assert 0.7 <= strongest.sec <= 1.3
    assert strongest.confidence > 0


def test_static_clip_has_no_peak(tmp_path: Path):
    video = tmp_path / "static.mp4"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30, (160, 120)
    )
    for _ in range(60):
        frame = np.zeros((120, 160, 3), np.uint8)
        cv2.rectangle(frame, (60, 40), (80, 80), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()
    peaks = ActionPeakDetector().detect(video, start_sec=0.0, end_sec=2.0)
    assert peaks == []


def test_analyze_pending_persists_and_caches(tmp_path: Path):
    video = tmp_path / "burst.mp4"
    _write_burst_video(video)
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec,proxy_path) "
            "VALUES ('a',?,'hash',30,1,2,?)",
            (str(video), str(video)),
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,motion_mag) "
            "VALUES ('s','a',0,0,2,1.0)",
        )
    report = analyze_pending_action_peaks(conn, cache_root=tmp_path / "cache")
    assert report["analyzed"] == 1
    assert report["version"] == ACTION_PEAK_VERSION
    row = conn.execute(
        "SELECT action_peaks,action_peaks_version FROM shots WHERE id='s'"
    ).fetchone()
    assert row["action_peaks_version"] == ACTION_PEAK_VERSION
    peaks = load_action_peaks(row["action_peaks"])
    assert peaks
    # Second run is a no-op: version already current.
    again = analyze_pending_action_peaks(conn, cache_root=tmp_path / "cache")
    assert again["analyzed"] == 0


def test_load_action_peaks_tolerates_garbage():
    assert load_action_peaks(None) == []
    assert load_action_peaks("") == []
    assert load_action_peaks("not json") == []

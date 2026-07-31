from __future__ import annotations

from fractions import Fraction

from studio.critic.technical import qa as technical_qa_module
from studio.qa.rendered_qa import compare_style_summary, run_rendered_qa
from studio.spec.reference_blueprint import StyleSummary


def _style(**overrides) -> StyleSummary:
    base = dict(
        cut_density=1.0,
        shot_duration_distribution={"mean": 1.0, "median": 0.9},
        music_sync_distribution={"synced_ratio": 0.8},
        motion_coverage=0.6,
        hold_ratio=0.2,
        reversal_ratio=0.1,
        scale_motion_ratio=0.3,
        blur_usage=0.2,
    )
    base.update(overrides)
    return StyleSummary(**base)


def test_compare_style_summary_flags_only_out_of_tolerance_fields():
    reference = _style()
    actual = _style(cut_density=1.05, motion_coverage=0.1)

    metrics = compare_style_summary(reference, actual)

    assert metrics["cut_density"].passed
    assert not metrics["motion_coverage"].passed
    assert metrics["motion_coverage"].difference == abs(0.6 - 0.1)


def _mock_probe(monkeypatch, *, width=1080, height=1350, duration=4.0):
    monkeypatch.setattr(
        technical_qa_module,
        "probe_media_json",
        lambda *args, **kwargs: {
            "format": {"duration": str(duration)},
            "streams": [
                {
                    "codec_type": "video", "codec_name": "h264",
                    "width": width, "height": height,
                    "avg_frame_rate": "24000/1001",
                    "nb_read_frames": str(round(duration * 24000 / 1001)),
                },
                {"codec_type": "audio"},
            ],
        },
    )

    def diagnostics(path, *, video_filter=None, audio_filter=None):
        if audio_filter and "ebur128" in audio_filter:
            return 0, "I: -11.0 LUFS"
        return 0, ""

    monkeypatch.setattr(technical_qa_module, "run_media_diagnostic", diagnostics)


def test_run_rendered_qa_passes_only_when_technical_and_fusion_and_metrics_all_pass(tmp_path, monkeypatch):
    media = tmp_path / "preview.mp4"
    media.write_bytes(b"render")
    _mock_probe(monkeypatch, duration=4.0)

    report = run_rendered_qa(
        media,
        expected_duration=4.0, expected_width=1080, expected_height=1350,
        expected_fps=Fraction(24000, 1001),
        reference_style=_style(), actual_style=_style(),
        fusion_graph_consistent=True,
    )

    assert report.technical.passed
    assert report.passed
    assert all(metric.passed for metric in report.metrics.values())


def test_run_rendered_qa_fails_on_inconsistent_fusion_graph_even_if_technical_passes(tmp_path, monkeypatch):
    media = tmp_path / "preview.mp4"
    media.write_bytes(b"render")
    _mock_probe(monkeypatch, duration=4.0)

    report = run_rendered_qa(
        media,
        expected_duration=4.0, expected_width=1080, expected_height=1350,
        expected_fps=Fraction(24000, 1001),
        reference_style=_style(), actual_style=_style(),
        fusion_graph_consistent=False,
    )

    assert report.technical.passed
    assert not report.passed

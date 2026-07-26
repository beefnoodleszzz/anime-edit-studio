from fractions import Fraction

from studio.critic.technical import qa as module


def test_technical_qa_has_exactly_thirteen_independent_checks(tmp_path, monkeypatch):
    media = tmp_path / "render.mp4"
    media.write_bytes(b"render")
    monkeypatch.setattr(
        module,
        "probe_media_json",
        lambda *args, **kwargs: {
            "format": {"duration": "2.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1350,
                    "avg_frame_rate": "24000/1001",
                    "nb_read_frames": "48",
                },
                {"codec_type": "audio"},
            ],
        },
    )

    def diagnostics(path, *, video_filter=None, audio_filter=None):
        if audio_filter and "ebur128" in audio_filter:
            return 0, "I: -14.0 LUFS"
        return 0, ""

    monkeypatch.setattr(module, "run_media_diagnostic", diagnostics)
    result = module.run_technical_qa(
        media,
        expected_duration=2,
        expected_width=1080,
        expected_height=1350,
        expected_fps=Fraction(24000, 1001),
    )
    assert result.passed
    assert len(result.checks) == 13
    assert {check.name for check in result.checks} == {
        "file_exists", "duration", "resolution", "fps", "codec",
        "audio_track", "loudness", "black_frames", "freeze_frames",
        "missing_frames", "corruption", "unexpected_silence", "aspect_ratio",
    }


def test_technical_qa_failure_cannot_pass(tmp_path, monkeypatch):
    media = tmp_path / "broken.mp4"
    media.write_bytes(b"render")
    monkeypatch.setattr(
        module,
        "probe_media_json",
        lambda *args, **kwargs: {
            "format": {"duration": "1.0"},
            "streams": [
                {
                    "codec_type": "video", "codec_name": "h264",
                    "width": 1920, "height": 1080,
                    "avg_frame_rate": "24/1", "nb_read_frames": "24",
                }
            ],
        },
    )
    monkeypatch.setattr(module, "run_media_diagnostic", lambda *args, **kwargs: (0, ""))
    result = module.run_technical_qa(
        media,
        expected_duration=2,
        expected_width=1080,
        expected_height=1350,
        expected_fps=Fraction(24000, 1001),
    )
    assert not result.passed
    assert not next(check for check in result.checks if check.name == "resolution").passed


def test_qa_uses_video_duration_and_allows_declared_low_motion_freeze(
    tmp_path, monkeypatch
):
    media = tmp_path / "render.mov"
    media.write_bytes(b"render")
    monkeypatch.setattr(
        module,
        "probe_media_json",
        lambda *args, **kwargs: {
            "format": {"duration": "2.109333"},
            "streams": [
                {
                    "codec_type": "video", "codec_name": "hevc",
                    "width": 1080, "height": 1350,
                    "duration": "2.002", "avg_frame_rate": "24000/1001",
                    "nb_read_frames": "48",
                },
                {"codec_type": "audio", "duration": "2.109333"},
            ],
        },
    )

    def diagnostics(path, *, video_filter=None, audio_filter=None):
        if video_filter and "freezedetect" in video_filter:
            return 0, "freeze_start: 0.5\nfreeze_end: 1.7"
        if audio_filter and "ebur128" in audio_filter:
            return 0, "I: -14.0 LUFS"
        return 0, ""

    monkeypatch.setattr(module, "run_media_diagnostic", diagnostics)
    result = module.run_technical_qa(
        media,
        expected_duration=2,
        expected_width=1080,
        expected_height=1350,
        expected_fps=Fraction(24000, 1001),
        expected_freeze_ranges=[(0.45, 1.75)],
    )
    assert result.passed
    assert next(check for check in result.checks if check.name == "duration").measured == 2.002
    assert next(
        check for check in result.checks if check.name == "freeze_frames"
    ).measured == 0


def test_freeze_threshold_allows_one_frame_quantization(tmp_path, monkeypatch):
    media = tmp_path / "render.mov"
    media.write_bytes(b"render")
    monkeypatch.setattr(
        module,
        "probe_media_json",
        lambda *args, **kwargs: {
            "format": {"duration": "2.0"},
            "streams": [
                {
                    "codec_type": "video", "codec_name": "h264",
                    "width": 1080, "height": 1350,
                    "duration": "2.0", "avg_frame_rate": "24000/1001",
                    "nb_read_frames": "48",
                },
                {"codec_type": "audio"},
            ],
        },
    )

    def diagnostics(path, *, video_filter=None, audio_filter=None):
        if video_filter and "freezedetect" in video_filter:
            return 0, "freeze_start: 0.0\nfreeze_end: 0.75075"
        if audio_filter and "ebur128" in audio_filter:
            return 0, "I: -14.0 LUFS"
        return 0, ""

    monkeypatch.setattr(module, "run_media_diagnostic", diagnostics)
    result = module.run_technical_qa(
        media,
        expected_duration=2,
        expected_width=1080,
        expected_height=1350,
        expected_fps=Fraction(24000, 1001),
    )
    assert next(
        check for check in result.checks if check.name == "freeze_frames"
    ).passed

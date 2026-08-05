from __future__ import annotations

import cv2
import numpy as np
import soundfile as sf

from studio.core.database import connect
from studio.workflows import create_amv as module


def _panning_scene(rng, width=320, height=240, n=60, direction=1):
    base = rng.integers(0, 200, size=(height + 40, width + 80, 3), dtype=np.uint8)
    cv2.rectangle(base, (60, 60), (200, 180), (10, 200, 10), -1)
    frames = []
    for i in range(n):
        offset = int(direction * i * 1.2)
        frame = np.roll(base, offset, axis=1)[20:20 + height, 40:40 + width]
        frames.append(frame.copy())
    return frames


def _write_video(path, frames, fps=24, size=(320, 240)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for frame in frames:
        writer.write(frame)
    writer.release()


def _write_audio(path, duration=6, sample_rate=22050):
    audio = np.zeros(sample_rate * duration, np.float32)
    for second in np.arange(0, duration, 0.5):
        start = round(second * sample_rate)
        length = min(800, len(audio) - start)
        audio[start:start + length] += np.hanning(length).astype(np.float32)
    sf.write(path, audio, sample_rate)


def _seed_shots(database, asset_id: str, *, count: int = 6, shot_duration: float = 1.0) -> list[str]:
    conn = connect(database)
    shot_ids = [f"{asset_id}-s{index}" for index in range(count)]
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO assets(id,path,sha256,width,height,fps_num,fps_den,duration_sec,codec,proxy_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (asset_id, f"/tmp/{asset_id}.mp4", asset_id, 320, 240, 24, 1, count * shot_duration, "h264", None),
        )
        for index, shot_id in enumerate(shot_ids):
            conn.execute(
                "INSERT OR IGNORE INTO shots(id,asset_id,start_sec,end_sec) VALUES (?,?,?,?)",
                (shot_id, asset_id, index * shot_duration, (index + 1) * shot_duration),
            )
    conn.close()
    return shot_ids


def test_build_amv_spec_workflow_writes_the_exact_output_contract(tmp_path, monkeypatch):
    demo = tmp_path / "demo.mp4"
    _write_video(demo, _panning_scene(np.random.default_rng(3)))
    music = tmp_path / "music.wav"
    _write_audio(music)
    database = tmp_path / "engine.sqlite"

    monkeypatch.setattr(module, "resolve_shot_ids", lambda shot_ids, catalog_db=None: [])
    monkeypatch.setattr(module, "import_shot_manifest", lambda conn, manifest: [])
    shot_ids = _seed_shots(database, "mat0", count=8, shot_duration=0.5)

    result = module.build_amv_spec_workflow(
        project_id="test-amv",
        demo_path=demo,
        shot_ids=shot_ids,  # DB seeded directly; resolve/import monkeypatched away above
        music_path=music,
        database=database,
        projects_root=tmp_path / "projects",
    )

    paths = result.paths()
    for name, path in paths.items():
        assert path.is_file(), f"missing {name}"
    assert set(p.name for p in result.output_dir.iterdir()) >= {
        "project.json", "reference_blueprint.json", "music_timeline.json", "amv_spec.json",
    }
    # No r1/r2/candidate-group/preview-2 litter (REFACTOR.md §3).
    assert not any(name.startswith(("r1", "r2", "v3", "candidate")) for name in
                   (p.name for p in result.output_dir.iterdir()))

    assert result.spec.duration_sec > 0
    assert result.spec.canvas.aspect == result.blueprint.technical.aspect
    assert result.spec.id == "test-amv"


def test_build_amv_spec_workflow_caches_reference_and_music_analysis_by_hash(tmp_path, monkeypatch):
    demo = tmp_path / "demo.mp4"
    _write_video(demo, _panning_scene(np.random.default_rng(7)))
    music = tmp_path / "music.wav"
    _write_audio(music)
    database = tmp_path / "engine.sqlite"

    monkeypatch.setattr(module, "resolve_shot_ids", lambda shot_ids, catalog_db=None: [])
    monkeypatch.setattr(module, "import_shot_manifest", lambda conn, manifest: [])
    shot_ids = _seed_shots(database, "mat0", count=8, shot_duration=0.5)

    calls = {"reference": 0, "music": 0}
    real_analyze_reference = module.analyze_reference
    real_analyze_music = module.analyze_music_timeline

    def counting_reference(path):
        calls["reference"] += 1
        return real_analyze_reference(path)

    def counting_music(path, *, cache_root):
        calls["music"] += 1
        return real_analyze_music(path, cache_root=cache_root)

    monkeypatch.setattr(module, "analyze_reference", counting_reference)
    monkeypatch.setattr(module, "analyze_music_timeline", counting_music)

    module.build_amv_spec_workflow(
        project_id="cache-test", demo_path=demo, shot_ids=shot_ids, music_path=music,
        database=database, projects_root=tmp_path / "projects",
    )
    module.build_amv_spec_workflow(
        project_id="cache-test", demo_path=demo, shot_ids=shot_ids, music_path=music,
        database=database, projects_root=tmp_path / "projects",
    )

    assert calls["reference"] == 1
    assert calls["music"] == 1


def test_release_amv_refuses_without_a_passing_qa_report(tmp_path):
    output_dir = tmp_path / "projects" / "p1"
    output_dir.mkdir(parents=True)
    (output_dir / "preview.mov").write_bytes(b"fake")

    import pytest

    with pytest.raises(FileNotFoundError):
        module.release_amv(output_dir)

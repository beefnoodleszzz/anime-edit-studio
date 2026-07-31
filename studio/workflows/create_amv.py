"""create_amv: the one supported workflow (REFACTOR.md §3-4, §15).

Demo + materials (+ optional target music, optional focus) -> AMVSpec ->
Resolve preview -> QA. Split into two phases on purpose:

* :func:`build_amv_spec_workflow` never touches Resolve. It indexes
  materials, analyzes the Demo and the target track, plans the sequence and
  motion, and writes ``project.json`` / ``reference_blueprint.json`` /
  ``music_timeline.json`` / ``amv_spec.json`` — fully testable and usable
  even when Resolve is not running.
* :func:`render_amv_preview` places the resulting AMVSpec on a real Resolve
  timeline, compiles Fusion, renders ``preview.mov``, and writes ``qa.json``.
  Nothing here fabricates a pass when Resolve is unavailable — callers must
  surface ``ResolveUnavailable`` rather than claim a preview exists.

Repeated runs overwrite ``amv_spec.json`` / ``preview.mov`` / ``qa.json`` in
place (REFACTOR.md §3) — no r1/r2/v3/candidate-groups litter.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from studio.analysis.music_analyzer import analyze_music_timeline
from studio.analysis.reference_analyzer import analyze_reference
from studio.asset_intelligence.ingest.service import ingest_asset
from studio.asset_intelligence.pipeline import analyze_assets
from studio.asset_intelligence.shot_detection.detector import detect_shots
from studio.core.database import DEFAULT_V2_DB, connect
from studio.core.hashing import file_sha256
from studio.execution.ffmpeg import prebake_audio
from studio.planning.amv_spec_builder import build_amv_spec
from studio.planning.global_sequence_planner import plan_sequence
from studio.planning.rhythm_style_mapper import map_rhythm_to_slots
from studio.qa.optimizer import optimize_test_interval, pick_representative_interval
from studio.qa.rendered_qa import RenderedQAReport, run_rendered_qa
from studio.spec import SPEC_VERSION
from studio.spec.amv import AMVSpec, Canvas, Timebase
from studio.spec.music_timeline import MusicTimeline
from studio.spec.reference_blueprint import ReferenceBlueprint

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v"}


@dataclass(frozen=True)
class AMVSpecResult:
    project_id: str
    output_dir: Path
    blueprint: ReferenceBlueprint
    music: MusicTimeline
    spec: AMVSpec

    def paths(self) -> dict[str, Path]:
        return {
            "project": self.output_dir / "project.json",
            "reference_blueprint": self.output_dir / "reference_blueprint.json",
            "music_timeline": self.output_dir / "music_timeline.json",
            "amv_spec": self.output_dir / "amv_spec.json",
        }


def _iter_materials(materials_dir: Path):
    for path in sorted(materials_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            yield path


def index_materials(materials_dir: Path, *, database: Path, profile: str = "full") -> list[str]:
    """Ingest/shot-detect every material incrementally; both steps are
    idempotent (content-hash upsert / skip-if-already-detected), so re-running
    over the same directory never re-analyzes the whole library.

    ``profile="coarse"`` skips the ml-tier analyzers (embeddings/tagger,
    REFACTOR.md §18) for a fast first pass; ``"full"`` (default) runs them.
    ShotWindow generation itself always runs at selection time regardless of
    this profile — it only gates the per-asset embedding/tagger pass.
    """
    if profile not in ("coarse", "full"):
        raise ValueError(f"unknown index profile: {profile!r}")
    asset_ids = []
    for path in _iter_materials(materials_dir):
        info = ingest_asset(path, database=database)
        detect_shots(info["id"], database=database)
        asset_ids.append(info["id"])
    analyze_assets(asset_id=None, include_models=(profile == "full"))
    return asset_ids


def _materials_index_hash(asset_ids: list[str]) -> str:
    import hashlib

    return hashlib.sha256("|".join(sorted(asset_ids)).encode()).hexdigest()


def _load_or_analyze_reference(conn: sqlite3.Connection, demo_path: Path) -> ReferenceBlueprint:
    source_hash = file_sha256(demo_path)
    row = conn.execute(
        "SELECT version,blueprint_json FROM reference_blueprints WHERE source_hash=?",
        (source_hash,),
    ).fetchone()
    # A cached row from an older SPEC_VERSION may be missing fields this
    # version's Pydantic model requires (extra="forbid" also rejects fields
    # it no longer has) — re-analyze rather than fail or silently reuse a
    # stale shape (REFACTOR.md §13).
    if row is not None and row[0] == SPEC_VERSION:
        return ReferenceBlueprint.model_validate_json(row[1])
    blueprint = analyze_reference(demo_path)
    with conn:
        conn.execute(
            """
            INSERT INTO reference_blueprints(source_hash,version,blueprint_json) VALUES (?,?,?)
            ON CONFLICT(source_hash) DO UPDATE SET
              version=excluded.version, blueprint_json=excluded.blueprint_json
            """,
            (source_hash, blueprint.version, blueprint.model_dump_json()),
        )
    return blueprint


def _load_or_analyze_music(conn: sqlite3.Connection, music_path: Path, *, cache_root: Path) -> MusicTimeline:
    source_hash = file_sha256(music_path)
    row = conn.execute(
        "SELECT version,timeline_json FROM music_timelines WHERE source_hash=?", (source_hash,)
    ).fetchone()
    if row is not None and row[0] == SPEC_VERSION:
        return MusicTimeline.model_validate_json(row[1])
    timeline = analyze_music_timeline(music_path, cache_root=cache_root)
    with conn:
        conn.execute(
            """
            INSERT INTO music_timelines(source_hash,version,timeline_json) VALUES (?,?,?)
            ON CONFLICT(source_hash) DO UPDATE SET
              version=excluded.version, timeline_json=excluded.timeline_json
            """,
            (source_hash, timeline.version, timeline.model_dump_json()),
        )
    return timeline


def build_amv_spec_workflow(
    *,
    project_id: str,
    demo_path: Path,
    materials_dir: Path,
    music_path: Path | None = None,
    focus: str | None = None,
    aspect: str | None = None,
    fps: tuple[int, int] | None = None,
    projects_root: Path = Path("projects"),
    database: Path = DEFAULT_V2_DB,
    selector_profile: str = "balanced",
) -> AMVSpecResult:
    """``selector_profile`` (REFACTOR.md §18): ``"fast"`` skips the ml-tier
    per-asset analyzers (embeddings/tagger) for a quicker first pass.
    ``"balanced"`` (default) and ``"quality"`` both run the full analyzer set
    and the same ShotWindow selection pipeline today — CoTracker/SAM2/DOVER
    refinement isn't wired into the selector yet (backends/*.py degrade to
    their fallbacks regardless of profile), so those two profiles are not
    currently distinguishable; the option exists so callers don't need to
    change once that wiring lands."""
    if selector_profile not in ("fast", "balanced", "quality"):
        raise ValueError(f"unknown selector profile: {selector_profile!r}")
    output_dir = projects_root / project_id
    output_dir.mkdir(parents=True, exist_ok=True)

    asset_ids = index_materials(
        materials_dir, database=database,
        profile="coarse" if selector_profile == "fast" else "full",
    )
    conn = connect(database)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO amv_projects(id,demo_path,materials_dir,music_path,focus,output_dir)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  demo_path=excluded.demo_path, materials_dir=excluded.materials_dir,
                  music_path=excluded.music_path, focus=excluded.focus,
                  output_dir=excluded.output_dir, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                """,
                (project_id, str(demo_path), str(materials_dir), str(music_path) if music_path else None,
                 focus, str(output_dir)),
            )

        blueprint = _load_or_analyze_reference(conn, demo_path)

        # No independent target track: use the Demo's own audio (§7.1 Exact Replica).
        resolved_music_path = music_path
        if resolved_music_path is None:
            resolved_music_path = prebake_audio(
                demo_path, output_dir / "demo_audio.wav",
                duration_sec=blueprint.technical.duration_sec,
            )
        music = _load_or_analyze_music(conn, resolved_music_path, cache_root=output_dir / "cache")

        slots = map_rhythm_to_slots(blueprint, music)
        choices = plan_sequence(conn, slots, project_id=project_id, asset_ids=asset_ids, character=focus)

        resolved_aspect = aspect or blueprint.technical.aspect
        width, height = blueprint.technical.width, blueprint.technical.height
        if resolved_aspect != blueprint.technical.aspect:
            aw, ah = (int(part) for part in resolved_aspect.split(":"))
            height = round(width * ah / aw)
        canvas = Canvas(width=width, height=height, aspect=resolved_aspect)
        timebase = Timebase(num=fps[0], den=fps[1]) if fps else Timebase(
            num=blueprint.technical.fps_num, den=blueprint.technical.fps_den,
        )

        spec = build_amv_spec(
            conn,
            project_id=project_id, slots=slots, choices=choices,
            canvas=canvas, timebase=timebase, music=music,
            music_path=resolved_music_path,
            demo_hash=blueprint.source_hash,
            materials_index_hash=_materials_index_hash(asset_ids),
            output_path=output_dir / "preview.mov",
        )
    finally:
        conn.close()

    result = AMVSpecResult(
        project_id=project_id, output_dir=output_dir, blueprint=blueprint, music=music, spec=spec,
    )
    paths = result.paths()
    paths["project"].write_text(
        json.dumps(
            {"id": project_id, "demo": str(demo_path), "materials": str(materials_dir),
             "music": str(resolved_music_path), "focus": focus},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    paths["reference_blueprint"].write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")
    paths["music_timeline"].write_text(music.model_dump_json(indent=2), encoding="utf-8")
    paths["amv_spec"].write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return result


def render_amv_preview(
    result: AMVSpecResult,
    *,
    adapter,  # ResolveAdapter — imported lazily by the caller so this module
              # stays importable without pyremoteobject installed.
    database: Path = DEFAULT_V2_DB,
) -> RenderedQAReport:
    """Place the AMVSpec on Resolve, optimize the representative window, render
    the full preview, and write qa.json. Raises if Resolve rejects any step —
    a caller must not report success on a partial pipeline (REFACTOR.md §0.4)."""
    from fractions import Fraction

    from studio.execution.resolve.amv_render import build_amv_timeline, render_amv

    conn = connect(database)
    try:
        interval = pick_representative_interval(result.spec, result.music)
        if interval is not None:
            def _render_window(spec: AMVSpec, params: dict, window: tuple[float, float]):
                items = build_amv_timeline(
                    adapter, spec, conn, project_name=f"{result.project_id}-probe", reset=True,
                )
                render_result, consistent = render_amv(
                    adapter, spec, items,
                    output_dir=result.output_dir / "probe",
                    name=f"{result.project_id}-probe",
                    mark_in_sec=window[0], mark_out_sec=window[1],
                )
                return render_result.output, consistent

            def _qa_window(rendered) -> RenderedQAReport:
                path, consistent = rendered
                actual_blueprint = analyze_reference(path)
                return run_rendered_qa(
                    path,
                    expected_duration=interval[1] - interval[0],
                    expected_width=result.spec.canvas.width,
                    expected_height=result.spec.canvas.height,
                    expected_fps=Fraction(result.spec.timebase.num, result.spec.timebase.den),
                    reference_style=result.blueprint.style_summary,
                    actual_style=actual_blueprint.style_summary,
                    fusion_graph_consistent=consistent,
                )

            optimize_test_interval(result.spec, interval, render_fn=_render_window, qa_fn=_qa_window)

        items = build_amv_timeline(adapter, result.spec, conn, project_name=result.project_id, reset=True)
        render_result, consistent = render_amv(
            adapter, result.spec, items,
            output_dir=result.output_dir, name="preview",
        )
        preview_path = result.output_dir / "preview.mov"
        if render_result.output != preview_path:
            render_result.output.replace(preview_path)

        actual_blueprint = analyze_reference(preview_path)
        report = run_rendered_qa(
            preview_path,
            expected_duration=result.spec.duration_sec,
            expected_width=result.spec.canvas.width,
            expected_height=result.spec.canvas.height,
            expected_fps=Fraction(result.spec.timebase.num, result.spec.timebase.den),
            reference_style=result.blueprint.style_summary,
            actual_style=actual_blueprint.style_summary,
            fusion_graph_consistent=consistent,
        )
        (result.output_dir / "qa.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8",
        )
        return report
    finally:
        conn.close()


def release_amv(output_dir: Path) -> Path:
    """Copy an accepted preview to release.mov — the only way it may exist
    (REFACTOR.md §3: 'release.mov 只有用户显式执行 release 后才允许存在')."""
    preview = output_dir / "preview.mov"
    qa_path = output_dir / "qa.json"
    if not preview.is_file():
        raise FileNotFoundError(f"no preview to release: {preview}")
    if not qa_path.is_file():
        raise FileNotFoundError(f"no qa.json to release against: {qa_path}")
    report = RenderedQAReport.model_validate_json(qa_path.read_text(encoding="utf-8"))
    if not report.passed:
        raise ValueError("QA hard gates did not pass; refusing to release")
    release_path = output_dir / "release.mov"
    import shutil

    shutil.copyfile(preview, release_path)
    return release_path


__all__ = [
    "AMVSpecResult",
    "build_amv_spec_workflow",
    "release_amv",
    "render_amv_preview",
]

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, db, decision_loop


class ReviewPayload(BaseModel):
    decision: str
    reasons: list[str] = Field(default_factory=list)
    rating: int | None = None
    trim_start_sec: float | None = None
    trim_end_sec: float | None = None
    preferred_role: str | None = None


class TrimPayload(BaseModel):
    trim_start_sec: float | None = None
    trim_end_sec: float | None = None


class BriefPayload(BaseModel):
    character_query: str | None = None
    theme: str | None = None
    target_emotions: list[str] = Field(default_factory=list)
    duration_sec: float | None = None
    aspect_ratio: str | None = None
    target_platform: str | None = None
    structure_json: dict = Field(default_factory=dict)
    reference_video_path: str | None = None


class VariantSelectPayload(BaseModel):
    selected: bool = True


class RightsPayload(BaseModel):
    source_type: str | None = None
    source_url: str | None = None
    creator: str | None = None
    title: str | None = None
    license: str | None = None
    license_url: str | None = None
    commercial_allowed: bool | None = None
    modification_allowed: bool | None = None
    attribution_required: bool | None = None
    attribution_text: str | None = None
    permission_proof_path: str | None = None
    acquired_at: str | None = None
    license_checked_at: str | None = None
    expires_at: str | None = None
    status: str = "review"
    notes: str | None = None


def _safe_media_path(path_value: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    allowed = [config.LIBRARY.resolve(), config.PROJECTS.resolve()]
    if not any(root == path or root in path.parents for root in allowed):
        raise HTTPException(status_code=403, detail="media path outside library/projects whitelist")
    if ".." in Path(path_value).parts:
        raise HTTPException(status_code=400, detail="path traversal is not allowed")
    if not path.exists():
        raise HTTPException(status_code=404, detail="media path not found")
    return path


def create_app() -> FastAPI:
    app = FastAPI(title="anime-edit-studio review api")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/config")
    def get_config():
        return {"default_project_id": getattr(app.state, "default_project_id", "demo")}

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return decision_loop.get_project(project_id)

    @app.get("/api/projects/{project_id}/shots")
    def get_project_shots(project_id: str):
        return {"items": decision_loop.list_project_shots(project_id)}

    @app.get("/api/projects/{project_id}/reviews")
    def get_reviews(project_id: str):
        return {"items": decision_loop.list_reviews(project_id)}

    @app.put("/api/projects/{project_id}/shots/{shot_id}/review")
    def put_review(project_id: str, shot_id: str, payload: ReviewPayload):
        return decision_loop.put_review(project_id, shot_id, payload.model_dump())

    @app.patch("/api/projects/{project_id}/shots/{shot_id}/trim")
    def patch_trim(project_id: str, shot_id: str, payload: TrimPayload):
        return decision_loop.patch_trim(project_id, shot_id, payload.trim_start_sec, payload.trim_end_sec)

    @app.get("/api/projects/{project_id}/brief")
    def get_brief(project_id: str):
        return decision_loop.get_brief(project_id) or {}

    @app.put("/api/projects/{project_id}/brief")
    def put_brief(project_id: str, payload: BriefPayload):
        return decision_loop.upsert_brief(project_id, payload.model_dump())

    @app.post("/api/projects/{project_id}/gap-analysis")
    def post_gap(project_id: str):
        return decision_loop.gap_analysis(project_id)

    @app.post("/api/projects/{project_id}/blueprints")
    def post_blueprints(project_id: str):
        return decision_loop.generate_blueprints(project_id)

    @app.get("/api/projects/{project_id}/variants")
    def get_variants(project_id: str):
        return {"items": decision_loop.list_variants(project_id)}

    @app.post("/api/projects/{project_id}/variants/{variant_id}/select")
    def post_select_variant(project_id: str, variant_id: int, payload: VariantSelectPayload):
        if not payload.selected:
            raise HTTPException(status_code=400, detail="selected must be true")
        return decision_loop.select_variant(project_id, variant_id)

    @app.get("/api/sources")
    def get_sources():
        return {"items": decision_loop.list_sources()}

    @app.put("/api/assets/{asset_id}/rights")
    def put_rights(asset_id: str, payload: RightsPayload):
        return decision_loop.upsert_source_record(asset_id, payload.model_dump())

    @app.get("/api/assets/{asset_id}/preview")
    def get_asset_preview(asset_id: str):
        conn = db.connect()
        row = db.asset_by_id(conn, asset_id)
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="asset not found")
        path = _safe_media_path(row["proxy_path"] or row["path"])
        return FileResponse(path)

    @app.get("/api/shots/{shot_id}/keyframe")
    def get_shot_keyframe(shot_id: str):
        conn = db.connect()
        row = conn.execute("SELECT keyframe FROM shots WHERE id=?", (shot_id,)).fetchone()
        conn.close()
        if not row or not row["keyframe"]:
            raise HTTPException(status_code=404, detail="keyframe not found")
        path = _safe_media_path(row["keyframe"])
        return FileResponse(path)

    return app

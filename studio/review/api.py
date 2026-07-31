"""FastAPI routes for high-value A/B/C decisions only."""

import json
import shutil
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from studio.core.database import DEFAULT_V2_DB, connect
from studio.core.state import WorkflowState, current_state, fail, transition
from studio.creative.director.plan import HOUSE_DURATION_SEC
from studio.editing.candidates import choose_candidate, precision_metrics

REPO = Path(__file__).resolve().parents[2]


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    context: dict = Field(default_factory=dict)
    project_style: str | None = None


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(..., min_length=1, max_length=2000)


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=120)
    intent: str = Field(..., min_length=1, max_length=4000)
    # House format default: 16–20s with a 3–5s hook.  Longer stays allowed —
    # some pieces need it — but it has to be an explicit choice, not the default.
    duration_sec: float = Field(HOUSE_DURATION_SEC, ge=5, le=180)
    platform: str = Field("douyin", max_length=40)
    primary_characters: list[str] = Field(default_factory=list, max_length=8)
    tone: list[str] = Field(default_factory=list, max_length=12)


class RecipeDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(..., min_length=1, max_length=120)
    decision: str = Field(..., pattern="^(accepted|rejected)$")
    notes: str = Field("", max_length=2000)
    reviewed_at: str = Field(..., min_length=1, max_length=80)


def create_review_app(
    *,
    database: Path = DEFAULT_V2_DB,
    projects_root: Path = REPO / "projects",
):
    try:
        from fastapi import FastAPI, File, HTTPException, UploadFile
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise RuntimeError('review UI 依赖未安装: uv pip install -e ".[review]"') from exc

    app = FastAPI(title="Anime Edit Studio Review", version="2")
    projects_root = projects_root.expanduser().resolve()

    def transition_sequence(project_id: str, *states: WorkflowState) -> None:
        conn = connect(database)
        try:
            for state in states:
                transition(conn, project_id, state)
        finally:
            conn.close()

    def record_failure(project_id: str, error: Exception, *, step: str) -> None:
        """Persist a recoverable structured failure without hiding the API error."""
        conn = connect(database)
        try:
            if current_state(conn, project_id) is not None:
                fail(
                    conn,
                    project_id,
                    error=str(error),
                    payload={"step": step, "error_type": type(error).__name__},
                )
        finally:
            conn.close()

    @app.post("/projects")
    def create_project(request: ProjectCreateRequest):
        from studio.core.hashing import stable_hash

        project_id = "project-" + stable_hash(
            {
                "title": request.title,
                "intent": request.intent,
                "created_ns": time.time_ns(),
            }
        )[:12]
        root = projects_root / project_id
        root.mkdir(parents=True, exist_ok=False)
        payload = request.model_dump(mode="json")
        payload["project_id"] = project_id
        (root / "project.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        conn = connect(database)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO creative_briefs(
                      project_id,character_query,theme,target_emotions,duration_sec,
                      aspect_ratio,target_platform,creative_contract_json,
                      created_at,updated_at
                    ) VALUES (?,?,?,?,?,'1:1',?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                      strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                    """,
                    (
                        project_id,
                        " ".join(request.primary_characters),
                        request.intent,
                        json.dumps(request.tone, ensure_ascii=False),
                        request.duration_sec,
                        request.platform,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
        finally:
            conn.close()
        conn = connect(database)
        try:
            transition(conn, project_id, WorkflowState.CREATED, payload={"title": request.title})
        finally:
            conn.close()
        return {"project_id": project_id, **payload}

    @app.post("/projects/{project_id}/uploads/{kind}")
    def upload_project_file(
        project_id: str,
        kind: str,
        file: UploadFile = File(...),
    ):
        if kind not in {"music", "reference", "source"}:
            raise HTTPException(status_code=400, detail="上传类型不支持")
        root = (projects_root / project_id).resolve()
        if projects_root not in root.parents or not (root / "project.json").is_file():
            raise HTTPException(status_code=404, detail="项目不存在")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".mp3", ".wav", ".m4a", ".mp4", ".mov", ".mkv"}:
            raise HTTPException(status_code=400, detail="文件格式不支持")
        if kind == "source":
            from studio.core.hashing import stable_hash

            token = stable_hash(
                {"filename": file.filename, "received_ns": time.time_ns()}
            )[:12]
            target = root / "uploads" / f"source-{token}{suffix}"
        else:
            target = root / "uploads" / f"{kind}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.upload")
        with temporary.open("wb") as stream:
            shutil.copyfileobj(file.file, stream)
        temporary.replace(target)
        response: dict = {
            "project_id": project_id,
            "kind": kind,
            "filename": file.filename,
            "path": str(target),
            "size": target.stat().st_size,
        }
        if kind == "source":
            from studio.asset_intelligence.ingest import ingest_asset
            from studio.asset_intelligence.pipeline import analyze_assets
            from studio.asset_intelligence.shot_detection import detect_shots

            ingested = ingest_asset(target, database=database)
            detect_shots(ingested["id"], database=database)
            response["asset"] = ingested
            response["analysis"] = analyze_assets(
                asset_id=ingested["id"], database=database
            )
        return response

    def project_config(project_id: str) -> tuple[Path, dict]:
        root = (projects_root / project_id).resolve()
        config = root / "project.json"
        if projects_root not in root.parents or not config.is_file():
            raise HTTPException(status_code=404, detail="项目不存在")
        return root, json.loads(config.read_text(encoding="utf-8"))

    @app.post("/projects/{project_id}/prepare")
    def prepare_project(project_id: str):
        """Analyze music/reference and produce real A/B/C groups."""
        from studio.workflows import create_first_cut

        root, config = project_config(project_id)
        music = next((root / "uploads").glob("music.*"), None)
        reference = next((root / "uploads").glob("reference.*"), None)
        if music is None:
            raise HTTPException(status_code=400, detail="请先上传音乐")
        try:
            result = create_first_cut(
                project_id=project_id,
                music_path=music,
                duration_sec=float(config["duration_sec"]),
                reference_path=reference,
                primary_characters=config.get("primary_characters") or [],
                tone=config.get("tone") or [],
                database=database,
                output_dir=root,
            )
            transition_sequence(
                project_id,
                WorkflowState.INGESTING,
                WorkflowState.ANALYZED,
                WorkflowState.DIRECTING,
                WorkflowState.CANDIDATES_READY,
                WorkflowState.USER_SELECTION,
            )
            return {
                **result.model_dump(mode="json"),
                "next": f"/?project={project_id}&page=candidates",
            }
        except Exception as exc:
            record_failure(project_id, exc, step="prepare")
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/first-cut")
    def finalize_first_cut(project_id: str):
        """Re-plan after A/B/C choices, then build/render the real preview."""
        from studio.core.assets import DatabaseResolver, DatabaseShotResolver
        from studio.editspec.schema import EditSpec
        from studio.execution.compiler import ResolveCompiler
        from studio.execution.render import render_spec
        from studio.execution.resolve import ResolveAdapter
        from studio.workflows import create_first_cut

        root, config = project_config(project_id)
        music = next((root / "uploads").glob("music.*"), None)
        reference = next((root / "uploads").glob("reference.*"), None)
        if music is None:
            raise HTTPException(status_code=400, detail="请先上传音乐")
        conn = connect(database)
        try:
            groups, selected = conn.execute(
                """
                SELECT count(*),count(selected_shot_id)
                FROM candidate_groups WHERE project_id=? AND active=1
                """,
                (project_id,),
            ).fetchone()
            if groups == 0 or selected != groups:
                raise HTTPException(
                    status_code=409,
                    detail="请先完成所有当前 A/B/C 候选组的选择或 AI 决定",
                )
            result = create_first_cut(
                project_id=project_id,
                music_path=music,
                duration_sec=float(config["duration_sec"]),
                reference_path=reference,
                primary_characters=config.get("primary_characters") or [],
                tone=config.get("tone") or [],
                database=database,
                output_dir=root,
                reuse_candidate_groups=True,
            )
            state = current_state(conn, project_id)
            base_state = (
                state.state.removeprefix("FAILED_") if state is not None else ""
            )
            if base_state == WorkflowState.USER_SELECTION.value:
                transition(conn, project_id, WorkflowState.EDIT_PLANNING)
            elif base_state in {
                WorkflowState.USER_REVIEW.value,
                WorkflowState.REVISION.value,
            }:
                # Re-selecting shots after preview review is a revision, not a
                # second trip through the initial planning state.
                transition(conn, project_id, WorkflowState.REVISION)
            else:
                raise ValueError(
                    f"当前状态不允许生成首剪/重选镜头: {state.state if state else 'NONE'}"
                )
            spec_path = Path(result.spec_path)
            spec = EditSpec.model_validate_json(
                spec_path.read_text(encoding="utf-8")
            )
            adapter = ResolveAdapter.open()
            build = ResolveCompiler(
                adapter,
                DatabaseResolver(database, prefer_proxy=False),
                resolve_shot=DatabaseShotResolver(database),
                state_dir=root,
            ).build(spec, reset_project=True)
            transition(conn, project_id, WorkflowState.RESOLVE_BUILD)
            render_id, rendered = render_spec(
                adapter,
                conn,
                spec,
                kind="preview",
                output_dir=root / "renders",
            )
            transition(conn, project_id, WorkflowState.PREVIEW_RENDER)
            transition(conn, project_id, WorkflowState.USER_REVIEW)
            return {
                **result.model_dump(mode="json"),
                "build": build.to_dict(),
                "render_id": render_id,
                "preview_url": f"/projects/{project_id}/preview",
                "render_status": rendered.status,
            }
        except HTTPException:
            raise
        except Exception as exc:
            record_failure(project_id, exc, step="first_cut")
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.get("/projects/{project_id}/candidate-groups")
    def list_groups(project_id: str):
        conn = connect(database)
        try:
            rows = conn.execute(
                """
                SELECT id,project_id,role,slot_key,timeline_in_sec,
                       timeline_duration_sec,shot_ids_json,selected_shot_id,
                       selection_source,plan_revision,active,created_at
                FROM candidate_groups
                WHERE project_id=? AND active=1
                ORDER BY coalesce(timeline_in_sec,999999),role
                """,
                (project_id,),
            ).fetchall()
            groups = []
            for row in rows:
                shot_ids = json.loads(row["shot_ids_json"])
                manifest_path = (
                    projects_root / project_id / "previews" / row["id"] / "manifest.json"
                )
                manifest = (
                    json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest_path.is_file() else {}
                )
                previews = manifest.get("previews", {})
                groups.append(
                    {
                        **dict(row),
                        "shot_ids": shot_ids,
                        "candidates": [
                            {
                                "id": shot_id,
                                "label": label,
                                "preview": (
                                    f"/review-assets/{row['id']}/{Path(previews[label]).name}"
                                    if label in previews else None
                                ),
                            }
                            for label, shot_id in zip(("A", "B", "C"), shot_ids, strict=True)
                        ],
                    }
                )
            return {
                "project_id": project_id,
                "groups": groups,
            }
        finally:
            conn.close()

    @app.post("/candidate-groups/{group_id}/selection")
    def select(group_id: str, request: SelectionRequest):
        conn = connect(database)
        try:
            try:
                group = choose_candidate(
                    conn,
                    group_id=group_id,
                    shot_id=request.shot_id,
                    context=request.context,
                    project_style=request.project_style,
                    selection_source="human",
                )
                from studio.creative.preference import train_pairwise

                train_pairwise(
                    conn,
                    scope=f"project:{group.project_id}",
                    project_id=group.project_id,
                )
                train_pairwise(conn, scope="global")
                return group.model_dump(mode="json")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.post("/candidate-groups/{group_id}/ai-selection")
    def ai_select(group_id: str):
        """Choose the highest persisted contextual score, never a fixed UI slot."""
        conn = connect(database)
        try:
            row = conn.execute(
                """
                SELECT project_id,role,shot_ids_json FROM candidate_groups
                WHERE id=? AND active=1
                """,
                (group_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="候选组不存在")
            shot_ids = json.loads(row["shot_ids_json"])
            occupied = {
                item["selected_shot_id"]
                for item in conn.execute(
                    """
                    SELECT selected_shot_id FROM candidate_groups
                    WHERE project_id=? AND active=1 AND id<>?
                      AND selected_shot_id IS NOT NULL
                    """,
                    (row["project_id"], group_id),
                )
            }
            available = [shot_id for shot_id in shot_ids if shot_id not in occupied]
            if not available:
                raise HTTPException(
                    status_code=409,
                    detail="该组候选均已被其他 narrative role 使用，请重新生成候选",
                )
            placeholders = ",".join("?" for _ in available)
            ranked = conn.execute(
                f"""
                SELECT shot_id,total FROM candidate_scores
                WHERE project_id=? AND role=? AND shot_id IN ({placeholders})
                ORDER BY total DESC,shot_id LIMIT 1
                """,
                (row["project_id"], row["role"], *available),
            ).fetchone()
            shot_id = ranked["shot_id"] if ranked else available[0]
            group = choose_candidate(
                conn,
                group_id=group_id,
                shot_id=shot_id,
                context={"role": row["role"], "source": "ai_decide"},
                project_style="current",
                selection_source="ai",
            )
            from studio.creative.preference import train_pairwise

            train_pairwise(
                conn,
                scope=f"project:{group.project_id}",
                project_id=group.project_id,
            )
            train_pairwise(conn, scope="global")
            return group.model_dump(mode="json")
        finally:
            conn.close()

    @app.get("/projects/{project_id}/preference-profile")
    def project_preference_profile(project_id: str):
        from studio.creative.preference import preference_profile

        conn = connect(database)
        try:
            return preference_profile(
                conn,
                scope=f"project:{project_id}",
                project_id=project_id,
            ).model_dump(mode="json")
        finally:
            conn.close()

    @app.get("/projects/{project_id}/candidate-metrics")
    def metrics(project_id: str):
        conn = connect(database)
        try:
            return precision_metrics(conn, project_id)
        finally:
            conn.close()

    @app.get("/projects/{project_id}/kpis")
    def kpis(project_id: str):
        from studio.core.kpi import project_kpis

        conn = connect(database)
        try:
            return project_kpis(conn, project_id)
        finally:
            conn.close()

    @app.get("/projects/{project_id}/state")
    def project_state(project_id: str):
        conn = connect(database)
        try:
            state = current_state(conn, project_id)
            return (
                {
                    "project_id": project_id,
                    "state": state.state,
                    "attempt": state.attempt,
                    "payload": state.payload,
                }
                if state else {"project_id": project_id, "state": None}
            )
        finally:
            conn.close()

    @app.get("/projects/{project_id}/summary")
    def project_summary(project_id: str):
        root, config = project_config(project_id)
        spec_path = root / "editspec.json"
        music_path = root / "music_map.json"
        style_path = root / "style_fingerprint.json"
        spec = (
            json.loads(spec_path.read_text(encoding="utf-8"))
            if spec_path.is_file() else None
        )
        return {
            "project_id": project_id,
            "title": config["title"],
            "ready": spec is not None,
            "duration_sec": spec.get("duration_sec") if spec else None,
            "clip_count": len(spec.get("clips", [])) if spec else 0,
            "roles": [
                clip.get("role") for clip in spec.get("clips", [])
            ] if spec else [],
            "music": (
                json.loads(music_path.read_text(encoding="utf-8"))
                if music_path.is_file() else None
            ),
            "style": (
                json.loads(style_path.read_text(encoding="utf-8"))
                if style_path.is_file() else None
            ),
        }

    @app.get("/projects/{project_id}/preview")
    def project_preview(project_id: str):
        conn = connect(database)
        try:
            row = conn.execute(
                """
                SELECT output_path FROM renders
                WHERE project_id=? AND status='complete' AND preset LIKE '%H.264%'
                ORDER BY spec_version DESC,finished_at DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or not row["output_path"]:
            raise HTTPException(status_code=404, detail="预览尚未生成")
        target = Path(row["output_path"]).resolve()
        if not target.is_file():
            raise HTTPException(status_code=404, detail="预览文件不存在")
        return FileResponse(target, media_type="video/quicktime")

    @app.post("/projects/{project_id}/refresh-recipes")
    def refresh_recipes(project_id: str):
        """Apply newly owner-accepted Recipes as an auditable spec revision."""
        from studio.core.assets import DatabaseResolver, DatabaseShotResolver
        from studio.editspec.schema import EditSpec
        from studio.execution.compiler import ResolveCompiler
        from studio.execution.render import render_spec
        from studio.execution.resolve import ResolveAdapter
        from studio.workflows import refresh_recipe_plan

        project_root = (projects_root / project_id).resolve()
        spec_path = project_root / "editspec.json"
        plan_path = project_root / "director_plan.yaml"
        if (
            projects_root not in project_root.parents
            or not spec_path.is_file()
            or not plan_path.is_file()
        ):
            raise HTTPException(status_code=404, detail="项目制品不完整")
        conn = connect(database)
        try:
            result = refresh_recipe_plan(
                conn,
                spec_path=spec_path,
                plan_path=plan_path,
                database_path=database,
            )
            spec = EditSpec.model_validate_json(
                spec_path.read_text(encoding="utf-8")
            )
            adapter = ResolveAdapter.open()
            build = ResolveCompiler(
                adapter,
                DatabaseResolver(database, prefer_proxy=False),
                resolve_shot=DatabaseShotResolver(database),
                state_dir=project_root,
            ).update(spec)
            if build.clips_changed == 0 and build.clips_removed == 0:
                return {
                    "project_id": project_id,
                    "from_version": result.from_version,
                    "to_version": result.to_version,
                    "operations": result.operations,
                    "status": "unchanged",
                }
            transition(conn, project_id, WorkflowState.REVISION)
            transition(conn, project_id, WorkflowState.RESOLVE_BUILD)
            render_id, rendered = render_spec(
                adapter,
                conn,
                spec,
                kind="preview",
                output_dir=project_root / "renders",
            )
            transition(conn, project_id, WorkflowState.PREVIEW_RENDER)
            transition(conn, project_id, WorkflowState.USER_REVIEW)
            return {
                "project_id": project_id,
                "from_version": result.from_version,
                "to_version": result.to_version,
                "operations": result.operations,
                "changed_clip_ids": result.changed_clip_ids,
                "build": build.to_dict(),
                "render_id": render_id,
                "preview_url": f"/projects/{project_id}/preview",
                "render_status": rendered.status,
            }
        except Exception as exc:
            record_failure(project_id, exc, step="recipe_refresh")
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.post("/projects/{project_id}/revision")
    def revise(project_id: str, request: RevisionRequest):
        """Natural-language feedback → two-phase persisted revision → V2 preview."""
        from studio.agents import ClaudeCLIProvider
        from studio.core.assets import DatabaseResolver, DatabaseShotResolver
        from studio.editspec.schema import EditSpec
        from studio.execution.compiler import ResolveCompiler
        from studio.execution.render import render_spec
        from studio.execution.resolve import ResolveAdapter
        from studio.workflows import revise_from_feedback

        project_root = (projects_root / project_id).resolve()
        spec_path = project_root / "editspec.json"
        if projects_root not in project_root.parents or not spec_path.is_file():
            raise HTTPException(status_code=404, detail="项目 EditSpec 不存在")
        conn = connect(database)
        try:
            result = revise_from_feedback(
                conn,
                provider=ClaudeCLIProvider(),
                feedback=request.feedback,
                spec_path=spec_path,
                database_path=database,
            )
            transition(conn, project_id, WorkflowState.REVISION)
            spec = EditSpec.model_validate_json(
                spec_path.read_text(encoding="utf-8")
            )
            adapter = ResolveAdapter.open()
            compiler = ResolveCompiler(
                adapter,
                DatabaseResolver(database, prefer_proxy=False),
                resolve_shot=DatabaseShotResolver(database),
                state_dir=project_root,
            )
            build = compiler.update(spec)
            transition(conn, project_id, WorkflowState.RESOLVE_BUILD)
            render_id, rendered = render_spec(
                adapter,
                conn,
                spec,
                kind="preview",
                output_dir=project_root / "renders",
            )
            transition(conn, project_id, WorkflowState.PREVIEW_RENDER)
            transition(conn, project_id, WorkflowState.USER_REVIEW)
            return {
                "project_id": project_id,
                "from_version": result.from_version,
                "to_version": result.to_version,
                "operations": result.operations,
                "changed_clip_ids": result.changed_clip_ids,
                "changed_ranges": build.to_dict()["changed_ranges"],
                "changed_duration_sec": build.changed_duration_sec,
                "render_id": render_id,
                "preview_url": f"/projects/{project_id}/preview",
                "render_status": rendered.status,
            }
        except Exception as exc:
            record_failure(project_id, exc, step="revision")
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.get("/projects/{project_id}/revision-status")
    def revision_status(project_id: str):
        conn = connect(database)
        try:
            row = conn.execute(
                """
                SELECT to_version,diff_json,status FROM revision_runs
                WHERE project_id=? ORDER BY id DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                return {"status": "none"}
            patch = json.loads(row["diff_json"]) if row["diff_json"] else {"ops": []}
            clip_ids = sorted(
                {
                    op["clip_id"]
                    for op in patch.get("ops", [])
                    if op.get("clip_id")
                }
            )
            ranges = []
            if row["to_version"]:
                spec_row = conn.execute(
                    """
                    SELECT spec_json FROM edit_specs
                    WHERE project_id=? AND version=?
                    """,
                    (project_id, row["to_version"]),
                ).fetchone()
                if spec_row:
                    spec = json.loads(spec_row["spec_json"])
                    by_id = {clip["id"]: clip for clip in spec["clips"]}
                    ranges = [
                        [
                            by_id[clip_id]["timeline"]["in_sec"],
                            by_id[clip_id]["timeline"]["in_sec"]
                            + by_id[clip_id]["timeline"]["duration_sec"],
                        ]
                        for clip_id in clip_ids
                        if clip_id in by_id
                    ]
            return {
                "status": row["status"],
                "to_version": row["to_version"],
                "operations": len(patch.get("ops", [])),
                "changed_clip_ids": clip_ids,
                "changed_ranges": ranges,
                "changed_duration_sec": sum(end - start for start, end in ranges),
                "preview_url": f"/projects/{project_id}/preview",
            }
        finally:
            conn.close()

    @app.post("/projects/{project_id}/lock")
    def lock_and_render(project_id: str):
        """User visual confirmation triggers the only H.265 Master path."""
        from fractions import Fraction

        from studio.core.assets import DatabaseResolver, DatabaseShotResolver
        from studio.critic.technical import run_technical_qa
        from studio.editspec.schema import EditSpec
        from studio.execution.compiler import ResolveCompiler
        from studio.execution.render import render_spec
        from studio.execution.resolve import ResolveAdapter

        project_root = (projects_root / project_id).resolve()
        spec_path = project_root / "editspec.json"
        if projects_root not in project_root.parents or not spec_path.is_file():
            raise HTTPException(status_code=404, detail="项目 EditSpec 不存在")
        spec = EditSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
        conn = connect(database)
        try:
            from studio.creative.preference import record_final_survival

            first_row = conn.execute(
                """
                SELECT spec_json FROM edit_specs
                WHERE project_id=? AND version=coalesce(
                  (
                    SELECT min(spec_version) FROM renders
                    WHERE project_id=? AND preset LIKE '%H.264%'
                      AND status='complete'
                  ),
                  (
                    SELECT min(version) FROM edit_specs WHERE project_id=?
                  )
                )
                """,
                (project_id, project_id, project_id),
            ).fetchone()
            if first_row is not None:
                first_cut = EditSpec.model_validate_json(first_row["spec_json"])
                if not conn.execute(
                    """
                    SELECT 1 FROM feedback_events
                    WHERE project_id=? AND kind='survival' AND spec_version=?
                    LIMIT 1
                    """,
                    (project_id, spec.revision),
                ).fetchone():
                    record_final_survival(
                        conn,
                        project_id=project_id,
                        first_cut=first_cut,
                        final=spec,
                    )
            transition(conn, project_id, WorkflowState.LOCKED)
            adapter = ResolveAdapter.open()
            compiler = ResolveCompiler(
                adapter,
                DatabaseResolver(database, prefer_proxy=False),
                resolve_shot=DatabaseShotResolver(database),
                state_dir=project_root,
            )
            build = compiler.build(spec)
            transition(conn, project_id, WorkflowState.MASTER_RENDER)
            render_id, rendered = render_spec(
                adapter,
                conn,
                spec,
                kind="master",
                output_dir=project_root / "renders",
            )
            qa = run_technical_qa(
                rendered.output,
                expected_duration=spec.duration_sec,
                expected_width=spec.canvas.width,
                expected_height=spec.canvas.height,
                expected_fps=Fraction(spec.timebase.num, spec.timebase.den),
                expected_audio=True,
                expected_freeze_ranges=[
                    (
                        max(0.0, clip.timeline.in_sec - 2 / float(spec.timebase.fps)),
                        min(
                            spec.duration_sec,
                            clip.timeline.in_sec
                            + clip.timeline.duration_sec
                            + 2 / float(spec.timebase.fps),
                        ),
                    )
                    for clip in spec.clips
                    if clip.shot_id
                    and (
                        conn.execute(
                            """
                            SELECT coalesce(subject_motion,motion_mag,1)
                            FROM shots WHERE id=?
                            """,
                            (clip.shot_id,),
                        ).fetchone()[0]
                        <= 0.01
                    )
                ],
                expected_silence_ranges=[
                    (marker.sec, marker.sec + marker.duration_sec)
                    for marker in spec.markers
                    if marker.kind == "expected_silence" and marker.duration_sec > 0
                ],
                render_id=render_id,
                conn=conn,
            )
            transition(conn, project_id, WorkflowState.FINAL_QA)
            if qa.passed:
                transition(
                    conn,
                    project_id,
                    WorkflowState.DELIVERED,
                    payload={"technical_qa_passed": True, "render_id": render_id},
                )
            return {
                "project_id": project_id,
                "spec_version": spec.revision,
                "build": build.to_dict(),
                "render_id": render_id,
                "technical_qa": qa.model_dump(mode="json"),
                "delivery_url": f"/projects/{project_id}/delivery",
            }
        except Exception as exc:
            record_failure(project_id, exc, step="master_render")
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.get("/projects/{project_id}/delivery")
    def delivery(project_id: str):
        """Expose only persisted Resolve master + Technical QA evidence."""
        conn = connect(database)
        try:
            row = conn.execute(
                """
                SELECT r.id,r.spec_version,r.output_path,r.status,r.preset,
                       q.passed,q.checks_json
                FROM renders r
                LEFT JOIN qa_results q
                  ON q.id=(
                    SELECT q2.id FROM qa_results q2
                    WHERE q2.render_id=r.id AND q2.kind='technical'
                    ORDER BY q2.id DESC LIMIT 1
                  )
                WHERE r.project_id=? AND r.preset LIKE '%H.265%'
                ORDER BY r.spec_version DESC,r.finished_at DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                return {
                    "project_id": project_id,
                    "status": "pending",
                    "passed": False,
                    "checks": [],
                    "output_path": None,
                }
            checks = json.loads(row["checks_json"]) if row["checks_json"] else []
            passed = bool(
                row["status"] == "complete"
                and row["passed"] == 1
                and len(checks) == 13
                and all(item.get("passed") is True for item in checks)
            )
            return {
                "project_id": project_id,
                "render_id": row["id"],
                "spec_version": row["spec_version"],
                "status": "passed" if passed else row["status"],
                "passed": passed,
                "checks": checks,
                "output_path": row["output_path"] if passed else None,
            }
        finally:
            conn.close()

    @app.post("/projects/{project_id}/publish")
    def mark_published(project_id: str):
        """Record owner confirmation; external platform upload is out of scope."""
        conn = connect(database)
        try:
            state = current_state(conn, project_id)
            if state is None or state.state != WorkflowState.DELIVERED.value:
                raise HTTPException(
                    status_code=409,
                    detail="只有 Technical QA 通过的 DELIVERED 项目可标记已发布",
                )
            saved = transition(
                conn,
                project_id,
                WorkflowState.PUBLISHED,
                payload={"source": "owner_confirmation"},
            )
            return {"project_id": project_id, "state": saved.state}
        finally:
            conn.close()

    @app.get("/review-assets/{group_id}/{filename}")
    def review_asset(group_id: str, filename: str):
        """Serve only files belonging to the persisted candidate group."""
        if Path(filename).name != filename:
            raise HTTPException(status_code=400, detail="非法文件名")
        conn = connect(database)
        try:
            row = conn.execute(
                "SELECT project_id FROM candidate_groups WHERE id=?", (group_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="候选组不存在")
        target = (
            projects_root / row["project_id"] / "previews" / group_id / filename
        ).resolve()
        root = (projects_root / row["project_id"] / "previews" / group_id).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="预览文件不存在")
        return FileResponse(target)

    @app.get("/projects/{project_id}/download")
    def download(project_id: str):
        conn = connect(database)
        try:
            row = conn.execute(
                """
                SELECT r.output_path,q.passed,q.checks_json
                FROM renders r JOIN qa_results q ON q.render_id=r.id
                WHERE r.project_id=? AND r.status='complete'
                  AND r.preset LIKE '%H.265%' AND q.kind='technical'
                ORDER BY r.spec_version DESC,q.id DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="母版尚未生成")
        checks = json.loads(row["checks_json"])
        if not (
            row["passed"] == 1
            and len(checks) == 13
            and all(item.get("passed") is True for item in checks)
        ):
            raise HTTPException(status_code=409, detail="Technical QA 尚未通过")
        target = Path(row["output_path"]).resolve()
        if not target.is_file():
            raise HTTPException(status_code=404, detail="母版文件不存在")
        return FileResponse(
            target,
            media_type="video/quicktime",
            filename=target.name,
        )

    @app.get("/recipe-reviews")
    def recipe_reviews():
        from studio.execution.recipes import list_recipe_reviews

        return {
            "recipes": [
                {
                    **item.model_dump(mode="json"),
                    "preview_url": f"/recipe-reviews/{item.id}/preview",
                }
                for item in list_recipe_reviews()
            ]
        }

    @app.get("/recipe-reviews/{recipe_id}/preview")
    def recipe_preview(recipe_id: str):
        from studio.execution.recipes import list_recipe_reviews

        item = next(
            (row for row in list_recipe_reviews() if row.id == recipe_id),
            None,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Recipe 不存在")
        target = (REPO / item.preview).resolve()
        recipes_root = (REPO / "recipes").resolve()
        if recipes_root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="Recipe preview 不存在")
        return FileResponse(target, media_type="video/mp4")

    @app.post("/recipe-reviews/{recipe_id}/decision")
    def recipe_decision(recipe_id: str, request: RecipeDecisionRequest):
        from typing import cast

        from studio.execution.recipes import record_recipe_decision
        from studio.execution.recipes.acceptance import Decision

        try:
            result = record_recipe_decision(
                recipe_id,
                reviewer=request.reviewer,
                decision=cast(Decision, request.decision),
                notes=request.notes,
                reviewed_at=request.reviewed_at,
            )
            return result.model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Recipe 不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    web_dist = REPO / "review-web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="review-ui")

    return app


__all__ = [
    "RecipeDecisionRequest",
    "RevisionRequest",
    "SelectionRequest",
    "create_review_app",
]

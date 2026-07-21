"""Brief, scoring, review, gap analysis, blueprints, and preference learning."""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config, db, editspec, risk

SCORE_VERSION = "decision-loop-v1"
DEFAULT_WEIGHTS = {
    "technical_quality": 0.15,
    "composition_quality": 0.15,
    "brief_match": 0.20,
    "structure_match": 0.15,
    "preference_score": 0.20,
    "diversity_score": 0.10,
    "risk_penalty": 0.05,
}
DEFAULT_STRUCTURE = {
    "hook": ["eye_detail", "face_closeup", "hero_pose"],
    "build": ["relationship", "environment", "action_start", "transition"],
    "climax": ["action_motion", "action_impact", "reaction"],
    "release": ["reaction", "calm_release", "relationship"],
    "ending": ["ending_image", "calm_release", "environment"],
}
VARIANT_PROFILES = {
    "emotion": {"hook": "face_closeup", "build": "relationship", "climax": "reaction", "release": "calm_release", "ending": "ending_image"},
    "action": {"hook": "hero_pose", "build": "action_start", "climax": "action_impact", "release": "reaction", "ending": "ending_image"},
    "narrative": {"hook": "eye_detail", "build": "environment", "climax": "action_motion", "release": "relationship", "ending": "calm_release"},
}


@dataclass
class ShotFeatures:
    technical_quality: float
    composition_quality: float
    character_salience: float
    emotion_intensity: float
    action_intensity: float
    hook_potential: float
    climax_potential: float
    ending_potential: float
    vertical_crop_score: float
    subtitle_risk: float
    watermark_risk: float
    diversity_score: float
    preference_score: float
    final_score: float
    brief_match: float
    structure_match: float
    explanation: dict


def _weights() -> dict[str, float]:
    return {**DEFAULT_WEIGHTS, **(config.get("scoring", "weights", {}) or {})}


def _loads_json(text: str | None, fallback):
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def _norm(value: float | None, max_value: float) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(float(value) / max_value, 1.0))


def _text_words(*parts: str | None) -> set[str]:
    words: set[str] = set()
    for part in parts:
        if not part:
            continue
        for item in str(part).replace(",", " ").replace("_", " ").split():
            token = item.strip().lower()
            if token:
                words.add(token)
    return words


def classify_prototype(row: dict) -> str:
    tags = _text_words(row.get("tags"), row.get("action"), row.get("emotion"), row.get("camera"))
    motion = float(row.get("motion_mag") or 0)
    sharp = float(row.get("sharpness") or 0)
    emotion = (row.get("emotion") or "").lower()
    camera = (row.get("camera") or "").lower()
    character = (row.get("character") or "").lower()
    brightness = float(row.get("brightness") or 0)

    if {"eye", "eyes", "eyewear"} & tags:
        return "eye_detail"
    if {"close-up", "close", "portrait", "face"} & tags or ("close" in camera and character):
        return "face_closeup"
    if {"hero", "solo"} & tags:
        return "hero_pose"
    if {"impact", "explosion", "blast"} & tags or motion >= 0.9:
        return "action_impact"
    if {"run", "fight", "dash", "motion", "action"} & tags or motion >= 0.55:
        return "action_motion"
    if {"draw", "start", "windup"} & tags:
        return "action_start"
    if {"reaction", "shock", "cry"} & tags or emotion in {"sad", "angry", "surprised"}:
        return "reaction"
    if {"city", "sky", "scenery", "wide"} & tags or "wide" in camera:
        return "environment"
    if {"pair", "duo", "group", "relationship"} & tags:
        return "relationship"
    if {"transition", "blur", "flash"} & tags:
        return "transition"
    if brightness < 0.35 and motion < 0.35:
        return "ending_image"
    if sharp > 250 and motion < 0.3:
        return "calm_release"
    return "hero_pose" if character else "environment"


def _structure_match(role: str, prototype: str) -> float:
    wanted = DEFAULT_STRUCTURE.get(role, [])
    if prototype in wanted:
        return 1.0
    return 0.45 if prototype in DEFAULT_STRUCTURE.get("build", []) else 0.15


def _brief_match(brief: dict, row: dict, prototype: str) -> tuple[float, dict]:
    brief_words = _text_words(
        brief.get("character_query"),
        brief.get("theme"),
        " ".join(brief.get("target_emotions", [])),
    )
    shot_words = _text_words(
        row.get("character"),
        row.get("action"),
        row.get("emotion"),
        row.get("tags"),
        prototype,
    )
    if not brief_words:
        return 0.5, {"matched_terms": [], "reason": "no brief terms"}
    matched = sorted(brief_words & shot_words)
    score = min(len(matched) / max(len(brief_words), 1), 1.0)
    return score, {"matched_terms": matched}


def _load_model(scope: str = "global") -> dict | None:
    conn = db.connect()
    row = conn.execute(
        "SELECT model_type, version, features_json, model_json, trained_on FROM preference_models WHERE scope=?",
        (scope,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "model_type": row["model_type"],
        "version": row["version"],
        "features": _loads_json(row["features_json"], []),
        "model": _loads_json(row["model_json"], {}),
        "trained_on": row["trained_on"],
    }


def _rule_preference(tags: set[str], scope: str = "global") -> tuple[float, dict]:
    model = _load_model(scope)
    if not model or model["model_type"] != "rule":
        return 0.5, {"mode": "cold_start"}
    weights = model["model"].get("tag_weights", {})
    score = 0.5 + sum(float(weights.get(tag, 0)) for tag in tags)
    return max(0.0, min(score, 1.0)), {"mode": "rule", "tag_weights": {k: weights[k] for k in tags if k in weights}}


def _logistic_preference(features: dict[str, float], scope: str = "global") -> tuple[float, dict]:
    model = _load_model(scope)
    if not model or model["model_type"] != "logistic":
        return _rule_preference(set(), scope)
    coeffs = model["model"].get("coefficients", {})
    bias = float(model["model"].get("bias", 0.0))
    value = bias
    contributions = {}
    for name in model["features"]:
        part = float(coeffs.get(name, 0.0)) * float(features.get(name, 0.0))
        value += part
        contributions[name] = round(part, 4)
    prob = 1.0 / (1.0 + math.exp(-value))
    return prob, {"mode": "logistic", "contributions": contributions}


def _preference_score(row: dict, feature_map: dict[str, float], scope: str = "global") -> tuple[float, dict]:
    model = _load_model(scope)
    tags = _text_words(row.get("tags"), row.get("character"), row.get("emotion"))
    if not model:
        return 0.5, {"mode": "cold_start"}
    if model["model_type"] == "logistic":
        return _logistic_preference(feature_map, scope)
    return _rule_preference(tags, scope)


def get_brief(project_id: str) -> dict | None:
    conn = db.connect()
    row = conn.execute("SELECT * FROM creative_briefs WHERE project_id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data["target_emotions"] = _loads_json(data.get("target_emotions"), [])
    data["structure_json"] = _loads_json(data.get("structure_json"), DEFAULT_STRUCTURE)
    return data


def upsert_brief(project_id: str, payload: dict) -> dict:
    target_emotions = payload.get("target_emotions") or payload.get("emotion") or []
    if isinstance(target_emotions, str):
        target_emotions = [item.strip() for item in target_emotions.split(",") if item.strip()]
    structure = payload.get("structure_json") or payload.get("structure") or DEFAULT_STRUCTURE
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO creative_briefs (
            project_id, character_query, theme, target_emotions, duration_sec,
            aspect_ratio, target_platform, structure_json, reference_video_path,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(project_id) DO UPDATE SET
            character_query=excluded.character_query,
            theme=excluded.theme,
            target_emotions=excluded.target_emotions,
            duration_sec=excluded.duration_sec,
            aspect_ratio=excluded.aspect_ratio,
            target_platform=excluded.target_platform,
            structure_json=excluded.structure_json,
            reference_video_path=excluded.reference_video_path,
            updated_at=datetime('now')
        """,
        (
            project_id,
            payload.get("character_query") or payload.get("character"),
            payload.get("theme"),
            db.json_dumps(target_emotions),
            payload.get("duration_sec") or payload.get("duration"),
            payload.get("aspect_ratio") or payload.get("aspect"),
            payload.get("target_platform") or payload.get("platform"),
            db.json_dumps(structure),
            payload.get("reference_video_path") or payload.get("reference"),
        ),
    )
    conn.commit()
    conn.close()
    return get_brief(project_id) or {}


def _score_row(row: dict, brief: dict | None, scope: str = "global") -> ShotFeatures:
    technical_quality = round((_norm(row.get("sharpness"), 450) * 0.6 + _norm(row.get("brightness"), 1.0) * 0.4), 4)
    composition_quality = round((1.0 - min(abs((row.get("reframe_x") or 0.0)), 1.0)) * 0.4 + _norm(row.get("sharpness"), 350) * 0.6, 4)
    character_salience = 1.0 if row.get("character") else (0.7 if "face" in (row.get("tags") or "") else 0.2)
    emotion_intensity = 1.0 if row.get("emotion") else min(_norm(row.get("brightness"), 1.0) + 0.2, 1.0)
    action_intensity = min(_norm(row.get("motion_mag"), 2.0) + (0.25 if row.get("action") else 0.0), 1.0)
    prototype = classify_prototype(row)
    hook_potential = 1.0 if prototype in {"eye_detail", "face_closeup", "hero_pose"} else 0.35
    climax_potential = 1.0 if prototype in {"action_motion", "action_impact", "reaction"} else 0.25
    ending_potential = 1.0 if prototype in {"ending_image", "calm_release", "environment"} else 0.2
    vertical_crop_score = 1.0 if (row.get("fill_mode") or "crop") == "crop" else 0.55
    subtitle_risk = float(row.get("subtitle_risk") or 0.0)
    watermark_risk = float(row.get("watermark_risk") or 0.0)
    diversity_score = 0.6
    brief_match, brief_explain = _brief_match(brief or {}, row, prototype)
    structure_role = "build"
    if brief:
        structure_role = next((name for name, wanted in brief.get("structure_json", DEFAULT_STRUCTURE).items() if prototype in wanted), "build")
    structure_match = _structure_match(structure_role, prototype)
    feature_map = {
        "technical_quality": technical_quality,
        "composition_quality": composition_quality,
        "character_salience": character_salience,
        "emotion_intensity": emotion_intensity,
        "action_intensity": action_intensity,
        "vertical_crop_score": vertical_crop_score,
        "subtitle_risk": subtitle_risk,
        "watermark_risk": watermark_risk,
    }
    preference_score, pref_explain = _preference_score(row, feature_map)
    w = _weights()
    risk_penalty = max(subtitle_risk, watermark_risk)
    final_score = (
        technical_quality * w["technical_quality"]
        + composition_quality * w["composition_quality"]
        + brief_match * w["brief_match"]
        + structure_match * w["structure_match"]
        + preference_score * w["preference_score"]
        + diversity_score * w["diversity_score"]
        - risk_penalty * w["risk_penalty"]
    )
    explanation = {
        "prototype": prototype,
        "structure_role": structure_role,
        "brief": brief_explain,
        "preference": pref_explain,
        "weights": w,
        "risk_penalty": risk_penalty,
        "subtitle_regions": row.get("subtitle_regions", {}),
        "watermark_regions": row.get("watermark_regions", {}),
        "crop_avoidable": row.get("crop_avoidable", False),
        "suggested_action": row.get("suggested_action", "人工审查"),
    }
    return ShotFeatures(
        technical_quality=technical_quality,
        composition_quality=composition_quality,
        character_salience=character_salience,
        emotion_intensity=emotion_intensity,
        action_intensity=action_intensity,
        hook_potential=hook_potential,
        climax_potential=climax_potential,
        ending_potential=ending_potential,
        vertical_crop_score=vertical_crop_score,
        subtitle_risk=subtitle_risk,
        watermark_risk=watermark_risk,
        diversity_score=diversity_score,
        preference_score=preference_score,
        final_score=round(final_score, 4),
        brief_match=brief_match,
        structure_match=structure_match,
        explanation=explanation,
    )


def score_project_shots(project_id: str) -> dict:
    brief = get_brief(project_id)
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT s.*, a.path AS asset_path, a.proxy_path, a.width, a.height, a.duration,
               COALESCE(ss.subtitle_risk, 0) AS subtitle_risk,
               COALESCE(ss.watermark_risk, 0) AS watermark_risk
        FROM shots s
        JOIN assets a ON a.id=s.asset_id
        LEFT JOIN shot_scores ss ON ss.shot_id=s.id
        ORDER BY s.asset_id, s.idx
        """
    ).fetchall()
    scored = []
    for row in rows:
        payload = dict(row)
        payload.update(risk.assess_keyframe(payload.get("keyframe")))
        features = _score_row(payload, brief)
        conn.execute(
            """
            INSERT INTO shot_scores (
                shot_id, technical_quality, composition_quality, character_salience,
                emotion_intensity, action_intensity, hook_potential, climax_potential,
                ending_potential, vertical_crop_score, subtitle_risk, watermark_risk,
                diversity_score, preference_score, final_score, score_version, explanation_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(shot_id) DO UPDATE SET
                technical_quality=excluded.technical_quality,
                composition_quality=excluded.composition_quality,
                character_salience=excluded.character_salience,
                emotion_intensity=excluded.emotion_intensity,
                action_intensity=excluded.action_intensity,
                hook_potential=excluded.hook_potential,
                climax_potential=excluded.climax_potential,
                ending_potential=excluded.ending_potential,
                vertical_crop_score=excluded.vertical_crop_score,
                subtitle_risk=excluded.subtitle_risk,
                watermark_risk=excluded.watermark_risk,
                diversity_score=excluded.diversity_score,
                preference_score=excluded.preference_score,
                final_score=excluded.final_score,
                score_version=excluded.score_version,
                explanation_json=excluded.explanation_json,
                updated_at=datetime('now')
            """,
            (
                payload["id"],
                features.technical_quality,
                features.composition_quality,
                features.character_salience,
                features.emotion_intensity,
                features.action_intensity,
                features.hook_potential,
                features.climax_potential,
                features.ending_potential,
                features.vertical_crop_score,
                features.subtitle_risk,
                features.watermark_risk,
                features.diversity_score,
                features.preference_score,
                features.final_score,
                SCORE_VERSION,
                db.json_dumps(features.explanation),
            ),
        )
        scored.append(features.final_score)
    conn.commit()
    conn.close()
    return {"project_id": project_id, "scored_shots": len(rows), "max_score": max(scored) if scored else 0}


def _shot_rows(project_id: str) -> list[dict]:
    score_project_shots(project_id)
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT s.*, a.path AS asset_path, a.proxy_path, a.width, a.height, a.fps, a.duration AS asset_duration,
               sc.technical_quality, sc.composition_quality, sc.character_salience, sc.emotion_intensity,
               sc.action_intensity, sc.hook_potential, sc.climax_potential, sc.ending_potential,
               sc.vertical_crop_score, sc.subtitle_risk, sc.watermark_risk, sc.diversity_score,
               sc.preference_score, sc.final_score, sc.score_version, sc.explanation_json,
               rv.decision, rv.reasons, rv.rating, rv.trim_start_sec, rv.trim_end_sec, rv.preferred_role,
               sr.source_type, sr.source_url, sr.creator, sr.title, sr.license, sr.commercial_allowed,
               sr.status AS source_status
        FROM shots s
        JOIN assets a ON a.id=s.asset_id
        LEFT JOIN shot_scores sc ON sc.shot_id=s.id
        LEFT JOIN review_decisions rv ON rv.project_id=? AND rv.shot_id=s.id
        LEFT JOIN source_records sr ON sr.asset_id=s.asset_id
        ORDER BY sc.final_score DESC, s.asset_id, s.idx
        """,
        (project_id,),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["reasons"] = _loads_json(item.get("reasons"), [])
        item["explanation_json"] = _loads_json(item.get("explanation_json"), {})
        item["prototype"] = classify_prototype(item)
        result.append(item)
    return result


def list_project_shots(project_id: str) -> list[dict]:
    return _shot_rows(project_id)


def get_project(project_id: str) -> dict:
    brief = get_brief(project_id)
    shots = _shot_rows(project_id)
    return {
        "project_id": project_id,
        "brief": brief,
        "shot_count": len(shots),
        "reviewed_count": sum(1 for shot in shots if shot.get("decision")),
        "top_score": max((shot.get("final_score") or 0) for shot in shots) if shots else 0,
    }


def list_reviews(project_id: str) -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM review_decisions WHERE project_id=? ORDER BY updated_at DESC, id DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["reasons"] = _loads_json(item.get("reasons"), [])
        result.append(item)
    return result


def put_review(project_id: str, shot_id: str, payload: dict) -> dict:
    reasons = payload.get("reasons") or []
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO review_decisions (
            project_id, shot_id, decision, reasons, rating, trim_start_sec, trim_end_sec, preferred_role, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(project_id, shot_id) DO UPDATE SET
            decision=excluded.decision,
            reasons=excluded.reasons,
            rating=excluded.rating,
            trim_start_sec=COALESCE(excluded.trim_start_sec, review_decisions.trim_start_sec),
            trim_end_sec=COALESCE(excluded.trim_end_sec, review_decisions.trim_end_sec),
            preferred_role=excluded.preferred_role,
            updated_at=datetime('now')
        """,
        (
            project_id,
            shot_id,
            payload["decision"],
            db.json_dumps(reasons),
            payload.get("rating"),
            payload.get("trim_start_sec"),
            payload.get("trim_end_sec"),
            payload.get("preferred_role"),
        ),
    )
    if payload["decision"] == "use":
        conn.execute("UPDATE shots SET picked=COALESCE(picked, 0) + 1 WHERE id=?", (shot_id,))
    conn.commit()
    conn.close()
    return next(item for item in list_reviews(project_id) if item["shot_id"] == shot_id)


def patch_trim(project_id: str, shot_id: str, trim_start_sec: float | None, trim_end_sec: float | None) -> dict:
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO review_decisions (project_id, shot_id, decision, reasons, trim_start_sec, trim_end_sec, updated_at)
        VALUES (?, ?, 'alternate', '[]', ?, ?, datetime('now'))
        ON CONFLICT(project_id, shot_id) DO UPDATE SET
            trim_start_sec=excluded.trim_start_sec,
            trim_end_sec=excluded.trim_end_sec,
            updated_at=datetime('now')
        """,
        (project_id, shot_id, trim_start_sec, trim_end_sec),
    )
    conn.commit()
    conn.close()
    return next(item for item in list_reviews(project_id) if item["shot_id"] == shot_id)


def upsert_source_record(asset_id: str, payload: dict) -> dict:
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO source_records (
            asset_id, source_type, source_url, creator, title, license, license_url,
            commercial_allowed, modification_allowed, attribution_required, attribution_text,
            permission_proof_path, acquired_at, license_checked_at, expires_at, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(asset_id) DO UPDATE SET
            source_type=excluded.source_type,
            source_url=excluded.source_url,
            creator=excluded.creator,
            title=excluded.title,
            license=excluded.license,
            license_url=excluded.license_url,
            commercial_allowed=excluded.commercial_allowed,
            modification_allowed=excluded.modification_allowed,
            attribution_required=excluded.attribution_required,
            attribution_text=excluded.attribution_text,
            permission_proof_path=excluded.permission_proof_path,
            acquired_at=excluded.acquired_at,
            license_checked_at=excluded.license_checked_at,
            expires_at=excluded.expires_at,
            status=excluded.status,
            notes=excluded.notes
        """,
        (
            asset_id,
            payload.get("source_type"),
            payload.get("source_url"),
            payload.get("creator"),
            payload.get("title"),
            payload.get("license"),
            payload.get("license_url"),
            payload.get("commercial_allowed"),
            payload.get("modification_allowed"),
            payload.get("attribution_required"),
            payload.get("attribution_text"),
            payload.get("permission_proof_path"),
            payload.get("acquired_at"),
            payload.get("license_checked_at"),
            payload.get("expires_at"),
            payload.get("status", "review"),
            payload.get("notes"),
        ),
    )
    row = conn.execute("SELECT * FROM source_records WHERE asset_id=?", (asset_id,)).fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else {"asset_id": asset_id}


def list_sources() -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT sr.*, a.path, a.width, a.height, a.duration FROM source_records sr LEFT JOIN assets a ON a.id=sr.asset_id ORDER BY sr.asset_id"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def gap_analysis(project_id: str) -> dict:
    brief = get_brief(project_id) or {"structure_json": DEFAULT_STRUCTURE}
    shots = _shot_rows(project_id)
    structure = brief.get("structure_json") or DEFAULT_STRUCTURE
    summary = {}
    by_role: dict[str, list[dict]] = defaultdict(list)
    for shot in shots:
        proto = shot["prototype"]
        for role, needs in structure.items():
            if proto in needs:
                by_role[role].append(shot)
    for role, needed in structure.items():
        pool = by_role.get(role, [])
        prototypes = Counter(shot["prototype"] for shot in pool)
        high_quality = [shot for shot in pool if (shot.get("final_score") or 0) >= 0.72]
        clean = [shot for shot in pool if (shot.get("subtitle_risk") or 0) < 0.25]
        char_match = [shot for shot in pool if brief.get("character_query") and brief["character_query"].lower() in " ".join(_text_words(shot.get("character"), shot.get("tags")))]
        emo_match = [shot for shot in pool if any(em.lower() in " ".join(_text_words(shot.get("emotion"), shot.get("tags"))) for em in brief.get("target_emotions", []))]
        missing = [proto for proto in needed if prototypes.get(proto, 0) == 0]
        summary[role] = {
            "required_types": needed,
            "have_count": len(pool),
            "usable_count": sum(1 for shot in pool if (shot.get("decision") or "use") != "reject"),
            "high_quality_count": len(high_quality),
            "clean_count": len(clean),
            "diversity": round(len(prototypes) / max(len(needed), 1), 3),
            "character_match_rate": round(len(char_match) / max(len(pool), 1), 3),
            "emotion_match_rate": round(len(emo_match) / max(len(pool), 1), 3),
            "vertical_match_rate": round(sum(1 for shot in pool if (shot.get("vertical_crop_score") or 0) >= 0.75) / max(len(pool), 1), 3),
            "missing_types": missing,
            "search_keywords": [f"{brief.get('character_query') or ''} {proto}".strip() for proto in missing],
            "suggested_shots": [f"{role.title()} 缺少 {proto.replace('_', ' ')}" for proto in missing],
        }
    return {"project_id": project_id, "brief": brief, "segments": summary}


def _shot_to_candidate(row: dict, start_frame: int, duration_frames: int) -> editspec.Shot:
    src = row.get("proxy_path") or row.get("asset_path")
    return editspec.Shot(
        id=row["id"],
        src=src,
        source_in_sec=row.get("trim_start_sec") if row.get("trim_start_sec") is not None else row["start_sec"],
        start_frame=start_frame,
        duration_in_frames=duration_frames,
        reframe_x=row.get("reframe_x") or 0.0,
        fill_mode=row.get("fill_mode") or "crop",
    )


def _render_preview(spec: editspec.EditSpec, output: Path) -> str:
    ffmpeg = config.tool("ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="anime-blueprint-preview-") as temp_dir:
        temp_root = Path(temp_dir)
        segments = []
        for index, shot in enumerate(spec.shots):
            seg = temp_root / f"{index:03d}.mp4"
            duration = max(shot.duration_in_frames / spec.fps, 0.2)
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    f"{shot.source_in_sec:.3f}",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    shot.src,
                    "-vf",
                    "scale=-2:720",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(seg),
                ],
                check=True,
            )
            segments.append(seg)
        concat = temp_root / "concat.txt"
        concat.write_text("".join(f"file '{segment.as_posix()}'\n" for segment in segments))
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-c",
                "copy",
                str(output),
            ],
            check=True,
        )
    return str(output)


def generate_blueprints(project_id: str, variant_types: list[str] | None = None) -> dict:
    variant_types = variant_types or ["emotion", "action", "narrative"]
    brief = get_brief(project_id)
    if not brief:
        raise ValueError("请先创建 creative brief")
    shots = [shot for shot in _shot_rows(project_id) if shot.get("decision") != "reject"]
    if not shots:
        raise ValueError("当前没有可用镜头")
    out = []
    proj_dir = config.PROJECTS / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    aspect = (brief.get("aspect_ratio") or "4:5").replace(":", "x")
    canvas = "4x5" if aspect == "4x5" else "auto"
    for variant in variant_types:
        profile = VARIANT_PROFILES[variant]
        selected = []
        explanation = {"variant_type": variant, "selections": [], "gap_analysis": gap_analysis(project_id)["segments"]}
        used = set()
        start_frame = 0
        total_duration = int((brief.get("duration_sec") or 25) * 30)
        per_role = max(total_duration // max(len(profile), 1), 45)
        for role, prototype in profile.items():
            pool = [shot for shot in shots if shot["prototype"] == prototype and shot["id"] not in used]
            if not pool:
                pool = [shot for shot in shots if shot["id"] not in used]
            if not pool:
                pool = [shot for shot in shots if shot["prototype"] == prototype]
            if not pool:
                pool = list(shots)
            pick = max(pool, key=lambda shot: shot.get("final_score") or 0)
            used.add(pick["id"])
            duration_frames = max(30, min(per_role, round((pick["end_sec"] - pick["start_sec"]) * 30)))
            selected.append(_shot_to_candidate(pick, start_frame, duration_frames))
            start_frame += duration_frames
            explanation["selections"].append(
                {
                    "shot_id": pick["id"],
                    "role": role,
                    "prototype": pick["prototype"],
                    "reason": f"{variant} variant prefers {prototype} for {role}",
                    "alternates": [item["id"] for item in sorted(pool, key=lambda shot: shot.get("final_score") or 0, reverse=True)[1:4]],
                }
            )
        width, height, _ = editspec.choose_canvas(
            [{"asset_id": shot["asset_id"]} for shot in shots[:1]] or [{"asset_id": shots[0]["asset_id"]}],
            canvas=canvas,
        )
        spec = editspec.EditSpec(id=project_id, fps=30, width=width, height=height, duration_in_frames=start_frame, shots=selected)
        editspec_path = editspec.save(spec, name=f"blueprint.{variant}")
        preview_path = proj_dir / "outputs" / f"blueprint.{variant}.preview.mp4"
        rendered_preview = _render_preview(spec, preview_path)
        conn = db.connect()
        brief_row = conn.execute("SELECT id FROM creative_briefs WHERE project_id=?", (project_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO cut_variants (project_id, brief_id, variant_type, editspec_path, preview_path, score, explanation_json, selected)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                project_id,
                brief_row["id"] if brief_row else None,
                variant,
                str(editspec_path),
                rendered_preview,
                round(sum((shot.get("final_score") or 0) for shot in shots[: len(selected)]) / max(len(selected), 1), 4),
                db.json_dumps(explanation),
            ),
        )
        variant_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.commit()
        conn.close()
        out.append({"id": variant_id, "variant_type": variant, "editspec_path": str(editspec_path), "preview_path": rendered_preview, "explanation": explanation})
    return {"project_id": project_id, "variants": out}


def list_variants(project_id: str) -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM cut_variants WHERE project_id=? ORDER BY selected DESC, score DESC, id DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["explanation_json"] = _loads_json(item.get("explanation_json"), {})
        result.append(item)
    return result


def select_variant(project_id: str, variant_id: int) -> dict:
    conn = db.connect()
    conn.execute("UPDATE cut_variants SET selected=CASE WHEN id=? THEN 1 ELSE 0 END WHERE project_id=?", (variant_id, project_id))
    row = conn.execute("SELECT * FROM cut_variants WHERE id=? AND project_id=?", (variant_id, project_id)).fetchone()
    if not row:
        conn.commit()
        conn.close()
        raise ValueError("variant not found")
    spec = json.loads(Path(row["editspec_path"]).read_text())
    for shot in spec.get("shots", []):
        existing = conn.execute(
            "SELECT decision FROM review_decisions WHERE project_id=? AND shot_id=?",
            (project_id, shot["id"]),
        ).fetchone()
        if existing and existing["decision"] == "reject":
            continue
        conn.execute(
            """
            INSERT INTO review_decisions (project_id, shot_id, decision, reasons, preferred_role, updated_at)
            VALUES (?, ?, 'use', '["variant_selected"]', ?, datetime('now'))
            ON CONFLICT(project_id, shot_id) DO UPDATE SET
                decision='use',
                reasons='["variant_selected"]',
                updated_at=datetime('now')
            """,
            (project_id, shot["id"], None),
        )
        conn.execute("UPDATE shots SET picked=COALESCE(picked, 0) + 1 WHERE id=?", (shot["id"],))
    conn.commit()
    conn.close()
    train_preference()
    return {"project_id": project_id, "variant_id": variant_id, "selected": True, "shots": len(spec.get("shots", []))}


def train_preference() -> dict:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT rv.decision, s.tags, s.character, s.emotion, s.sharpness, s.motion_mag, s.brightness, s.reframe_x
        FROM review_decisions rv
        JOIN shots s ON s.id=rv.shot_id
        WHERE rv.decision IN ('use','reject')
        """
    ).fetchall()
    total = len(rows)
    if total == 0:
        conn.close()
        return {"trained_on": 0, "model_type": "cold_start"}
    if total < 30:
        counts: Counter[str] = Counter()
        for row in rows:
            sign = 0.06 if row["decision"] == "use" else -0.06
            for tag in _text_words(row["tags"], row["character"], row["emotion"]):
                counts[tag] += sign
        payload = {"tag_weights": dict(counts)}
        conn.execute(
            """
            INSERT INTO preference_models (scope, model_type, version, features_json, model_json, trained_on, updated_at)
            VALUES ('global', 'rule', ?, '[]', ?, ?, datetime('now'))
            ON CONFLICT(scope) DO UPDATE SET
                model_type='rule',
                version=excluded.version,
                features_json='[]',
                model_json=excluded.model_json,
                trained_on=excluded.trained_on,
                updated_at=datetime('now')
            """,
            (SCORE_VERSION, db.json_dumps(payload), total),
        )
        conn.commit()
        conn.close()
        return {"trained_on": total, "model_type": "rule", "features": len(payload["tag_weights"])}

    features = ["sharpness", "motion_mag", "brightness", "reframe_x"]
    x = np.array(
        [
            [
                float(row["sharpness"] or 0) / 500.0,
                float(row["motion_mag"] or 0) / 3.0,
                float(row["brightness"] or 0),
                abs(float(row["reframe_x"] or 0)),
            ]
            for row in rows
        ],
        dtype=np.float32,
    )
    y = np.array([1.0 if row["decision"] == "use" else 0.0 for row in rows], dtype=np.float32)
    w = np.zeros(x.shape[1], dtype=np.float32)
    b = 0.0
    lr = 0.2
    for _ in range(400):
        logits = x @ w + b
        pred = 1.0 / (1.0 + np.exp(-logits))
        grad_w = (x.T @ (pred - y)) / len(x)
        grad_b = float(np.mean(pred - y))
        w -= lr * grad_w
        b -= lr * grad_b
    payload = {"coefficients": {name: float(value) for name, value in zip(features, w)}, "bias": float(b)}
    conn.execute(
        """
        INSERT INTO preference_models (scope, model_type, version, features_json, model_json, trained_on, updated_at)
        VALUES ('global', 'logistic', ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(scope) DO UPDATE SET
            model_type='logistic',
            version=excluded.version,
            features_json=excluded.features_json,
            model_json=excluded.model_json,
            trained_on=excluded.trained_on,
            updated_at=datetime('now')
        """,
        (SCORE_VERSION, db.json_dumps(features), db.json_dumps(payload), total),
    )
    conn.commit()
    conn.close()
    return {"trained_on": total, "model_type": "logistic", "features": features}


def explain_preference(shot_id: str) -> dict:
    conn = db.connect()
    row = conn.execute(
        "SELECT tags, character, emotion, sharpness, motion_mag, brightness, reframe_x FROM shots WHERE id=?",
        (shot_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError("shot not found")
    feature_map = {
        "sharpness": float(row["sharpness"] or 0) / 500.0,
        "motion_mag": float(row["motion_mag"] or 0) / 3.0,
        "brightness": float(row["brightness"] or 0),
        "reframe_x": abs(float(row["reframe_x"] or 0)),
        "technical_quality": float(row["sharpness"] or 0) / 500.0,
        "composition_quality": 1.0 - abs(float(row["reframe_x"] or 0)),
        "character_salience": 1.0 if row["character"] else 0.0,
        "emotion_intensity": 1.0 if row["emotion"] else 0.2,
        "action_intensity": float(row["motion_mag"] or 0) / 3.0,
        "vertical_crop_score": 1.0,
        "subtitle_risk": 0.0,
        "watermark_risk": 0.0,
    }
    score, explain = _preference_score(dict(row), feature_map)
    return {"shot_id": shot_id, "preference_score": round(score, 4), "explanation": explain}

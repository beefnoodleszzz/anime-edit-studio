"""Decision-loop workstation core."""
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

SCORE_VERSION = "decision-loop-v2"
BLUEPRINT_FPS = 30
# 低于该帧数的镜头素材边界不足以安全切一刀,应放弃换候选,而不是强行拉长越界。
MIN_SHOT_FRAMES = 8
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


def _reference_dna_path(project_id: str) -> Path:
    return config.PROJECTS / project_id / "reference-dna.json"


def _reference_dna(project_id: str) -> dict | None:
    path = _reference_dna_path(project_id)
    if not path.exists():
        return None
    return _loads_json(path.read_text(), None)


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


def _rule_preference(tags: set[str], scope: str = "global", model: dict | None = None) -> tuple[float, dict]:
    model = model or _load_model(scope)
    if not model or model["model_type"] != "rule":
        return 0.5, {"mode": "cold_start"}
    weights = model["model"].get("tag_weights", {})
    score = 0.5 + sum(float(weights.get(tag, 0.0)) for tag in tags)
    return max(0.0, min(score, 1.0)), {"mode": "rule", "tag_weights": {k: weights[k] for k in tags if k in weights}}


def _logistic_preference(features: dict[str, float], scope: str = "global", model: dict | None = None) -> tuple[float, dict]:
    model = model or _load_model(scope)
    if not model or model["model_type"] != "logistic":
        return _rule_preference(set(), scope, model)
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


def _preference_score(row: dict, feature_map: dict[str, float], scope: str = "global", model: dict | None = None) -> tuple[float, dict]:
    model = model or _load_model(scope)
    tags = _text_words(row.get("tags"), row.get("character"), row.get("emotion"))
    if not model:
        return 0.5, {"mode": "cold_start"}
    if model["model_type"] == "logistic":
        return _logistic_preference(feature_map, scope, model)
    return _rule_preference(tags, scope, model)


def get_brief(project_id: str) -> dict | None:
    conn = db.connect()
    row = conn.execute("SELECT * FROM creative_briefs WHERE project_id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data["target_emotions"] = _loads_json(data.get("target_emotions"), [])
    data["structure_json"] = _loads_json(data.get("structure_json"), DEFAULT_STRUCTURE)
    data["creative_contract_json"] = _loads_json(
        data.get("creative_contract_json"), {})
    return data


def attach_assets(project_id: str, asset_ids: list[str]) -> dict:
    conn = db.connect()
    existing = []
    missing = []
    for asset_id in asset_ids:
        row = db.asset_by_id(conn, asset_id)
        if row:
            existing.append(row["id"])
        else:
            missing.append(asset_id)
    db.attach_project_assets(conn, project_id, existing)
    conn.close()
    return {"project_id": project_id, "attached": existing, "missing": missing}


def _ensure_project_assets(conn, project_id: str, brief: dict | None = None) -> list[str]:
    asset_ids = db.project_asset_ids(conn, project_id)
    if asset_ids:
        return asset_ids
    brief = brief or get_brief(project_id) or {}
    query = (brief.get("character_query") or "").strip().lower()
    if query:
        rows = conn.execute(
            """
            SELECT DISTINCT asset_id FROM shots
            WHERE LOWER(COALESCE(character,'')) LIKE ?
               OR LOWER(COALESCE(tags,'')) LIKE ?
               OR LOWER(COALESCE(dialogue,'')) LIKE ?
            """,
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
        asset_ids = [row["asset_id"] for row in rows]
    if not asset_ids:
        rows = conn.execute("SELECT id FROM assets ORDER BY created_at, id").fetchall()
        if len(rows) == 1:
            asset_ids = [rows[0]["id"]]
    if asset_ids:
        db.attach_project_assets(conn, project_id, asset_ids)
    return asset_ids


def upsert_brief(project_id: str, payload: dict) -> dict:
    target_emotions = payload.get("target_emotions") or payload.get("emotion") or []
    if isinstance(target_emotions, str):
        target_emotions = [item.strip() for item in target_emotions.split(",") if item.strip()]
    structure = payload.get("structure_json") or payload.get("structure") or DEFAULT_STRUCTURE
    contract = payload.get("creative_contract_json") or payload.get("creative_contract") or {}
    for key in ("must_include", "must_avoid"):
        if isinstance(contract.get(key), str):
            contract[key] = [
                item.strip() for item in contract[key].split(",") if item.strip()
            ]
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO creative_briefs (
            project_id, character_query, theme, target_emotions, duration_sec,
            aspect_ratio, target_platform, structure_json, creative_contract_json,
            reference_video_path, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(project_id) DO UPDATE SET
            character_query=excluded.character_query,
            theme=excluded.theme,
            target_emotions=excluded.target_emotions,
            duration_sec=excluded.duration_sec,
            aspect_ratio=excluded.aspect_ratio,
            target_platform=excluded.target_platform,
            structure_json=excluded.structure_json,
            creative_contract_json=excluded.creative_contract_json,
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
            db.json_dumps(contract),
            payload.get("reference_video_path") or payload.get("reference"),
        ),
    )
    _ensure_project_assets(conn, project_id, payload)
    conn.commit()
    conn.close()
    return get_brief(project_id) or {}


def validate_brief(project_id: str) -> dict:
    brief = get_brief(project_id)
    if not brief:
        return {"project_id": project_id, "ready": False,
                "missing": ["creative_brief"], "contract": {}}
    contract = brief.get("creative_contract_json") or {}
    required = (
        "content_lane", "audience_context", "viewer_promise", "payoff",
        "ending_aftertaste", "edit_mode", "visual_motif", "sound_strategy",
        "success_criteria",
    )
    missing = [key for key in required if not contract.get(key)]
    if not brief.get("character_query"):
        missing.append("character_query")
    if not brief.get("target_emotions"):
        missing.append("target_emotions")
    if not brief.get("duration_sec"):
        missing.append("duration_sec")
    if not brief.get("target_platform"):
        missing.append("target_platform")
    summary = (
        f"{brief.get('character_query') or '未定主体'} · "
        f"{contract.get('content_lane') or '未定内容线'} / "
        f"{contract.get('edit_mode') or '未定形态'} · "
        f"承诺：{contract.get('viewer_promise') or '未定'} → "
        f"兑现：{contract.get('payoff') or '未定'} → "
        f"余味：{contract.get('ending_aftertaste') or '未定'}"
    )
    return {"project_id": project_id, "ready": not missing,
            "missing": missing, "summary": summary, "contract": contract}


def _brief_match(brief: dict, row: dict, prototype: str) -> tuple[float, dict]:
    brief_words = _text_words(brief.get("character_query"), brief.get("theme"), " ".join(brief.get("target_emotions", [])))
    shot_words = _text_words(row.get("character"), row.get("action"), row.get("emotion"), row.get("tags"), prototype)
    if not brief_words:
        return 0.5, {"matched_terms": [], "reason": "no brief terms"}
    matched = sorted(brief_words & shot_words)
    score = min(len(matched) / max(len(brief_words), 1), 1.0)
    return score, {"matched_terms": matched}


def _structure_match(role: str, prototype: str) -> float:
    wanted = DEFAULT_STRUCTURE.get(role, [])
    if prototype in wanted:
        return 1.0
    return 0.45 if prototype in DEFAULT_STRUCTURE.get("build", []) else 0.15


def _score_row(row: dict, brief: dict | None, preference_model: dict | None = None) -> ShotFeatures:
    # 画质用 LAION 美学分为主(真"好看"信号,归一 aesthetic/10),清晰度只作次要项;
    # 美学分缺失(未 embed 或权重不可用)时回退旧式清晰度/亮度组合。
    aesthetic = row.get("aesthetic")
    if aesthetic is not None:
        technical_quality = round(min(float(aesthetic) / 10.0, 1.0) * 0.8 + _norm(row.get("sharpness"), 450) * 0.2, 4)
    else:
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
        "sharpness": _norm(row.get("sharpness"), 500),
        "motion_mag": _norm(row.get("motion_mag"), 3),
        "brightness": float(row.get("brightness") or 0),
        "reframe_x": abs(float(row.get("reframe_x") or 0)),
        "technical_quality": technical_quality,
        "composition_quality": composition_quality,
        "character_salience": character_salience,
        "emotion_intensity": emotion_intensity,
        "action_intensity": action_intensity,
        "vertical_crop_score": vertical_crop_score,
        "subtitle_risk": subtitle_risk,
        "watermark_risk": watermark_risk,
    }
    preference_score, pref_explain = _preference_score(row, feature_map, model=preference_model)
    weights = _weights()
    risk_penalty = max(subtitle_risk, watermark_risk)
    final_score = (
        technical_quality * weights["technical_quality"]
        + composition_quality * weights["composition_quality"]
        + brief_match * weights["brief_match"]
        + structure_match * weights["structure_match"]
        + preference_score * weights["preference_score"]
        + diversity_score * weights["diversity_score"]
        - risk_penalty * weights["risk_penalty"]
    )
    explanation = {
        "prototype": prototype,
        "structure_role": structure_role,
        "brief": brief_explain,
        "preference": pref_explain,
        "weights": weights,
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
    preference_model = _load_model()
    asset_ids = _ensure_project_assets(conn, project_id, brief)
    if not asset_ids:
        conn.close()
        return {"project_id": project_id, "scored_shots": 0, "max_score": 0}
    rows = conn.execute(
        f"""
        SELECT s.*, a.path AS asset_path, a.proxy_path, a.width, a.height, a.duration,
               COALESCE(ss.subtitle_risk, 0) AS subtitle_risk,
               COALESCE(ss.watermark_risk, 0) AS watermark_risk
        FROM shots s
        JOIN assets a ON a.id=s.asset_id
        LEFT JOIN shot_scores ss ON ss.shot_id=s.id
        WHERE s.asset_id IN ({",".join("?" for _ in asset_ids)})
        ORDER BY s.asset_id, s.idx
        """,
        asset_ids,
    ).fetchall()
    scored = []
    for row in rows:
        payload = dict(row)
        payload.update(risk.assess_keyframe(payload.get("keyframe")))
        features = _score_row(payload, brief, preference_model)
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
    asset_ids = _ensure_project_assets(conn, project_id)
    if not asset_ids:
        conn.close()
        return []
    rows = conn.execute(
        f"""
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
        WHERE s.asset_id IN ({",".join("?" for _ in asset_ids)})
        ORDER BY sc.final_score DESC, s.asset_id, s.idx
        """,
        (project_id, *asset_ids),
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
        "asset_ids": sorted({shot["asset_id"] for shot in shots}),
    }


def list_reviews(project_id: str) -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM review_decisions WHERE project_id=? ORDER BY updated_at DESC, id DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["reasons"] = _loads_json(item.get("reasons"), [])
        out.append(item)
    return out


def _validate_trim(conn, shot_id: str, trim_start_sec: float | None, trim_end_sec: float | None, project_id: str) -> tuple[float | None, float | None]:
    shot = conn.execute("SELECT start_sec, end_sec FROM shots WHERE id=?", (shot_id,)).fetchone()
    if not shot:
        raise ValueError("shot not found")
    existing = conn.execute(
        "SELECT trim_start_sec, trim_end_sec FROM review_decisions WHERE project_id=? AND shot_id=?",
        (project_id, shot_id),
    ).fetchone()
    start_value = trim_start_sec if trim_start_sec is not None else (existing["trim_start_sec"] if existing else None)
    end_value = trim_end_sec if trim_end_sec is not None else (existing["trim_end_sec"] if existing else None)
    source_start = float(shot["start_sec"])
    source_end = float(shot["end_sec"])
    if start_value is not None and not (source_start <= start_value < source_end):
        raise ValueError("trim_start_sec out of range")
    if end_value is not None and not (source_start < end_value <= source_end):
        raise ValueError("trim_end_sec out of range")
    if start_value is not None and end_value is not None and not (start_value < end_value):
        raise ValueError("trim_start_sec must be earlier than trim_end_sec")
    return start_value, end_value


def put_review(project_id: str, shot_id: str, payload: dict) -> dict:
    reasons = payload.get("reasons") or []
    conn = db.connect()
    existing = conn.execute(
        "SELECT decision FROM review_decisions WHERE project_id=? AND shot_id=?",
        (project_id, shot_id),
    ).fetchone()
    _validate_trim(conn, shot_id, payload.get("trim_start_sec"), payload.get("trim_end_sec"), project_id)
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
    if payload["decision"] == "use" and (not existing or existing["decision"] != "use"):
        conn.execute("UPDATE shots SET picked=COALESCE(picked, 0) + 1 WHERE id=?", (shot_id,))
    conn.commit()
    conn.close()
    return next(item for item in list_reviews(project_id) if item["shot_id"] == shot_id)


def patch_trim(project_id: str, shot_id: str, trim_start_sec: float | None, trim_end_sec: float | None) -> dict:
    conn = db.connect()
    # 只按 I 只发 trim_start、只按 O 只发 trim_end;写入前先与已存值合并,
    # 避免未提交的那一端被覆盖成 NULL(I/O 互相清空)。
    start_value, end_value = _validate_trim(conn, shot_id, trim_start_sec, trim_end_sec, project_id)
    conn.execute(
        """
        INSERT INTO review_decisions (project_id, shot_id, decision, reasons, trim_start_sec, trim_end_sec, updated_at)
        VALUES (?, ?, 'alternate', '[]', ?, ?, datetime('now'))
        ON CONFLICT(project_id, shot_id) DO UPDATE SET
            trim_start_sec=excluded.trim_start_sec,
            trim_end_sec=excluded.trim_end_sec,
            updated_at=datetime('now')
        """,
        (project_id, shot_id, start_value, end_value),
    )
    conn.commit()
    conn.close()
    return next(item for item in list_reviews(project_id) if item["shot_id"] == shot_id)


def upsert_source_record(asset_id: str, payload: dict) -> dict:
    conn = db.connect()
    commercial = payload.get("commercial_allowed")
    status = payload.get("status", "review")
    if commercial is True and status != "blocked":
        status = "approved"
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
            commercial,
            payload.get("modification_allowed"),
            payload.get("attribution_required"),
            payload.get("attribution_text"),
            payload.get("permission_proof_path"),
            payload.get("acquired_at"),
            payload.get("license_checked_at"),
            payload.get("expires_at"),
            status,
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


def audit_sources(project_id: str | None = None) -> dict:
    conn = db.connect()
    asset_ids = db.project_asset_ids(conn, project_id) if project_id else []
    if project_id and asset_ids:
        rows = conn.execute(
            f"""
            SELECT a.id AS asset_id, a.path, sr.status, sr.commercial_allowed, sr.source_url, sr.license
            FROM assets a
            LEFT JOIN source_records sr ON sr.asset_id=a.id
            WHERE a.id IN ({",".join("?" for _ in asset_ids)})
            ORDER BY a.id
            """,
            asset_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT a.id AS asset_id, a.path, sr.status, sr.commercial_allowed, sr.source_url, sr.license
            FROM assets a
            LEFT JOIN source_records sr ON sr.asset_id=a.id
            ORDER BY a.id
            """
        ).fetchall()
    conn.close()
    items = []
    for row in rows:
        item = dict(row)
        status = item.get("status") or "review"
        if item.get("commercial_allowed") == 1 and status != "blocked":
            status = "approved"
        item["effective_status"] = status
        items.append(item)
    return {
        "project_id": project_id,
        "items": items,
        "approved": sum(item["effective_status"] == "approved" for item in items),
        "review": sum(item["effective_status"] == "review" for item in items),
        "blocked": sum(item["effective_status"] == "blocked" for item in items),
    }


def source_report(project_id: str) -> dict:
    # 纯来源报告:列出项目素材的来源/状态备注。不再计算 export_allowed(导出不受权利影响)。
    audit = audit_sources(project_id)
    return {"project_id": project_id, "items": audit["items"]}


def resolve_master_sources(spec: dict) -> dict:
    """返回一个副本:每个镜头的 src 按 shot.id 从 DB 回源到本地母版路径(assets.path)。

    目的是让正式成片用本地最高质量原片,而不是代理文件——不做任何权利拦截。
    解析不到的镜头保持其原 src 不变。source_records 仅用于记录来源,不再阻断导出。
    """
    output = json.loads(json.dumps(spec))
    conn = db.connect()
    try:
        for shot in output.get("shots", []):
            row = conn.execute(
                "SELECT a.path FROM shots s JOIN assets a ON a.id=s.asset_id WHERE s.id=?",
                (shot.get("id"),),
            ).fetchone()
            if row and row["path"]:
                shot["src"] = row["path"]
    finally:
        conn.close()
    return output


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
            "usable_count": sum(1 for shot in pool if shot.get("decision") != "reject"),
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


def _eligible_shots(project_id: str) -> list[dict]:
    # 只排除人工 reject 的镜头;来源状态只是备注,不影响创作选片。
    return [shot for shot in _shot_rows(project_id) if shot.get("decision") != "reject"]


def _segment_frame_plan(brief: dict, reference_dna: dict | None, roles: list[str]) -> dict[str, int]:
    """先算各段相对权重,再统一缩放到 Brief 总时长,最后校正取整误差使总和精确等于 total_frames。

    有 Reference DNA 时 Hook/Ending 用参考片对应秒数、其余段用镜头中位数作为**相对权重**,
    而不是直接当作绝对秒数,避免参考片镜头很短时成片被压到只有几秒。
    """
    fps = BLUEPRINT_FPS
    floor = fps // 2
    total_frames = max(int(round((brief.get("duration_sec") or 25) * fps)), floor * len(roles))
    distribution = {"hook": 0.18, "build": 0.27, "climax": 0.27, "release": 0.14, "ending": 0.14}
    raw: dict[str, float] = {}
    median = None
    if reference_dna:
        median = float(np.median(reference_dna.get("shot_duration_distribution") or [1.0]))
    for role in roles:
        if reference_dna and role == "hook" and reference_dna.get("hook_length"):
            raw[role] = max(float(reference_dna["hook_length"]), 0.05)
        elif reference_dna and role == "ending" and reference_dna.get("ending_shot_length"):
            raw[role] = max(float(reference_dna["ending_shot_length"]), 0.05)
        elif reference_dna and median is not None:
            raw[role] = max(median * (1.25 if role == "build" else 1.0), 0.05)
        else:
            raw[role] = distribution.get(role, 0.2)
    weight_sum = sum(raw.values()) or 1.0
    plan = {role: max(int(round(raw[role] / weight_sum * total_frames)), floor) for role in roles}
    # 校正取整/floor 引入的误差,把差额加到最长的一段,保证 sum(plan) == total_frames。
    diff = total_frames - sum(plan.values())
    if plan and diff:
        target = max(plan, key=plan.get)
        plan[target] = max(plan[target] + diff, floor)
    return plan


def _shot_available_frames(shot: dict) -> int:
    source_in = shot.get("trim_start_sec") if shot.get("trim_start_sec") is not None else shot["start_sec"]
    source_out = shot.get("trim_end_sec") if shot.get("trim_end_sec") is not None else shot["end_sec"]
    return max(round((source_out - source_in) * BLUEPRINT_FPS), 0)


def _pick_duration_frames(shot: dict, requested: int) -> int:
    # 只能在素材实际可用长度内取,绝不通过强行拉长突破人工设置的 Out 点或滑入下一镜头。
    # 零可用长度返回 0,由调用方跳过,不再返回 1 帧违反"永不越界"契约。
    available = _shot_available_frames(shot)
    return max(0, min(requested, available))


def _shot_to_candidate(row: dict, start_frame: int, duration_frames: int, *, master: bool = False) -> editspec.Shot:
    src = row.get("asset_path") if master else (row.get("proxy_path") or row.get("asset_path"))
    source_in = row.get("trim_start_sec") if row.get("trim_start_sec") is not None else row["start_sec"]
    return editspec.Shot(
        id=row["id"],
        src=src,
        source_in_sec=source_in,
        start_frame=start_frame,
        duration_in_frames=_pick_duration_frames(row, duration_frames),
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
                    ffmpeg, "-y", "-v", "error",
                    "-ss", f"{shot.source_in_sec:.3f}",
                    "-t", f"{duration:.3f}",
                    "-i", shot.src,
                    "-vf", f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=decrease,pad={spec.width}:{spec.height}:(ow-iw)/2:(oh-ih)/2:black,scale=-2:720,fps={spec.fps}",
                    "-an", "-r", str(spec.fps), "-g", str(spec.fps),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(seg),
                ],
                check=True,
            )
            segments.append(seg)
        concat = temp_root / "concat.txt"
        concat.write_text("".join(f"file '{segment.as_posix()}'\n" for segment in segments))
        subprocess.run(
            [
                ffmpeg, "-y", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(concat),
                "-r", str(spec.fps), "-g", str(spec.fps),
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(output),
            ],
            check=True,
        )
    return str(output)


def _variant_score(selected_rows: list[dict]) -> float:
    if not selected_rows:
        return 0.0
    return round(sum(float(row.get("final_score") or 0.0) for row in selected_rows) / len(selected_rows), 4)


def generate_blueprints(project_id: str, variant_types: list[str] | None = None) -> dict:
    variant_types = variant_types or ["emotion", "action", "narrative"]
    brief = get_brief(project_id)
    if not brief:
        raise ValueError("请先创建 creative brief")
    shots = _eligible_shots(project_id)
    if not shots:
        raise ValueError("当前没有可用镜头")
    if not any(_shot_available_frames(shot) >= MIN_SHOT_FRAMES for shot in shots):
        raise ValueError("所有可用镜头长度都低于最短帧数,无法生成蓝图")
    proj_dir = config.PROJECTS / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    aspect = (brief.get("aspect_ratio") or "4:5").replace(":", "x")
    canvas = "4x5" if aspect == "4x5" else "auto"
    width, height, _ = editspec.choose_canvas([{"asset_id": shot["asset_id"]} for shot in shots[:1]], canvas=canvas)
    reference_dna = _reference_dna(project_id)
    result = []
    for variant in variant_types:
        profile = VARIANT_PROFILES[variant]
        selected_specs: list[editspec.Shot] = []
        selected_rows: list[dict] = []
        used: set[str] = set()
        explanation = {"variant_type": variant, "reference_dna_used": bool(reference_dna), "selections": [], "gap_analysis": gap_analysis(project_id)["segments"]}
        frame_plan = _segment_frame_plan(brief, reference_dna, list(profile.keys()))
        usable = [shot for shot in shots if _shot_available_frames(shot) >= MIN_SHOT_FRAMES]
        start_frame = 0
        for role, prototype in profile.items():
            target_frames = frame_plan[role]
            accumulated = 0
            guard = 0
            while accumulated < target_frames and guard < len(usable) * 2 + 4:
                guard += 1
                pool = [shot for shot in usable if shot["prototype"] == prototype and shot["id"] not in used]
                if not pool:
                    pool = [shot for shot in usable if shot["id"] not in used]
                if not pool:
                    used.clear()
                    pool = [shot for shot in usable if shot["prototype"] == prototype] or usable
                if not pool:
                    break  # 没有任何满足最短帧数的镜头,放弃该段而不是越界
                pick = max(pool, key=lambda shot: shot.get("final_score") or 0)
                used.add(pick["id"])
                duration_frames = _pick_duration_frames(pick, target_frames - accumulated or target_frames)
                if duration_frames < MIN_SHOT_FRAMES:
                    continue  # 素材边界不足,换候选
                selected_specs.append(_shot_to_candidate(pick, start_frame, duration_frames, master=False))
                selected_rows.append(pick)
                start_frame += duration_frames
                accumulated += duration_frames
                explanation["selections"].append(
                    {
                        "shot_id": pick["id"],
                        "role": role,
                        "prototype": pick["prototype"],
                        "reason": f"{variant} variant prefers {prototype} for {role}",
                        "alternates": [item["id"] for item in sorted(pool, key=lambda shot: shot.get("final_score") or 0, reverse=True)[1:4]],
                    }
                )
        # 校验实际输出时长:计划总帧数与实际累计的差距,不能静默输出不满足时长要求的蓝图。
        target_total = sum(frame_plan.values())
        missing = target_total - start_frame
        if 0 < missing <= 3 and selected_specs:
            room = _shot_available_frames(selected_rows[-1]) - selected_specs[-1].duration_in_frames
            add = min(missing, max(room, 0))
            if add:
                selected_specs[-1].duration_in_frames += add
                start_frame += add
                missing -= add
        ratio = missing / target_total if target_total else 0.0
        if ratio > 0.10:
            raise ValueError(
                f"{variant} 蓝图实际时长 {start_frame} 帧,计划 {target_total} 帧,缺口 {ratio:.0%} 超过 10%,素材不足以支撑目标时长"
            )
        explanation["duration"] = {
            "target_frames": target_total,
            "actual_frames": start_frame,
            "missing_frames": missing,
            "warning": missing > 0,
        }
        spec = editspec.EditSpec(id=project_id, fps=30, width=width, height=height, duration_in_frames=start_frame, shots=selected_specs)
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
                _variant_score(selected_rows),
                db.json_dumps(explanation),
            ),
        )
        variant_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.commit()
        conn.close()
        result.append({"id": variant_id, "variant_type": variant, "editspec_path": str(editspec_path), "preview_path": rendered_preview, "explanation": explanation})
    return {"project_id": project_id, "variants": result}


def list_variants(project_id: str) -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM cut_variants WHERE project_id=? ORDER BY selected DESC, score DESC, id DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["explanation_json"] = _loads_json(item.get("explanation_json"), {})
        out.append(item)
    return out


def select_variant(project_id: str, variant_id: int) -> dict:
    rows_map = {item["id"]: item for item in _shot_rows(project_id)}
    conn = db.connect()
    # 先查询并验证 Variant,再写入 selected;避免传入错误 ID 时先把现有选中状态清零。
    row = conn.execute("SELECT * FROM cut_variants WHERE id=? AND project_id=?", (variant_id, project_id)).fetchone()
    if not row:
        conn.close()
        raise ValueError("variant not found")
    preview_spec = _loads_json(Path(row["editspec_path"]).read_text(), {})
    fps = int(preview_spec.get("fps") or BLUEPRINT_FPS)
    # 确定 Variant 实际使用(未被拒绝、可解析)的镜头。
    used_pairs: list[tuple[dict, dict]] = []
    for shot in preview_spec.get("shots", []):
        selected_row = rows_map.get(shot["id"])
        if not selected_row or selected_row.get("decision") == "reject":
            continue
        used_pairs.append((shot, selected_row))

    # 第一阶段:只计算 Final 镜头,不写数据库。
    # Trim 决定素材边界,Blueprint 决定实际使用时长——用户放宽 Trim 不应把镜头撑到整段源。
    final_shots: list[dict] = []
    cursor = 0
    for shot, selected_row in used_pairs:
        planned_frames = int(shot.get("duration_in_frames") or 0)
        source_in = selected_row.get("trim_start_sec") if selected_row.get("trim_start_sec") is not None else selected_row["start_sec"]
        source_out = selected_row.get("trim_end_sec") if selected_row.get("trim_end_sec") is not None else selected_row["end_sec"]
        available_frames = max(round((source_out - source_in) * fps), 0)
        duration_frames = min(planned_frames, available_frames)
        if duration_frames < 1:
            continue  # Trim 后无有效时长,跳过
        # 跳过被拒/无效镜头后按顺序重排时间线,避免黑场空洞。
        final_shots.append({
            **shot,
            "src": selected_row["asset_path"],
            "source_in_sec": source_in,
            "start_frame": cursor,
            "duration_in_frames": duration_frames,
        })
        cursor += duration_frames

    # Blueprint 阶段已校验素材足够,但审片后 reject/缩短 Trim 可能让 Final 明显变短,需重检。
    planned_total = int(preview_spec.get("duration_in_frames", 0))
    final_total = cursor
    missing_frames = max(planned_total - final_total, 0)
    missing_ratio = missing_frames / planned_total if planned_total else 0.0
    if missing_ratio > 0.10:
        conn.close()
        raise ValueError(
            f"审片修改后 Final 时长不足:计划 {planned_total} 帧,实际 {final_total} 帧,"
            f"缺口 {missing_ratio:.0%}。请重新生成 Blueprint。"
        )
    warnings = []
    if missing_frames > 3:
        warnings.append(f"Final 比 Blueprint 少 {missing_frames} 帧")

    final_spec = {**preview_spec, "shots": final_shots, "duration_in_frames": final_total}
    final_path = config.PROJECTS / project_id / f"editspec.final.variant-{variant_id}.json"

    # 第二阶段:校验通过后,在一个事务里统一写库并落盘 Final。
    try:
        conn.execute("BEGIN")
        conn.execute("UPDATE cut_variants SET selected=CASE WHEN id=? THEN 1 ELSE 0 END WHERE project_id=?", (variant_id, project_id))
        for shot in final_shots:
            shot_id = shot["id"]
            existing = conn.execute(
                "SELECT decision FROM review_decisions WHERE project_id=? AND shot_id=?",
                (project_id, shot_id),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO review_decisions (project_id, shot_id, decision, reasons, preferred_role, updated_at)
                VALUES (?, ?, 'use', '["variant_selected"]', ?, datetime('now'))
                ON CONFLICT(project_id, shot_id) DO UPDATE SET
                    decision='use',
                    reasons='["variant_selected"]',
                    updated_at=datetime('now')
                """,
                (project_id, shot_id, None),
            )
            if not existing or existing["decision"] != "use":
                conn.execute("UPDATE shots SET picked=COALESCE(picked, 0) + 1 WHERE id=?", (shot_id,))
        final_path.write_text(json.dumps(final_spec, ensure_ascii=False, indent=2))
        conn.execute("UPDATE cut_variants SET final_editspec_path=? WHERE id=?", (str(final_path), variant_id))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    train_preference()
    return {
        "project_id": project_id,
        "variant_id": variant_id,
        "selected": True,
        "shots": len(final_shots),
        "duration_in_frames": final_total,
        "planned_duration_in_frames": planned_total,
        "warnings": warnings,
        "final_editspec_path": str(final_path),
    }


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
        [[float(row["sharpness"] or 0) / 500.0, float(row["motion_mag"] or 0) / 3.0, float(row["brightness"] or 0), abs(float(row["reframe_x"] or 0))] for row in rows],
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


def reset_preference() -> dict:
    conn = db.connect()
    deleted = conn.execute("DELETE FROM preference_models WHERE scope='global'").rowcount
    conn.commit()
    conn.close()
    return {"deleted": deleted}


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

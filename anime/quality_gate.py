"""Director-level structural audit and per-shot enhancement A/B gate."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from . import config, db


def _base_shot_id(value: str) -> str:
    return value.split("@hook-")[0].split("@")[0]


def _frame(path: str, second: float) -> np.ndarray | None:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_MSEC, max(second, 0) * 1000)
    ok, image = cap.read()
    cap.release()
    return image if ok else None


def _dhash(image: np.ndarray) -> int:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def audit(editspec_path: str, *, visual: bool = False) -> dict:
    path = Path(editspec_path).resolve()
    spec = json.loads(path.read_text())
    fps = int(spec.get("fps") or 0)
    shots = spec.get("shots") or []
    duration = int(spec.get("duration_in_frames") or 0) / fps if fps else 0
    sources = {str(Path(shot["src"]).resolve()) for shot in shots}
    hard: list[dict] = []
    warnings: list[dict] = []
    if (spec.get("width"), spec.get("height")) != (3072, 3840):
        hard.append({"code": "delivery_canvas", "detail": "必须为 3072x3840"})
    if fps != 60:
        hard.append({"code": "delivery_fps", "detail": "必须为 60fps"})
    if duration < 20:
        hard.append({"code": "delivery_duration", "detail": f"{duration:.2f}s < 20s"})
    if len(sources) < 2:
        hard.append({"code": "source_diversity", "detail": "少于两个独立源"})
    if not shots:
        hard.append({"code": "empty_timeline", "detail": "没有镜头"})

    conn = db.connect()
    rows = {}
    project_id = path.parent.name
    for shot in shots:
        base_id = _base_shot_id(shot["id"])
        row = conn.execute(
            """
            SELECT s.*,sc.subtitle_risk,sc.watermark_risk,
                   rv.decision AS review_decision,rv.reasons AS review_reasons
            FROM shots s
            LEFT JOIN shot_scores sc ON sc.shot_id=s.id
            LEFT JOIN review_decisions rv ON rv.shot_id=s.id AND rv.project_id=?
            WHERE s.id=?
            """, (project_id, base_id),
        ).fetchone()
        if row:
            rows[shot["id"]] = dict(row)
    conn.close()

    timeline_end = 0
    seen: dict[str, int] = {}
    fingerprints: list[tuple[str, int]] = []
    continuity_scores = []
    previous = None
    for index, shot in enumerate(shots):
        start, length = int(shot["start_frame"]), int(shot["duration_in_frames"])
        if start > timeline_end:
            hard.append({"code": "timeline_gap", "shot_id": shot["id"],
                         "detail": f"expected {timeline_end}, got {start}"})
        elif start < timeline_end:
            overlap = timeline_end - start
            transition = str(shot.get("transition") or "none")
            if overlap > 1 or transition == "none":
                warnings.append({"code": "timeline_overlap", "shot_id": shot["id"],
                                 "frames": overlap, "transition": transition})
        timeline_end = max(timeline_end, start + length)
        if length <= 0:
            hard.append({"code": "invalid_duration", "shot_id": shot["id"]})
        seen[shot["id"]] = seen.get(shot["id"], 0) + 1
        meta = rows.get(shot["id"])
        if meta:
            required = (length / fps) * float(shot.get("speed") or 1)
            runway = float(meta["end_sec"]) - float(shot["source_in_sec"])
            if runway + .02 < required:
                hard.append({"code": "source_runway", "shot_id": shot["id"],
                             "detail": f"needs {required:.3f}s, has {runway:.3f}s"})
            review_reasons = set(json.loads(meta.get("review_reasons") or "[]"))
            if ("burned_subtitle" in review_reasons
                    and meta.get("review_decision") != "reject"):
                hard.append({"code": "confirmed_burned_subtitle",
                             "shot_id": shot["id"]})
            elif float(meta.get("subtitle_risk") or 0) >= .5:
                warnings.append({"code": "subtitle_review", "shot_id": shot["id"]})
            if float(meta.get("watermark_risk") or 0) >= .7:
                warnings.append({"code": "watermark_review", "shot_id": shot["id"]})
        if previous:
            prev_meta, cur_meta = rows.get(previous["id"]), meta
            score = .5
            if prev_meta and cur_meta:
                same_direction = (prev_meta.get("motion_dir")
                                  and prev_meta.get("motion_dir") == cur_meta.get("motion_dir"))
                score += .25 if same_direction else 0
                prev_tags = set((prev_meta.get("tags") or "").split(","))
                cur_tags = set((cur_meta.get("tags") or "").split(","))
                score += .25 if prev_tags & cur_tags else 0
            continuity_scores.append(score)
        previous = shot
        if visual:
            image = _frame(shot["src"], float(shot.get("source_in_sec") or 0)
                           + (length / fps) * float(shot.get("speed") or 1) / 2)
            if image is not None:
                fingerprint = _dhash(image)
                for prior_id, prior in fingerprints:
                    if _hamming(fingerprint, prior) <= 5:
                        warnings.append({"code": "visual_near_duplicate",
                                         "shot_id": shot["id"], "similar_to": prior_id})
                        break
                fingerprints.append((shot["id"], fingerprint))
    for shot_id, count in seen.items():
        if count > 1:
            warnings.append({"code": "exact_shot_repeat", "shot_id": shot_id,
                             "count": count})
    if timeline_end != int(spec.get("duration_in_frames") or 0):
        hard.append({"code": "timeline_total", "detail": "镜头总长与成片总长不一致"})
    continuity = (sum(continuity_scores) / len(continuity_scores)
                  if continuity_scores else 1.0)
    if continuity < .58:
        warnings.append({"code": "causal_continuity_low",
                         "detail": round(continuity, 4)})
    report = {
        "editspec": str(path), "duration_sec": round(duration, 3),
        "source_count": len(sources), "shot_count": len(shots),
        "continuity_score": round(continuity, 4),
        "visual_fingerprints": len(fingerprints),
        "hard_failures": hard, "warnings": warnings, "pass": not hard,
    }
    out = path.with_name(path.stem + ".quality-report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    report["report"] = str(out)
    return report


def _sample_metrics(source: str, processed: str, samples: int = 5,
                    source_start: float = 0.0, duration: float | None = None) -> dict:
    src_cap, out_cap = cv2.VideoCapture(source), cv2.VideoCapture(processed)
    if not src_cap.isOpened() or not out_cap.isOpened():
        src_cap.release()
        out_cap.release()
        raise ValueError("无法打开 source/processed 视频")
    src_fps = float(src_cap.get(cv2.CAP_PROP_FPS) or 1)
    out_fps = float(out_cap.get(cv2.CAP_PROP_FPS) or 1)
    src_frames = int(src_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out_frames = int(out_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    window = duration or (out_frames / out_fps)
    distances, edge_ratios, flicker = [], [], []
    previous_delta = None
    for ratio in np.linspace(.1, .9, samples):
        src_index = round((source_start + window * ratio) * src_fps)
        src_cap.set(cv2.CAP_PROP_POS_FRAMES, min(max(src_index, 0), max(src_frames - 1, 0)))
        out_cap.set(cv2.CAP_PROP_POS_FRAMES, max(round((out_frames - 1) * ratio), 0))
        ok_a, a = src_cap.read()
        ok_b, b = out_cap.read()
        if not ok_a or not ok_b:
            continue
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
        ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
        distances.append(float(np.mean(cv2.absdiff(ga, gb))) / 255)
        edge_a = float(cv2.Laplacian(ga, cv2.CV_64F).var())
        edge_b = float(cv2.Laplacian(gb, cv2.CV_64F).var())
        edge_ratios.append(edge_b / max(edge_a, 1e-6))
        delta = float(np.mean(gb) - np.mean(ga))
        if previous_delta is not None:
            flicker.append(abs(delta - previous_delta) / 255)
        previous_delta = delta
    src_cap.release()
    out_cap.release()
    if not distances:
        raise ValueError("没有可比较帧")
    return {
        "mean_pixel_change": round(float(np.mean(distances)), 6),
        "max_pixel_change": round(float(np.max(distances)), 6),
        "edge_ratio": round(float(np.mean(edge_ratios)), 6),
        "temporal_delta_jitter": round(float(np.mean(flicker)) if flicker else 0, 6),
        "samples": len(distances),
    }


def compare(project_id: str, shot_id: str, stage: str,
            source_path: str, processed_path: str, *,
            source_start: float = 0.0, duration: float | None = None) -> dict:
    allowed = {"restore", "rife", "superres", "matte", "other"}
    if stage not in allowed:
        raise ValueError(f"stage 必须是 {sorted(allowed)}")
    source, processed = str(Path(source_path).resolve()), str(Path(processed_path).resolve())
    metrics = _sample_metrics(source, processed, source_start=source_start,
                              duration=duration)
    if (metrics["mean_pixel_change"] > .24 or metrics["temporal_delta_jitter"] > .08
            or metrics["edge_ratio"] > 3.5 or metrics["edge_ratio"] < .35):
        recommendation = "reject"
    elif (metrics["mean_pixel_change"] > .12 or metrics["temporal_delta_jitter"] > .035
          or metrics["edge_ratio"] > 2.2):
        recommendation = "review"
    else:
        recommendation = "accept"
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO enhancement_reviews
            (project_id,shot_id,stage,source_path,processed_path,metrics_json,recommendation)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(project_id,shot_id,stage,processed_path) DO UPDATE SET
            source_path=excluded.source_path,metrics_json=excluded.metrics_json,
            recommendation=excluded.recommendation,updated_at=datetime('now')
        """, (project_id, shot_id, stage, source, processed,
              json.dumps(metrics), recommendation),
    )
    review_id = conn.execute(
        "SELECT id FROM enhancement_reviews WHERE project_id=? AND shot_id=? AND stage=? AND processed_path=?",
        (project_id, shot_id, stage, processed),
    ).fetchone()["id"]
    conn.commit()
    conn.close()
    return {"review_id": review_id, "project_id": project_id, "shot_id": shot_id,
            "stage": stage, "metrics": metrics, "recommendation": recommendation,
            "decision": None}


def decide(review_id: int, decision: str, notes: str | None = None) -> dict:
    if decision not in {"accept", "reject"}:
        raise ValueError("decision 必须是 accept/reject")
    conn = db.connect()
    changed = conn.execute(
        """
        UPDATE enhancement_reviews SET decision=?,notes=?,updated_at=datetime('now')
        WHERE id=?
        """, (decision, notes, review_id),
    ).rowcount
    if not changed:
        conn.close()
        raise ValueError("enhancement review not found")
    conn.commit()
    row = dict(conn.execute("SELECT * FROM enhancement_reviews WHERE id=?",
                            (review_id,)).fetchone())
    conn.close()
    row["metrics"] = json.loads(row.pop("metrics_json"))
    return row


def status(project_id: str) -> dict:
    conn = db.connect()
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM enhancement_reviews WHERE project_id=? ORDER BY id",
        (project_id,),
    ).fetchall()]
    conn.close()
    for row in rows:
        row["metrics"] = json.loads(row.pop("metrics_json"))
    pending = [row for row in rows if row["decision"] is None]
    rejected = [row for row in rows if row["decision"] == "reject"]
    return {"project_id": project_id, "items": rows, "pending": len(pending),
            "rejected": len(rejected), "pass": bool(rows) and not pending and not rejected}

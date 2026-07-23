"""Controlled multi-factor publication experiments and shot-level attribution."""
from __future__ import annotations

import json
import math
import csv
from pathlib import Path

from . import config, db


def _rate(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not 0 <= value <= 1:
        raise ValueError(f"{name} 必须在 0..1 之间")
    return value


def _validate_curve(points: list[dict] | None) -> list[dict]:
    if not points:
        return []
    clean = []
    previous_second = -1.0
    previous_rate = 1.0
    for point in points:
        second = float(point["second"])
        rate = _rate(point["rate"], "retention curve rate")
        if second < 0 or second <= previous_second:
            raise ValueError("留存曲线时间必须非负且严格递增")
        if rate is None or rate > previous_rate + 0.02:
            raise ValueError("留存曲线必须近似单调不增（最多允许 0.02 回弹）")
        clean.append({"second": round(second, 3), "rate": round(rate, 6)})
        previous_second, previous_rate = second, rate
    return clean


def _curve_at(points: list[dict], second: float) -> float | None:
    if not points:
        return None
    if second <= points[0]["second"]:
        return points[0]["rate"]
    for left, right in zip(points, points[1:]):
        if left["second"] <= second <= right["second"]:
            span = right["second"] - left["second"]
            ratio = (second - left["second"]) / span if span else 0
            return left["rate"] + (right["rate"] - left["rate"]) * ratio
    return points[-1]["rate"]


def _variant_payload(base: dict, factor: dict, label: str, overlay_frames: int) -> dict:
    variant = json.loads(json.dumps(base))
    variant["id"] = f"{base.get('id', 'edit')}-experiment-{label.lower()}"
    hook = str(factor.get("hook") or "").strip()
    sub = str(factor.get("sub") or "").strip()
    if hook:
        variant["overlays"] = [{
            "text": hook, "sub": sub, "start_frame": 0,
            "duration_in_frames": overlay_frames,
            "style": str(factor.get("style") or "hook"),
            "anchor": str(factor.get("anchor") or "center"),
        }]
    hook_shot_id = factor.get("hook_shot_id")
    if hook_shot_id:
        shots = variant.get("shots") or []
        source = next((shot for shot in shots if shot["id"] == hook_shot_id), None)
        if not source:
            raise ValueError(f"hook_shot_id 不在 EditSpec: {hook_shot_id}")
        if not shots:
            raise ValueError("EditSpec 没有镜头")
        first_duration = int(shots[0]["duration_in_frames"])
        if int(source["duration_in_frames"]) < first_duration:
            raise ValueError(f"Hook 候选 {hook_shot_id} runway 不足 {first_duration} 帧")
        keep = {key: shots[0].get(key) for key in ("start_frame", "duration_in_frames")}
        shots[0] = {**source, **keep, "id": f"{source['id']}@hook-{label.lower()}"}
    audio_offset = int(factor.get("audio_offset_frames") or 0)
    if audio_offset:
        for layer in variant.get("audio") or []:
            next_trim = int(layer.get("trim_start_frames") or 0) + audio_offset
            if next_trim < 0:
                raise ValueError("audio_offset_frames 使音频入点越过 0")
            layer["trim_start_frames"] = next_trim
    return variant


def create_matrix(project_id: str, name: str, spec_path: str, factors: list[dict],
                  *, platform: str = "douyin", duration_sec: float = 2.8) -> dict:
    if len(factors) < 2:
        raise ValueError("实验至少需要两个变体")
    source = Path(spec_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    payload = json.loads(source.read_text())
    fps = int(payload.get("fps") or 60)
    total = int(payload.get("duration_in_frames") or 0)
    overlay_frames = min(max(round(duration_sec * fps), 1), total)
    if overlay_frames <= 0:
        raise ValueError("EditSpec 时长无效")
    project_dir = config.PROJECTS / project_id / "experiments" / name
    project_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    variants = []
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO growth_experiments(project_id,name,base_spec_path,platform)
            VALUES (?,?,?,?)
            ON CONFLICT(project_id,name) DO UPDATE SET
                base_spec_path=excluded.base_spec_path,platform=excluded.platform,status='draft'
            """, (project_id, name, str(source), platform),
        )
        experiment_id = conn.execute(
            "SELECT id FROM growth_experiments WHERE project_id=? AND name=?",
            (project_id, name),
        ).fetchone()["id"]
        conn.execute("DELETE FROM growth_variants WHERE experiment_id=?", (experiment_id,))
        for index, factor in enumerate(factors):
            label = str(factor.get("label") or chr(ord("A") + index)).strip().upper()
            if not label:
                raise ValueError("variant label 不能为空")
            variant = _variant_payload(payload, factor, label, overlay_frames)
            path = project_dir / f"variant-{label.lower()}.json"
            path.write_text(json.dumps(variant, ensure_ascii=False, indent=2))
            conn.execute(
                """
                INSERT INTO growth_variants
                    (experiment_id,label,hook_text,hook_sub,editspec_path,factors_json)
                VALUES (?,?,?,?,?,?)
                """,
                (experiment_id, label, str(factor.get("hook") or ""),
                 str(factor.get("sub") or ""), str(path),
                 json.dumps(factor, ensure_ascii=False)),
            )
            variant_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            variants.append({"id": variant_id, "label": label,
                             "hook": str(factor.get("hook") or ""),
                             "sub": str(factor.get("sub") or ""),
                             "factors": factor, "editspec_path": str(path)})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"experiment_id": experiment_id, "project_id": project_id,
            "name": name, "platform": platform, "variants": variants}


def create(project_id: str, name: str, spec_path: str, hooks: list[str],
           *, subs: list[str] | None = None, platform: str = "douyin",
           duration_sec: float = 2.8) -> dict:
    hooks = [item.strip() for item in hooks if item.strip()]
    if len(set(hooks)) != len(hooks):
        raise ValueError("hook 文案必须互不相同")
    subs = subs or []
    factors = [
        {"label": chr(ord("A") + i), "hook": hook,
         "sub": subs[i].strip() if i < len(subs) else ""}
        for i, hook in enumerate(hooks)
    ]
    return create_matrix(project_id, name, spec_path, factors,
                         platform=platform, duration_sec=duration_sec)


def _attribute(conn, variant_id: int, curve: list[dict]) -> list[dict]:
    row = conn.execute(
        "SELECT editspec_path FROM growth_variants WHERE id=?", (variant_id,)
    ).fetchone()
    spec = json.loads(Path(row["editspec_path"]).read_text())
    fps = float(spec.get("fps") or 60)
    conn.execute("DELETE FROM shot_outcomes WHERE variant_id=?", (variant_id,))
    outcomes = []
    for shot in spec.get("shots") or []:
        start = float(shot["start_frame"]) / fps
        end = (float(shot["start_frame"]) + float(shot["duration_in_frames"])) / fps
        retention_in = _curve_at(curve, start)
        retention_out = _curve_at(curve, end)
        drop = ((retention_in - retention_out)
                if retention_in is not None and retention_out is not None else None)
        base_id = str(shot["id"]).split("@hook-")[0].split("@")[0]
        conn.execute(
            """
            INSERT INTO shot_outcomes
                (variant_id,shot_id,start_sec,end_sec,retention_in,retention_out,retention_drop)
            VALUES (?,?,?,?,?,?,?)
            """, (variant_id, base_id, start, end, retention_in, retention_out, drop),
        )
        outcomes.append({"shot_id": base_id, "start_sec": start, "end_sec": end,
                         "retention_in": retention_in, "retention_out": retention_out,
                         "retention_drop": drop})
    return outcomes


def record(variant_id: int, *, views: int, likes: int = 0, comments: int = 0,
           shares: int = 0, follows: int = 0, retention_2s: float | None = None,
           retention_3s: float | None = None, completion_rate: float | None = None,
           avg_watch_sec: float | None = None, published_at: str | None = None,
           retention_curve: list[dict] | None = None,
           external_post_id: str | None = None) -> dict:
    counts = {"views": views, "likes": likes, "comments": comments,
              "shares": shares, "follows": follows}
    if any(int(value) < 0 for value in counts.values()):
        raise ValueError("播放与互动计数不能为负数")
    if any(int(value) > int(views) for key, value in counts.items() if key != "views"):
        raise ValueError("互动计数不能大于播放数")
    r2, r3 = _rate(retention_2s, "retention_2s"), _rate(retention_3s, "retention_3s")
    completion = _rate(completion_rate, "completion_rate")
    curve = _validate_curve(retention_curve)
    if avg_watch_sec is not None and float(avg_watch_sec) < 0:
        raise ValueError("avg_watch_sec 不能为负数")
    conn = db.connect()
    try:
        cur = conn.execute(
            """
            UPDATE growth_variants SET
                views=?,likes=?,comments=?,shares=?,follows=?,
                retention_2s=?,retention_3s=?,completion_rate=?,avg_watch_sec=?,
                published_at=COALESCE(?,published_at),
                retention_curve_json=?,external_post_id=COALESCE(?,external_post_id),
                updated_at=datetime('now')
            WHERE id=?
            """,
            (int(views), int(likes), int(comments), int(shares), int(follows),
             r2, r3, completion, avg_watch_sec, published_at,
             json.dumps(curve), external_post_id, variant_id),
        )
        if not cur.rowcount:
            raise ValueError("experiment variant not found")
        experiment_id = conn.execute(
            "SELECT experiment_id FROM growth_variants WHERE id=?", (variant_id,)
        ).fetchone()["experiment_id"]
        outcomes = _attribute(conn, variant_id, curve) if curve else []
        conn.execute("UPDATE growth_experiments SET status='running' WHERE id=?",
                     (experiment_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"variant_id": variant_id, **counts, "retention_2s": r2,
            "retention_3s": r3, "completion_rate": completion,
            "avg_watch_sec": avg_watch_sec, "shot_outcomes": outcomes}


def _score(row: dict) -> float:
    views = max(int(row["views"]), 0)
    if not views:
        return 0.0
    r2, r3 = row["retention_2s"] or 0.0, row["retention_3s"] or 0.0
    completion = row["completion_rate"] or 0.0
    engagement = (
        row["likes"] + row["comments"] * 2 + row["shares"] * 4 + row["follows"] * 5
    ) / views
    raw = r2 * .25 + r3 * .30 + completion * .30 + min(engagement, .25) * .60
    return round(raw * min(math.sqrt(views / 5000), 1.0), 6)


def report(project_id: str, name: str) -> dict:
    conn = db.connect()
    experiment = conn.execute(
        "SELECT * FROM growth_experiments WHERE project_id=? AND name=?",
        (project_id, name),
    ).fetchone()
    if not experiment:
        conn.close()
        raise ValueError("experiment not found")
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM growth_variants WHERE experiment_id=? ORDER BY label",
        (experiment["id"],)).fetchall()]
    for row in rows:
        row["factors"] = json.loads(row.get("factors_json") or "{}")
        row["retention_curve"] = json.loads(row.get("retention_curve_json") or "[]")
        row["score"] = _score(row)
        row["sample_confidence"] = round(min(math.sqrt(row["views"] / 5000), 1.0), 4)
        row["shot_outcomes"] = [dict(item) for item in conn.execute(
            "SELECT * FROM shot_outcomes WHERE variant_id=? ORDER BY start_sec",
            (row["id"],)).fetchall()]
    conn.close()
    ranked = sorted(rows, key=lambda item: item["score"], reverse=True)
    winner = ranked[0] if ranked and ranked[0]["views"] else None
    decisive = bool(winner and winner["views"] >= 1000 and len(ranked) > 1
                    and winner["score"] >= ranked[1]["score"] * 1.08)
    return {"experiment": dict(experiment), "ranked_variants": ranked,
            "winner": winner["label"] if decisive else None,
            "decision": "winner" if decisive else "collect_more_data"}


def list_project(project_id: str) -> list[dict]:
    conn = db.connect()
    experiments = [dict(row) for row in conn.execute(
        "SELECT * FROM growth_experiments WHERE project_id=? ORDER BY id DESC",
        (project_id,),
    ).fetchall()]
    conn.close()
    return [report(project_id, item["name"]) for item in experiments]


def import_metrics(project_id: str, name: str, csv_path: str) -> dict:
    """Import a normalized platform export without requiring account credentials.

    Required columns: label, views. Optional metric names match ``record``.
    retention_curve_json may contain an inline JSON array.
    """
    conn = db.connect()
    experiment = conn.execute(
        "SELECT id FROM growth_experiments WHERE project_id=? AND name=?",
        (project_id, name),
    ).fetchone()
    if not experiment:
        conn.close()
        raise ValueError("experiment not found")
    variants = {
        row["label"]: row["id"] for row in conn.execute(
            "SELECT id,label FROM growth_variants WHERE experiment_id=?",
            (experiment["id"],),
        ).fetchall()
    }
    conn.close()
    imported, errors = [], []
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            label = str(row.get("label") or "").strip().upper()
            variant_id = variants.get(label)
            if not variant_id:
                errors.append({"line": line, "error": f"unknown label {label}"})
                continue
            try:
                optional_float = lambda key: (float(row[key]) if row.get(key) not in (None, "") else None)
                curve = json.loads(row["retention_curve_json"]) if row.get("retention_curve_json") else None
                imported.append(record(
                    variant_id, views=int(row["views"]),
                    likes=int(row.get("likes") or 0),
                    comments=int(row.get("comments") or 0),
                    shares=int(row.get("shares") or 0),
                    follows=int(row.get("follows") or 0),
                    retention_2s=optional_float("retention_2s"),
                    retention_3s=optional_float("retention_3s"),
                    completion_rate=optional_float("completion_rate"),
                    avg_watch_sec=optional_float("avg_watch_sec"),
                    published_at=row.get("published_at") or None,
                    retention_curve=curve,
                    external_post_id=row.get("external_post_id") or None,
                ))
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                errors.append({"line": line, "error": str(exc)})
    return {"project_id": project_id, "name": name, "imported": len(imported),
            "errors": errors, "items": imported}


def insights(project_id: str | None = None) -> dict:
    conn = db.connect()
    project_filter = "AND ge.project_id=?" if project_id else ""
    params = (project_id,) if project_id else ()
    rows = [dict(row) for row in conn.execute(
        f"""
        SELECT gv.*,ge.project_id,ge.platform FROM growth_variants gv
        JOIN growth_experiments ge ON ge.id=gv.experiment_id
        WHERE gv.views>=1000 {project_filter}
        """, params,
    ).fetchall()]
    conn.close()
    factors: dict[str, dict[str, list[tuple[float, int]]]] = {}
    for row in rows:
        for key, value in json.loads(row.get("factors_json") or "{}").items():
            if key == "label":
                continue
            factors.setdefault(key, {}).setdefault(str(value), []).append(
                (_score(row), int(row["views"])))
    summaries = {}
    for key, values in factors.items():
        summaries[key] = []
        for value, observations in values.items():
            total_views = sum(item[1] for item in observations)
            weighted = (sum(score * views for score, views in observations) / total_views
                        if total_views else 0)
            summaries[key].append({
                "value": value, "weighted_score": round(weighted, 6),
                "views": total_views, "variants": len(observations),
            })
        summaries[key].sort(key=lambda item: item["weighted_score"], reverse=True)
    return {"project_id": project_id, "sampled_variants": len(rows),
            "factors": summaries,
            "status": "ready" if rows else "insufficient_data"}


def learn(project_id: str | None = None) -> dict:
    """Aggregate comparable, adequately sampled outcomes into shot growth scores."""
    conn = db.connect()
    project_filter = "AND ge.project_id=?" if project_id else ""
    params = (project_id,) if project_id else ()
    rows = conn.execute(
        f"""
        SELECT so.shot_id,so.retention_drop,gv.views,gv.completion_rate
        FROM shot_outcomes so
        JOIN growth_variants gv ON gv.id=so.variant_id
        JOIN growth_experiments ge ON ge.id=gv.experiment_id
        WHERE gv.views>=1000 AND so.retention_drop IS NOT NULL {project_filter}
        """, params,
    ).fetchall()
    grouped: dict[str, list[float]] = {}
    for row in rows:
        # Low drop and high completion are positive. Keep the learned signal bounded.
        value = max(-1.0, min(1.0, (0.08 - row["retention_drop"]) * 5
                                  + ((row["completion_rate"] or 0) - .5) * .5))
        grouped.setdefault(row["shot_id"], []).append(value)
    updated = 0
    for shot_id, values in grouped.items():
        score = sum(values) / len(values)
        updated += conn.execute(
            "UPDATE shots SET growth_score=? WHERE id=?", (round(score, 6), shot_id)
        ).rowcount
    conn.commit()
    conn.close()
    return {"project_id": project_id, "observations": len(rows),
            "shots_updated": updated,
            "status": "learned" if rows else "insufficient_data"}

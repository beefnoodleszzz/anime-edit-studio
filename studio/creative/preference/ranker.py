"""Small deterministic Bradley-Terry ranker over auditable Shot features."""
from __future__ import annotations

import json
import sqlite3

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

PREFERENCE_MODEL_VERSION = "pairwise-bt-1.0.0"
FEATURES = (
    "image_quality",
    "pose_quality",
    "face_visibility",
    "visual_energy",
    "shot_scale",
    "subject_motion",
    "cutability",
    "subtitle_clean",
    "aesthetic",
)


class PreferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = PREFERENCE_MODEL_VERSION
    scope: str
    features: list[str] = Field(default_factory=lambda: list(FEATURES))
    weights: list[float]
    bias: float
    trained_on: int
    fitted: bool


class PreferenceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str
    model_version: str | None
    fitted: bool
    trained_on: int
    signals: dict[str, float]
    feedback_counts: dict[str, int]
    interpretation: str = "signal_only"


def _vector(row: sqlite3.Row) -> np.ndarray:
    try:
        subtitle = json.loads(row["subtitle_region"] or "{}")
    except json.JSONDecodeError:
        subtitle = {}
    values = [
        row["image_quality"],
        row["pose_quality"],
        row["face_visibility"],
        row["visual_energy"],
        row["shot_scale"],
        row["subject_motion"],
        row["cutability"],
        0.0 if subtitle.get("present") else 1.0,
        (row["aesthetic"] / 10) if row["aesthetic"] is not None else 0.5,
    ]
    return np.asarray([0.5 if value is None else float(value) for value in values])


def train_pairwise(
    conn: sqlite3.Connection,
    *,
    scope: str = "global",
    project_id: str | None = None,
    min_pairs: int = 5,
    learning_rate: float = 0.08,
    epochs: int = 400,
    l2: float = 0.02,
) -> PreferenceModel:
    conn.row_factory = sqlite3.Row
    sql = "SELECT winner_shot_id,loser_shot_id FROM preference_pairs"
    params: list[object] = []
    if project_id:
        sql += " WHERE project_id=?"
        params.append(project_id)
    pairs = conn.execute(sql, params).fetchall()
    ids = sorted({value for pair in pairs for value in pair})
    rows = (
        conn.execute(
            f"SELECT * FROM shots WHERE id IN ({','.join('?' for _ in ids)})", ids
        ).fetchall()
        if ids else []
    )
    by_id = {row["id"]: row for row in rows}
    differences = [
        _vector(by_id[pair["winner_shot_id"]])
        - _vector(by_id[pair["loser_shot_id"]])
        for pair in pairs
        if pair["winner_shot_id"] in by_id and pair["loser_shot_id"] in by_id
    ]
    weights = np.zeros(len(FEATURES), np.float64)
    bias = 0.0
    fitted = len(differences) >= min_pairs
    if fitted:
        matrix = np.stack(differences)
        for _ in range(epochs):
            logits = np.clip(matrix @ weights + bias, -20, 20)
            probabilities = 1 / (1 + np.exp(-logits))
            error = probabilities - 1
            gradient = matrix.T @ error / len(matrix) + l2 * weights
            weights -= learning_rate * gradient
            bias -= learning_rate * float(np.mean(error))
    model = PreferenceModel(
        scope=scope,
        weights=weights.tolist(),
        bias=bias,
        trained_on=len(differences),
        fitted=fitted,
    )
    with conn:
        conn.execute(
            """
            INSERT INTO preference_models(
              scope,model_type,version,features_json,model_json,trained_on,
              created_at,updated_at
            ) VALUES (?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),
              strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(scope) DO UPDATE SET
              model_type=excluded.model_type,version=excluded.version,
              features_json=excluded.features_json,model_json=excluded.model_json,
              trained_on=excluded.trained_on,updated_at=excluded.updated_at
            """,
            (
                scope,
                "bradley_terry_logistic",
                model.version,
                json.dumps(model.features),
                model.model_dump_json(),
                model.trained_on,
            ),
        )
    return model


def preference_signal(model: PreferenceModel, row: sqlite3.Row) -> float:
    if not model.fitted:
        return 0.5
    logit = float(_vector(row) @ np.asarray(model.weights) + model.bias)
    return float(1 / (1 + np.exp(-np.clip(logit, -20, 20))))


def preference_profile(
    conn: sqlite3.Connection,
    *,
    scope: str = "global",
    project_id: str | None = None,
) -> PreferenceProfile:
    row = conn.execute(
        "SELECT model_json FROM preference_models WHERE scope=?", (scope,)
    ).fetchone()
    model = PreferenceModel.model_validate_json(row[0]) if row else None
    params: list[object] = []
    sql = "SELECT kind,count(*) FROM feedback_events"
    if project_id:
        sql += " WHERE project_id=?"
        params.append(project_id)
    sql += " GROUP BY kind"
    counts = {item[0]: int(item[1]) for item in conn.execute(sql, params)}
    weights = (
        dict(zip(model.features, model.weights, strict=True))
        if model is not None else {feature: 0.0 for feature in FEATURES}
    )
    maximum = max((abs(value) for value in weights.values()), default=0.0)
    signals = {
        name: (value / maximum if maximum > 1e-9 else 0.0)
        for name, value in weights.items()
    }
    return PreferenceProfile(
        scope=scope,
        model_version=model.version if model else None,
        fitted=bool(model and model.fitted),
        trained_on=model.trained_on if model else 0,
        signals=signals,
        feedback_counts=counts,
    )


__all__ = [
    "PREFERENCE_MODEL_VERSION",
    "PreferenceModel",
    "PreferenceProfile",
    "preference_profile",
    "preference_signal",
    "train_pairwise",
]

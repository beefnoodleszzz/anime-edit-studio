"""Deterministic visual dimensions for existing and newly ingested shots.

This analyzer makes uncertainty explicit.  Tag-derived pose/face/eye signals
carry lower confidence than future dedicated models; they are never presented
as detector ground truth.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from studio.core.cache import JsonCache
from studio.core.contracts import ConfidenceValue, ShotAnalysis, SubjectBox
from studio.core.hashing import analysis_cache_key, file_sha256

PIPELINE_VERSION = "visual-1.1.0"
MODEL = "opencv+wdtag-derived"
MODEL_VERSION = f"opencv-{cv2.__version__}"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _tags(raw: str | None) -> set[str]:
    return {part.strip().lower() for part in (raw or "").split(",") if part.strip()}


@dataclass(frozen=True)
class VisualResult:
    analysis: ShotAnalysis
    subject_box: SubjectBox

    def to_dict(self) -> dict:
        return {
            "analysis": self.analysis.model_dump(mode="json"),
            "subject_box": self.subject_box.model_dump(mode="json"),
        }


class VisualAnalyzer:
    def __init__(self, cache: JsonCache):
        self.cache = cache

    def analyze(
        self,
        *,
        shot_id: str,
        keyframe: Path,
        tags: str | None,
        motion_mag: float | None,
        duration_sec: float,
        asset_hash: str,
        motion_dir: str | None = None,
    ) -> VisualResult:
        key = analysis_cache_key(
            asset_hash=asset_hash,
            model=MODEL,
            model_version=MODEL_VERSION,
            pipeline_version=PIPELINE_VERSION,
            parameters={
                "shot_id": shot_id,
                "keyframe_sha256": file_sha256(keyframe),
                "tags": tags or "",
                "motion_dir": motion_dir,
                "motion_mag": motion_mag,
                "duration_sec": duration_sec,
            },
        )
        cached = self.cache.get("visual-v2", key)
        if cached:
            return VisualResult(
                ShotAnalysis.model_validate(cached["analysis"]),
                SubjectBox.model_validate(cached["subject_box"]),
            )
        image = cv2.imread(str(keyframe), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"无法读取 keyframe: {keyframe}")
        result = self._compute(
            shot_id, image, _tags(tags), motion_dir, motion_mag or 0.0, duration_sec
        )
        self.cache.put("visual-v2", key, result.to_dict())
        return result

    def _compute(
        self,
        shot_id: str,
        image: np.ndarray,
        tags: set[str],
        motion_dir: str | None,
        motion_mag: float,
        duration_sec: float,
    ) -> VisualResult:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        subject = self._subject_box(gray, hsv)
        area = subject.width * subject.height
        palette = self._palette(image)
        subtitle = self._subtitle_region(gray)
        compression = self._blockiness(gray)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        close_tags = {
            "close-up", "close_up", "portrait", "face", "eye_focus",
            "upper_body", "cowboy_shot",
        }
        face_tags = {"face", "close-up", "close_up", "portrait", "1boy", "1girl", "solo"}
        eye_tags = {"eyes", "eye", "looking_at_viewer", "glowing_eyes", "eyewear"}
        pose_bad = {"bad_anatomy", "cropped", "out_of_frame", "motion_blur"}
        pose_good = {"full_body", "upper_body", "standing", "running", "fighting_stance"}

        tag_scale = (
            0.78 if tags & {"close-up", "close_up", "face", "portrait"}
            else 0.5 if tags & {"upper_body", "cowboy_shot"}
            else 0.25 if tags & {"full_body", "wide_shot", "scenery"}
            else area
        )
        shot_scale = _clamp(0.55 * area + 0.45 * tag_scale)
        face = _clamp(0.25 + 0.55 * bool(tags & face_tags) + 0.2 * shot_scale)
        eyes = _clamp(
            0.1 + 0.55 * bool(tags & eye_tags) + 0.25 * bool(tags & close_tags)
        )
        pose = _clamp(
            0.55 + 0.25 * bool(tags & pose_good) - 0.4 * bool(tags & pose_bad)
        )
        contrast = float(gray.std()) / 64.0
        saturation = float(hsv[..., 1].mean()) / 255.0
        motion = 1.0 - math.exp(-max(0.0, motion_mag) / 4.0)
        energy = _clamp(0.45 * motion + 0.3 * contrast + 0.25 * saturation)
        blur = _clamp(math.exp(-sharpness / 180.0))
        exposure = 1.0 - min(1.0, abs(float(gray.mean()) / 255 - 0.5) * 1.7)
        image_quality = _clamp(
            0.45 * (1.0 - blur) + 0.3 * (1.0 - compression) + 0.25 * exposure
        )
        center_x = subject.x + subject.width / 2
        center_y = subject.y + subject.height / 2
        thirds_distance = min(
            math.hypot(center_x - tx, center_y - ty)
            for tx in (1 / 3, 2 / 3)
            for ty in (1 / 3, 2 / 3)
        )
        composition_score = _clamp(1.0 - thirds_distance / 0.5)
        composition = (
            "rule_of_thirds" if composition_score >= 0.72
            else "centered" if abs(center_x - 0.5) <= 0.12
            else "off_center"
        )
        camera_motion = motion_dir if motion >= 0.18 and motion_dir else "static_or_subject"
        cutability = _clamp(
            0.55 * math.exp(-max(0.0, motion_mag) / 8.0)
            + 0.25 * min(duration_sec / 2.0, 1.0)
            + 0.2 * (1.0 - subtitle["coverage"])
        )

        def cv(value, confidence, method):
            return ConfidenceValue(
                value=value,
                confidence=confidence,
                method=method,
                model_version=MODEL_VERSION,
            )

        analysis = ShotAnalysis(
            shot_id=shot_id,
            analysis_version=PIPELINE_VERSION,
            shot_scale=cv(shot_scale, 0.62, "foreground_box+wd_tags"),
            subject_motion=cv(motion, 0.45, "global_flow_proxy"),
            compression_score=cv(compression, 0.8, "jpeg_block_boundary_ratio"),
            color_palette=cv(palette, 0.9, "quantized_rgb_histogram"),
            subtitle_region=cv(subtitle, 0.68, "text_morphology"),
            pose_quality=cv(pose, 0.4, "wd_tag_heuristic"),
            face_visibility=cv(face, 0.48, "wd_tag+subject_scale"),
            eye_visibility=cv(eyes, 0.4, "wd_tag_heuristic"),
            visual_energy=cv(energy, 0.72, "motion+contrast+saturation"),
            composition=cv(
                composition, min(0.78, subject.confidence), "subject_box_geometry"
            ),
            image_quality=cv(
                image_quality, 0.76, "sharpness+compression+exposure"
            ),
            blur_score=cv(blur, 0.82, "laplacian_calibration"),
            camera_motion=cv(
                camera_motion, 0.35, "global_flow_direction_proxy"
            ),
            cutability=cv(cutability, 0.38, "single_keyframe_proxy"),
        )
        return VisualResult(analysis, subject)

    @staticmethod
    def _subject_box(gray: np.ndarray, hsv: np.ndarray) -> SubjectBox:
        height, width = gray.shape
        edges = cv2.Canny(gray, 60, 160).astype(np.float32) / 255.0
        saturation = hsv[..., 1].astype(np.float32) / 255.0
        yy, xx = np.mgrid[0:height, 0:width]
        center = np.exp(
            -(((xx - width / 2) / (0.55 * width)) ** 2
              + ((yy - height / 2) / (0.65 * height)) ** 2)
        )
        saliency = cv2.GaussianBlur(0.65 * edges + 0.35 * saturation, (0, 0), 7)
        saliency *= center
        threshold = float(np.quantile(saliency, 0.82))
        mask = (saliency >= threshold).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (max(3, width // 40) | 1, max(3, height // 40) | 1)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h / (width * height)
            if area < 0.01:
                continue
            cx, cy = (x + w / 2) / width, (y + h / 2) / height
            score = area * (1.2 - 0.5 * math.hypot(cx - 0.5, cy - 0.48))
            candidates.append((score, x, y, w, h, area))
        if not candidates:
            return SubjectBox(
                x=0.25, y=0.15, width=0.5, height=0.7, confidence=0.15
            )
        _, x, y, w, h, area = max(candidates)
        pad_x, pad_y = int(w * 0.12), int(h * 0.12)
        x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
        x1, y1 = min(width, x + w + pad_x), min(height, y + h + pad_y)
        return SubjectBox(
            x=x0 / width,
            y=y0 / height,
            width=(x1 - x0) / width,
            height=(y1 - y0) / height,
            confidence=_clamp(0.35 + area),
        )

    @staticmethod
    def _palette(image: np.ndarray) -> list[str]:
        small = cv2.resize(image, (64, 64), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        quantized = (rgb // 32).reshape(-1, 3)
        colors, counts = np.unique(quantized, axis=0, return_counts=True)
        top = colors[np.argsort(counts)[-5:][::-1]]
        return [
            "#{:02x}{:02x}{:02x}".format(*(color.astype(int) * 32 + 16))
            for color in top
        ]

    @staticmethod
    def _blockiness(gray: np.ndarray) -> float:
        values = gray.astype(np.float32)
        x_boundaries = np.arange(8, gray.shape[1], 8)
        y_boundaries = np.arange(8, gray.shape[0], 8)
        vertical = (
            np.abs(values[:, x_boundaries] - values[:, x_boundaries - 1]).mean()
            if len(x_boundaries) else 0
        )
        horizontal = (
            np.abs(values[y_boundaries, :] - values[y_boundaries - 1, :]).mean()
            if len(y_boundaries) else 0
        )
        all_vertical = np.abs(np.diff(values, axis=1)).mean() + 1e-6
        all_horizontal = np.abs(np.diff(values, axis=0)).mean() + 1e-6
        ratio = 0.5 * (vertical / all_vertical + horizontal / all_horizontal)
        return _clamp((ratio - 0.8) / 1.8)

    @staticmethod
    def _subtitle_region(gray: np.ndarray) -> dict:
        height, width = gray.shape
        gradient = cv2.morphologyEx(
            gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        )
        _, mask = cv2.threshold(gradient, 70, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, width // 30), 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w / width < 0.08 or h / height > 0.12 or w / max(h, 1) < 2.0:
                continue
            if y < height * 0.45:
                continue
            boxes.append([x / width, y / height, w / width, h / height])
        coverage = sum(box[2] * box[3] for box in boxes)
        return {"present": bool(boxes), "boxes": boxes, "coverage": _clamp(coverage)}


def analyze_pending(
    conn: sqlite3.Connection,
    *,
    cache_root: Path,
    asset_id: str | None = None,
    limit: int | None = None,
) -> dict:
    """Incrementally fill only rows lacking the current analysis version."""
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT s.id,s.keyframe,s.tags,s.motion_dir,s.motion_mag,s.start_sec,s.end_sec,
               a.sha256 AS asset_hash
        FROM shots s JOIN assets a ON a.id=s.asset_id
        WHERE s.keyframe IS NOT NULL
          AND (s.analysis_version IS NULL OR s.analysis_version != ?)
        ORDER BY s.asset_id,s.idx
    """
    params: list[object] = [PIPELINE_VERSION]
    if asset_id:
        sql = sql.replace("ORDER BY", "AND s.asset_id=? ORDER BY")
        params.append(asset_id)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    analyzer = VisualAnalyzer(JsonCache(cache_root))
    done, failed = 0, []
    for row in rows:
        path = Path(row["keyframe"])
        try:
            result = analyzer.analyze(
                shot_id=row["id"],
                keyframe=path,
                tags=row["tags"],
                motion_dir=row["motion_dir"],
                motion_mag=row["motion_mag"],
                duration_sec=row["end_sec"] - row["start_sec"],
                asset_hash=row["asset_hash"],
            ).analysis
            values = {
                name: getattr(result, name)
                for name in (
                    "shot_scale", "subject_motion", "compression_score",
                    "color_palette", "subtitle_region", "pose_quality",
                    "face_visibility", "eye_visibility", "visual_energy", "cutability",
                    "composition", "image_quality", "blur_score", "camera_motion",
                )
            }
            conn.execute(
                """
                UPDATE shots SET
                  shot_scale=?,shot_scale_confidence=?,
                  subject_motion=?,subject_motion_confidence=?,
                  compression_score=?,compression_score_confidence=?,
                  color_palette=?,color_palette_confidence=?,
                  subtitle_region=?,subtitle_region_confidence=?,
                  pose_quality=?,pose_quality_confidence=?,
                  face_visibility=?,face_visibility_confidence=?,
                  eye_visibility=?,eye_visibility_confidence=?,
                  visual_energy=?,visual_energy_confidence=?,
                  composition=?,composition_confidence=?,
                  image_quality=?,image_quality_confidence=?,
                  blur_score=?,blur_score_confidence=?,
                  camera_motion=?,camera_motion_confidence=?,
                  cutability=?,cutability_confidence=?,analysis_version=?
                WHERE id=?
                """,
                (
                    values["shot_scale"].value, values["shot_scale"].confidence,
                    values["subject_motion"].value, values["subject_motion"].confidence,
                    values["compression_score"].value, values["compression_score"].confidence,
                    json.dumps(values["color_palette"].value),
                    values["color_palette"].confidence,
                    json.dumps(values["subtitle_region"].value),
                    values["subtitle_region"].confidence,
                    values["pose_quality"].value, values["pose_quality"].confidence,
                    values["face_visibility"].value, values["face_visibility"].confidence,
                    values["eye_visibility"].value, values["eye_visibility"].confidence,
                    values["visual_energy"].value, values["visual_energy"].confidence,
                    values["composition"].value, values["composition"].confidence,
                    values["image_quality"].value, values["image_quality"].confidence,
                    values["blur_score"].value, values["blur_score"].confidence,
                    values["camera_motion"].value, values["camera_motion"].confidence,
                    values["cutability"].value, values["cutability"].confidence,
                    PIPELINE_VERSION, row["id"],
                ),
            )
            done += 1
            if done % 250 == 0:
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            failed.append({"shot_id": row["id"], "error": str(exc)})
    conn.commit()
    return {"selected": len(rows), "analyzed": done, "failed": failed}


__all__ = [
    "MODEL",
    "MODEL_VERSION",
    "PIPELINE_VERSION",
    "VisualAnalyzer",
    "VisualResult",
    "analyze_pending",
]

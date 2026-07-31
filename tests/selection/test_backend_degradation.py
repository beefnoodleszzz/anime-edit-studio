"""Every heavy/optional backend must degrade instead of blocking (REFACTOR.md §22.7)."""
from __future__ import annotations

import numpy as np

from studio.selection.backends.dense_embedding import (
    NullEmbeddingBackend,
    create_dense_embedding_backend,
)
from studio.selection.backends.subject_tracker import (
    LucasKanadeSubjectTrackerBackend,
    create_subject_tracker_backend,
)
from studio.selection.backends.video_quality import (
    HeuristicVideoQualityBackend,
    create_video_quality_backend,
)
from studio.selection.schemas import BoundingBox


def test_null_embedding_backend_never_raises():
    backend = NullEmbeddingBackend()
    assert backend.status.available is False
    assert backend.embed(np.zeros((8, 8, 3), dtype=np.uint8)) is None


def test_dense_embedding_factory_degrades_without_raising():
    backend = create_dense_embedding_backend()
    assert backend.status is not None
    # Whatever backend is selected, embedding must never raise even if the
    # underlying model stack is unavailable in this environment.
    result = backend.embed(np.zeros((8, 8, 3), dtype=np.uint8))
    assert result is None or isinstance(result, np.ndarray)


def test_subject_tracker_falls_back_to_lucas_kanade():
    backend = create_subject_tracker_backend()
    assert isinstance(backend, LucasKanadeSubjectTrackerBackend)
    frames = [np.zeros((60, 60, 3), dtype=np.uint8) for _ in range(3)]
    points = backend.track(frames, BoundingBox(x=0.2, y=0.2, w=0.4, h=0.4))
    assert points[0].confidence <= 0.55


def test_video_quality_falls_back_to_heuristic_not_dover():
    backend = create_video_quality_backend()
    assert isinstance(backend, HeuristicVideoQualityBackend)
    scores = backend.score([np.zeros((30, 30, 3), dtype=np.uint8)])
    assert set(scores) == {"technical_quality_aux", "aesthetic_quality_aux"}

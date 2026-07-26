"""Versioned semantic embeddings and aesthetic representative-frame selection."""

from .encoder import (
    AESTHETIC_PIPELINE_VERSION,
    EMBEDDING_PIPELINE_VERSION,
    OpenClipBackend,
    encode_pending,
)

__all__ = [
    "AESTHETIC_PIPELINE_VERSION",
    "EMBEDDING_PIPELINE_VERSION",
    "OpenClipBackend",
    "encode_pending",
]

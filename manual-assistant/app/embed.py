"""Embeddings from an Ollama model, stored as numpy arrays on disk.

One .npz per document holds the chunk ids and a unit-normalised float32 matrix,
so cosine similarity is a dot product. SQLite is not used for the vectors.

If the embedding service is not reachable the functions here return nothing and
log a warning. Search then runs on BM25 alone. Nothing raises, because a manual
that cannot be embedded is still a manual worth searching.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np

from .config import Settings, get_settings
from .logsetup import get_logger

log = get_logger("embed")

BATCH = 16


class EmbeddingUnavailable(RuntimeError):
    """The embedding service did not answer. Callers fall back to BM25."""


def _vector_path(settings: Settings, doc_id: int) -> Path:
    return settings.vectors_dir / f"{doc_id}.npz"


def _post_embeddings(texts: list[str], settings: Settings) -> list[list[float]]:
    """Call the Ollama embeddings API, newer endpoint first."""
    base = settings.embed_base_url.rstrip("/")
    with httpx.Client(timeout=settings.embed_timeout_s) as client:
        try:
            response = client.post(
                f"{base}/api/embed",
                json={"model": settings.embed_model, "input": texts},
            )
            if response.status_code == 404:
                raise httpx.HTTPStatusError(
                    "no /api/embed", request=response.request, response=response
                )
            response.raise_for_status()
            payload = response.json()
            vectors = payload.get("embeddings")
            if vectors:
                return vectors
            single = payload.get("embedding")
            if single:
                return [single]
            raise EmbeddingUnavailable("embedding response contained no vectors")
        except httpx.HTTPStatusError:
            # Older Ollama builds only have /api/embeddings, one text per call.
            out: list[list[float]] = []
            for text in texts:
                response = client.post(
                    f"{base}/api/embeddings",
                    json={"model": settings.embed_model, "prompt": text},
                )
                response.raise_for_status()
                vector = response.json().get("embedding")
                if not vector:
                    raise EmbeddingUnavailable("embedding response contained no vectors")
                out.append(vector)
            return out


def embed_texts(texts: list[str], settings: Settings | None = None) -> np.ndarray:
    """Unit-normalised embeddings, one row per text.

    Raises EmbeddingUnavailable if the service cannot be reached or answers
    with something unusable.
    """
    s = settings or get_settings()
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    rows: list[list[float]] = []
    try:
        for start in range(0, len(texts), BATCH):
            rows.extend(_post_embeddings(texts[start : start + BATCH], s))
    except EmbeddingUnavailable:
        raise
    except Exception as exc:                       # noqa: BLE001 - turned into one type
        raise EmbeddingUnavailable(f"{type(exc).__name__}: {exc}") from exc

    if len(rows) != len(texts):
        raise EmbeddingUnavailable(
            f"asked for {len(texts)} embeddings, received {len(rows)}"
        )
    matrix = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def embed_query(text: str, settings: Settings | None = None) -> np.ndarray:
    return embed_texts([text], settings)[0]


def service_status(settings: Settings | None = None) -> tuple[bool, str]:
    """Used by /healthz. Never raises."""
    s = settings or get_settings()
    try:
        embed_texts(["status check"], s)
        return True, f"{s.embed_model} reachable"
    except EmbeddingUnavailable as exc:
        return False, str(exc)


def build_document_vectors(
    doc_id: int, texts: list[str], chunk_ids: list[int], settings: Settings | None = None
) -> int:
    """Embed one document's chunks and write them next to the others.

    Returns the number of vectors written, or 0 when the service is unavailable.
    """
    s = settings or get_settings()
    s.vectors_dir.mkdir(parents=True, exist_ok=True)
    path = _vector_path(s, doc_id)
    if not texts:
        path.unlink(missing_ok=True)
        return 0
    try:
        matrix = embed_texts(texts, s)
    except EmbeddingUnavailable as exc:
        # The document stays searchable through BM25. This is logged, not raised.
        log.warning(
            "embeddings_unavailable",
            extra={"doc_id": doc_id, "error": str(exc), "fallback": "bm25_only"},
        )
        path.unlink(missing_ok=True)
        return 0
    np.savez(
        path,
        ids=np.asarray(chunk_ids, dtype=np.int64),
        vectors=matrix.astype(np.float32),
    )
    log.info(
        "embeddings_written",
        extra={"doc_id": doc_id, "count": int(matrix.shape[0]), "dim": int(matrix.shape[1])},
    )
    return int(matrix.shape[0])


def drop_document_vectors(doc_id: int, settings: Settings | None = None) -> None:
    _vector_path(settings or get_settings(), doc_id).unlink(missing_ok=True)


def load_vectors(
    doc_ids: list[int], settings: Settings | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Chunk ids and the matching unit vectors for the given documents."""
    s = settings or get_settings()
    id_parts: list[np.ndarray] = []
    vector_parts: list[np.ndarray] = []
    dim = 0
    for doc_id in doc_ids:
        path = _vector_path(s, doc_id)
        if not path.exists():
            continue
        try:
            with np.load(path) as data:
                ids = data["ids"]
                vectors = data["vectors"]
        except Exception as exc:                   # noqa: BLE001 - a bad file is skipped
            log.warning("vector_file_unreadable", extra={"doc_id": doc_id, "error": str(exc)})
            continue
        if ids.shape[0] != vectors.shape[0] or vectors.ndim != 2:
            continue
        if dim and vectors.shape[1] != dim:
            # A document embedded with a different model cannot be compared.
            log.warning(
                "vector_dimension_mismatch",
                extra={"doc_id": doc_id, "expected": dim, "found": int(vectors.shape[1])},
            )
            continue
        dim = vectors.shape[1]
        id_parts.append(ids)
        vector_parts.append(vectors)
    if not id_parts:
        return np.zeros((0,), dtype=np.int64), np.zeros((0, 0), dtype=np.float32)
    return np.concatenate(id_parts), np.vstack(vector_parts)

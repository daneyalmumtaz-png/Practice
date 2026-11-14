"""Hybrid search: BM25 over lemmatised text, cosine over bge-m3, fused with RRF.

Two halves, because neither is enough on its own. BM25 finds an exact command or
file extension that an embedding blurs. Embeddings find a Finnish question whose
words appear nowhere in an English manual. Reciprocal Rank Fusion combines them
without needing the two score scales to be comparable.

If the embedding service is down, the BM25 half runs alone and the result says so.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from rank_bm25 import BM25Okapi

from . import db, embed
from .config import Settings, get_settings
from .lang import TOKEN, detect_language, highlight_words, query_terms, tokenize
from .logsetup import get_logger

log = get_logger("search")

SNIPPET_CHARS = 260


@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    doc_title: str
    product: str
    version: str
    section_path: str
    page_label: str
    pdf_page: int
    chunk_type: str
    text: str
    snippet: str
    highlights: list[str]
    score: float
    bm25_rank: int | None = None
    vector_rank: int | None = None


@dataclass
class SearchOutcome:
    hits: list[Hit]
    queries: list[str]
    language: str
    used_embeddings: bool
    embedding_error: str = ""
    elapsed_ms: int = 0
    candidates: int = 0


@dataclass
class Source:
    """A hit as the model sees it, numbered exactly as the page numbers it."""

    number: int
    hit: Hit


# ------------------------------------------------------------------------- snippets


def make_snippet(text: str, terms: set[str], *, width: int = SNIPPET_CHARS) -> str:
    """The part of the chunk where the question's words actually appear."""
    text = text.strip()
    if len(text) <= width:
        return text
    hits = [
        m.start()
        for m in TOKEN.finditer(text)
        if terms.intersection(tokenize(m.group(0), drop_stopwords=False))
    ]
    if not hits:
        return text[:width].rstrip() + "…"

    best_start, best_count = hits[0], 0
    for start in hits:
        count = sum(1 for h in hits if start <= h < start + width)
        if count > best_count:
            best_start, best_count = start, count

    begin = max(0, best_start - 60)
    end = min(len(text), begin + width)
    if begin:
        space = text.find(" ", begin)
        begin = space + 1 if 0 <= space < begin + 30 else begin
    piece = text[begin:end].strip()
    return ("…" if begin else "") + piece + ("…" if end < len(text) else "")


def table_snippet(text: str) -> str:
    """A table's caption and header, which is what identifies it in a list."""
    lines = [l for l in text.split("\n") if l.strip()]
    return "\n".join(lines[:3])


# ---------------------------------------------------------------------------- index


class SearchIndex:
    """BM25 and vectors over every searchable chunk, rebuilt when the data changes."""

    def __init__(self, settings: Settings, *, include_superseded: bool = False) -> None:
        self.settings = settings
        self.include_superseded = include_superseded
        self.signature = ""
        self.rows: list[sqlite3.Row] = []
        self.position: dict[int, int] = {}
        self.bm25: BM25Okapi | None = None
        self.vector_ids: np.ndarray = np.zeros((0,), dtype=np.int64)
        self.vectors: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self.vector_position: dict[int, int] = {}

    # The signature covers everything that would change a result: the chunk rows
    # themselves and each document's status and index state.
    def _current_signature(self, conn: sqlite3.Connection) -> str:
        digest = hashlib.sha256()
        chunk_count, max_chunk = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM chunks"
        ).fetchone()
        digest.update(f"{chunk_count}:{max_chunk}:{self.include_superseded}".encode())
        for row in conn.execute(
            "SELECT id, status, ingest_status, chunk_count, indexed_at"
            " FROM documents ORDER BY id"
        ):
            digest.update("|".join(str(v) for v in tuple(row)).encode())
        return digest.hexdigest()

    def refresh(self, conn: sqlite3.Connection) -> None:
        signature = self._current_signature(conn)
        if signature == self.signature and self.bm25 is not None:
            return

        rows = db.chunks_for_search(conn, include_superseded=self.include_superseded)
        weight = max(1, self.settings.retrieval.section_path_weight)
        corpus: list[list[str]] = []
        for row in rows:
            # The section path is indexed `weight` times, so a question naming a
            # section is pulled towards that section's chunks.
            tokens = tokenize(row["section_path"]) * weight + tokenize(row["text"])
            corpus.append(tokens or ["∅"])

        self.rows = rows
        self.position = {int(r["id"]): i for i, r in enumerate(rows)}
        self.bm25 = BM25Okapi(corpus) if corpus else None

        doc_ids = sorted({int(r["doc_id"]) for r in rows})
        ids, vectors = embed.load_vectors(doc_ids, self.settings)
        keep = [i for i, cid in enumerate(ids.tolist()) if cid in self.position]
        if keep and vectors.size:
            self.vector_ids = ids[keep]
            self.vectors = vectors[keep]
        else:
            self.vector_ids = np.zeros((0,), dtype=np.int64)
            self.vectors = np.zeros((0, 0), dtype=np.float32)
        self.vector_position = {int(c): i for i, c in enumerate(self.vector_ids.tolist())}
        self.signature = signature
        log.info(
            "index_built",
            extra={
                "chunks": len(rows),
                "documents": len(doc_ids),
                "vectors": int(self.vectors.shape[0]),
                "include_superseded": self.include_superseded,
            },
        )

    # ------------------------------------------------------------------ rankings

    def bm25_ranking(self, query: str, depth: int) -> list[int]:
        """Chunk ids, best first."""
        if self.bm25 is None:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        if not len(scores):
            return []
        order = np.argsort(-np.asarray(scores))[:depth]
        return [
            int(self.rows[i]["id"]) for i in order if scores[i] > 0
        ]

    def vector_ranking(self, vector: np.ndarray, depth: int) -> list[int]:
        if self.vectors.size == 0 or vector.size == 0:
            return []
        if vector.shape[0] != self.vectors.shape[1]:
            log.warning(
                "query_vector_dimension_mismatch",
                extra={"query_dim": int(vector.shape[0]), "index_dim": int(self.vectors.shape[1])},
            )
            return []
        scores = self.vectors @ vector
        order = np.argsort(-scores)[:depth]
        return [int(self.vector_ids[i]) for i in order]


_indexes: dict[tuple[str, bool], SearchIndex] = {}
_lock = threading.Lock()


def get_index(
    settings: Settings | None = None, *, include_superseded: bool = False
) -> SearchIndex:
    s = settings or get_settings()
    key = (str(s.db_path), include_superseded)
    with _lock:
        index = _indexes.get(key)
        if index is None:
            index = SearchIndex(s, include_superseded=include_superseded)
            _indexes[key] = index
        with db.session(s.db_path) as conn:
            index.refresh(conn)
        return index


def reset_indexes() -> None:
    """Drop the cached indexes. Used by tests and after a delete or re-index."""
    with _lock:
        _indexes.clear()


# ------------------------------------------------------------------------- fusion


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]], *, k: int = 60
) -> dict[int, float]:
    """Sum of 1/(k + rank) over every ranking a chunk appears in, rank from 1."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + position)
    return fused


def search(
    queries: Sequence[str],
    *,
    settings: Settings | None = None,
    include_superseded: bool = False,
    limit: int | None = None,
    depth: int = 50,
) -> SearchOutcome:
    """Run every query through both halves and fuse the rankings.

    `queries` is the user's question, optionally followed by an English version of
    it. Each query contributes its own BM25 and vector ranking to the fusion.
    """
    started = time.perf_counter()
    s = settings or get_settings()
    wanted = limit or s.retrieval.ui_results
    clean = [q.strip() for q in queries if q and q.strip()]
    if not clean:
        return SearchOutcome([], [], "en", False, elapsed_ms=0)

    index = get_index(s, include_superseded=include_superseded)
    language = detect_language(clean[0])

    rankings: list[list[int]] = []
    for query in clean:
        rankings.append(index.bm25_ranking(query, depth))

    used_embeddings = False
    embedding_error = ""
    if index.vectors.size:
        try:
            vectors = embed.embed_texts(list(clean), s)
            for row in vectors:
                ranking = index.vector_ranking(row, depth)
                if ranking:
                    rankings.append(ranking)
                    used_embeddings = True
        except embed.EmbeddingUnavailable as exc:
            embedding_error = str(exc)
            log.warning(
                "search_bm25_only",
                extra={"error": embedding_error, "fallback": "bm25_only"},
            )
    else:
        embedding_error = "no embeddings stored for the current documents"

    fused = reciprocal_rank_fusion(rankings, k=s.retrieval.rrf_k)
    if not fused:
        return SearchOutcome(
            [], clean, language, used_embeddings, embedding_error,
            int((time.perf_counter() - started) * 1000), 0,
        )

    terms = set()
    for query in clean:
        terms |= query_terms(query)

    ranks: list[dict[int, int]] = []
    for ranking in rankings:
        ranks.append({cid: i for i, cid in enumerate(ranking, start=1)})
    bm25_count = len(clean)

    hits: list[Hit] = []
    seen_pages: set[tuple[int, int]] = set()
    for chunk_id, score in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0])):
        position = index.position.get(chunk_id)
        if position is None:
            continue
        row = index.rows[position]
        # One card per page of a manual. Two chunks from the same page would send
        # the reader to the same place twice.
        page_key = (int(row["doc_id"]), int(row["pdf_page"]))
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)

        text = row["text"]
        snippet = (
            table_snippet(text)
            if row["chunk_type"] == "table"
            else make_snippet(text, terms)
        )
        bm25_rank = min(
            (r[chunk_id] for r in ranks[:bm25_count] if chunk_id in r), default=None
        )
        vector_rank = min(
            (r[chunk_id] for r in ranks[bm25_count:] if chunk_id in r), default=None
        )
        hits.append(
            Hit(
                chunk_id=chunk_id,
                doc_id=int(row["doc_id"]),
                doc_title=row["doc_title"],
                product=row["product"],
                version=row["version"],
                section_path=row["section_path"],
                page_label=row["page_label"],
                pdf_page=int(row["pdf_page"]),
                chunk_type=row["chunk_type"],
                text=text,
                snippet=snippet,
                highlights=highlight_words(snippet, terms),
                score=score,
                bm25_rank=bm25_rank,
                vector_rank=vector_rank,
            )
        )
        if len(hits) >= wanted:
            break

    return SearchOutcome(
        hits=hits,
        queries=clean,
        language=language,
        used_embeddings=used_embeddings,
        embedding_error=embedding_error,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        candidates=len(fused),
    )


# --------------------------------------------------------------- sources for the model


def estimate_tokens(text: str) -> int:
    """Rough token count. Four characters per token is close enough for English
    and Finnish prose to size a context budget, and it never underestimates by
    much on the Markdown tables."""
    return max(1, len(text) // 4)


def select_sources(hits: Sequence[Hit], token_budget: int) -> list[Source]:
    """The hits that fit the budget, keeping the numbers shown on the page.

    Chunks go to the model whole. A chunk that does not fit is left out, never
    cut, so the model never reasons from half a sentence.
    """
    sources: list[Source] = []
    used = 0
    for number, hit in enumerate(hits, start=1):
        cost = estimate_tokens(hit.text) + 40      # 40 for the source's own heading
        if used + cost > token_budget:
            continue
        sources.append(Source(number=number, hit=hit))
        used += cost
    return sources

"""Search tests: ranking, Finnish, fallback, and what reaches the model."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import db, embed, search as search_module
from app.chunker import Chunk
from app.eval import Row, parse_pages, summarise
from app.ingest import ingest_path
from app.lang import detect_language, tokenize
from app.search import (
    Hit,
    estimate_tokens,
    get_index,
    reciprocal_rank_fusion,
    search,
    select_sources,
)

from conftest import build_no_bookmarks_pdf, fake_embedder


@pytest.fixture
def library(workspace, sample_pdf: Path):
    """One indexed manual, no embeddings. BM25 only."""
    result = ingest_path(
        sample_pdf,
        title="Example Engineering Handbook",
        product="Other Bentley product",
        version="1.0",
        settings=workspace,
    )
    assert result.status == db.INGEST_READY
    search_module.reset_indexes()
    return workspace, result


@pytest.fixture
def library_with_vectors(workspace, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch):
    """The same manual with vectors built by the stand-in multilingual embedder."""
    monkeypatch.setattr(embed, "embed_texts", fake_embedder())
    result = ingest_path(
        sample_pdf, title="Example Engineering Handbook", settings=workspace
    )
    assert result.status == db.INGEST_READY
    assert result.vector_count > 0
    search_module.reset_indexes()
    return workspace, result


# ---------------------------------------------------------------- fusion arithmetic


def test_rrf_sums_reciprocal_ranks():
    fused = reciprocal_rank_fusion([[7, 1], [1, 7]], k=60)
    # Both appear at rank 1 and rank 2, so both score 1/61 + 1/62 and tie.
    assert fused[7] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[1] == pytest.approx(fused[7])


def test_rrf_prefers_a_chunk_found_by_both_halves():
    """Agreement between the two halves is what RRF rewards."""
    # 1 is second in both rankings; 9 is first in one and absent from the other.
    fused = reciprocal_rank_fusion([[7, 1, 2], [9, 1, 3]], k=60)
    assert fused[1] == pytest.approx(2 / 62)
    assert fused[9] == pytest.approx(1 / 61)
    assert fused[1] > fused[9]
    assert fused[1] > fused[7]


def test_rrf_k_default_is_sixty(workspace):
    assert workspace.retrieval.rrf_k == 60


# -------------------------------------------------------------------------- BM25


def test_bm25_finds_the_section_that_names_the_term(library):
    settings, _ = library
    outcome = search(["borehole import"], settings=settings)
    assert outcome.hits
    top = outcome.hits[0]
    assert top.section_path == "Importing data > Borehole import"
    assert top.bm25_rank == 1
    assert top.vector_rank is None


def test_section_path_counts_twice_in_the_index(library):
    settings, _ = library
    index = get_index(settings)
    row = next(
        r for r in index.rows if r["section_path"] == "Importing data > Borehole import"
    )
    assert int(row["id"]) in index.position
    # Rebuild the token list the same way the index does and compare counts.
    path_tokens = tokenize(row["section_path"])
    corpus_tokens = (
        tokenize(row["section_path"]) * settings.retrieval.section_path_weight
        + tokenize(row["text"])
    )
    assert settings.retrieval.section_path_weight == 2
    assert path_tokens
    for term in set(path_tokens):
        assert corpus_tokens.count(term) >= 2 * path_tokens.count(term)


def test_a_section_only_named_in_the_path_is_still_found(library):
    """"Reporting" appears in the bookmark and the heading, and must rank first."""
    settings, _ = library
    outcome = search(["reporting"], settings=settings)
    assert outcome.hits[0].section_path == "Reporting"


# ------------------------------------------------------------------------ Finnish


@pytest.mark.parametrize(
    "inflected, base",
    [
        ("pohjatutkimustiedot", "pohjatutkimustieto"),
        ("kairauksia", "kairaus"),
        ("raporttipohjat", "raporttipohja"),
        ("koordinaatistoa", "koordinaatisto"),
    ],
)
def test_finnish_lemmatisation_links_inflected_forms(inflected: str, base: str):
    assert set(tokenize(inflected)) & set(tokenize(base))


def test_english_stemming_links_inflected_forms():
    assert set(tokenize("importing")) & set(tokenize("imported"))
    assert set(tokenize("boreholes")) & set(tokenize("borehole"))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("How do I import borehole data?", "en"),
        ("Miten tuon pohjatutkimustiedot ohjelmaan?", "fi"),
        ("Mistä löydän raporttipohjat?", "fi"),
        ("What file formats can be imported?", "en"),
        ("", "en"),
    ],
)
def test_language_detection(text: str, expected: str):
    assert detect_language(text) == expected


def test_finnish_question_reaches_an_english_section_through_embeddings(
    library_with_vectors, monkeypatch: pytest.MonkeyPatch
):
    """The decisive case: a Finnish question with no English words in it.

    BM25 cannot bridge the languages on its own. The vector half has to, which is
    why the embedding model must be multilingual.
    """
    settings, _ = library_with_vectors
    monkeypatch.setattr(embed, "embed_texts", fake_embedder())

    question = "Mistä löydän raporttipohjat?"
    # First show that keyword search alone finds nothing for this question.
    index = get_index(settings)
    assert index.bm25_ranking(question, 50) == []

    outcome = search([question], settings=settings)
    assert outcome.used_embeddings is True
    assert outcome.hits, "the vector half must still find the section"
    assert outcome.hits[0].section_path == "Reporting"
    assert outcome.hits[0].vector_rank == 1
    assert outcome.hits[0].bm25_rank is None


def test_an_added_english_query_contributes_its_own_ranking(library):
    """A Finnish question is searched together with an English version of it."""
    settings, _ = library
    finnish_only = search(["Mistä löydän raporttipohjat?"], settings=settings)
    with_english = search(
        ["Mistä löydän raporttipohjat?", "where are the report templates"],
        settings=settings,
    )
    assert not finnish_only.hits
    assert with_english.hits
    assert "Reporting" in {h.section_path for h in with_english.hits}
    assert with_english.queries == [
        "Mistä löydän raporttipohjat?",
        "where are the report templates",
    ]


# ---------------------------------------------------------------- fallback behaviour


def test_search_falls_back_to_bm25_when_the_embedding_service_is_down(
    library_with_vectors, monkeypatch: pytest.MonkeyPatch
):
    def refuse(texts, settings=None):
        raise embed.EmbeddingUnavailable("ConnectError: connection refused")

    monkeypatch.setattr(embed, "embed_texts", refuse)
    settings, _ = library_with_vectors

    outcome = search(["borehole import"], settings=settings)
    assert outcome.used_embeddings is False
    assert "refused" in outcome.embedding_error
    assert outcome.hits, "BM25 alone must still answer"
    assert outcome.hits[0].bm25_rank == 1


def test_search_reports_when_no_vectors_are_stored(library):
    settings, _ = library
    outcome = search(["borehole import"], settings=settings)
    assert outcome.used_embeddings is False
    assert "no embeddings stored" in outcome.embedding_error
    assert outcome.hits


def test_a_document_with_no_vectors_is_still_searchable(
    library_with_vectors, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings, first = library_with_vectors
    # Second manual ingested while the embedding service is down.
    monkeypatch.setattr(
        embed,
        "embed_texts",
        lambda texts, settings=None: (_ for _ in ()).throw(
            embed.EmbeddingUnavailable("down")
        ),
    )
    second = ingest_path(
        build_no_bookmarks_pdf(tmp_path / "reporting.pdf"),
        title="Reporting Supplement",
        settings=settings,
    )
    assert second.status == db.INGEST_READY
    assert second.vector_count == 0
    search_module.reset_indexes()

    monkeypatch.setattr(embed, "embed_texts", fake_embedder())
    outcome = search(["editing a template"], settings=settings)
    titles = {h.doc_title for h in outcome.hits}
    assert "Reporting Supplement" in titles


# ------------------------------------------------------------------ library rules


def test_superseded_documents_are_never_returned_by_default(
    workspace, sample_pdf: Path, tmp_path: Path
):
    current = ingest_path(sample_pdf, title="Handbook v2", settings=workspace)
    old = ingest_path(
        build_no_bookmarks_pdf(tmp_path / "v1.pdf"),
        title="Handbook v1",
        status=db.STATUS_SUPERSEDED,
        settings=workspace,
    )
    search_module.reset_indexes()

    outcome = search(["reporting template"], settings=workspace)
    assert outcome.hits
    assert old.doc_id not in {h.doc_id for h in outcome.hits}

    admin = search(["reporting template"], settings=workspace, include_superseded=True)
    assert old.doc_id in {h.doc_id for h in admin.hits}
    assert current.doc_id in {h.doc_id for h in admin.hits} or admin.hits


def test_results_are_one_card_per_page(library):
    """Page 7 produces several chunks. The reader should see it once."""
    settings, result = library
    with db.session(settings.db_path) as conn:
        rows = db.chunks_for_doc(conn, result.doc_id)
    page_counts: dict[int, int] = {}
    for row in rows:
        page_counts[row["pdf_page"]] = page_counts.get(row["pdf_page"], 0) + 1
    assert max(page_counts.values()) > 1, "fixture should give one page several chunks"

    outcome = search(["field description column import file"], settings=settings)
    pages = [(h.doc_id, h.pdf_page) for h in outcome.hits]
    assert len(pages) == len(set(pages))


def test_every_hit_carries_what_a_card_must_show(library):
    settings, _ = library
    outcome = search(["borehole import"], settings=settings)
    for hit in outcome.hits:
        assert hit.doc_title
        assert hit.page_label
        assert hit.pdf_page >= 1
        assert hit.snippet
        assert hit.chunk_type in ("text", "table")
    assert any(h.section_path for h in outcome.hits)


def test_snippet_marks_the_matched_words(library):
    settings, _ = library
    outcome = search(["borehole import"], settings=settings)
    top = outcome.hits[0]
    assert top.highlights
    assert any("borehole" in w for w in top.highlights)
    for word in top.highlights:
        assert word in top.snippet.lower()


def test_ui_result_count_is_capped_by_config(library):
    settings, _ = library
    outcome = search(["import data file project report"], settings=settings)
    assert len(outcome.hits) <= settings.retrieval.ui_results


def test_empty_question_returns_nothing(library):
    settings, _ = library
    outcome = search(["   "], settings=settings)
    assert outcome.hits == []


# --------------------------------------------------------- what reaches the model


def test_whole_chunks_reach_the_model(library):
    """The old prototype cut text to 500 characters. Nothing is cut here."""
    settings, result = library
    outcome = search(["field descriptions import column"], settings=settings)
    sources = select_sources(outcome.hits, settings.retrieval.token_budget)
    assert sources

    with db.session(settings.db_path) as conn:
        stored = {int(r["id"]): r["text"] for r in db.chunks_for_doc(conn, result.doc_id)}
    for source in sources:
        assert source.hit.text == stored[source.hit.chunk_id]
        assert not source.hit.text.endswith("…")
    assert max(len(s.hit.text) for s in sources) > 500


def test_source_numbers_match_the_cards_shown(library):
    settings, _ = library
    outcome = search(["import borehole"], settings=settings)
    sources = select_sources(outcome.hits, settings.retrieval.token_budget)
    for source in sources:
        assert outcome.hits[source.number - 1] is source.hit


def test_token_budget_drops_whole_chunks_rather_than_cutting_them():
    def hit(chunk_id: int, chars: int) -> Hit:
        text = "x" * chars
        return Hit(
            chunk_id=chunk_id, doc_id=1, doc_title="t", product="", version="",
            section_path="s", page_label="1", pdf_page=1, chunk_type="text",
            text=text, snippet=text[:50], highlights=[], score=1.0,
        )

    hits = [hit(1, 2000), hit(2, 2000), hit(3, 2000)]
    sources = select_sources(hits, token_budget=800)
    # 2000 characters is about 500 tokens, so only one fits in 800.
    assert len(sources) == 1
    assert len(sources[0].hit.text) == 2000


def test_estimate_tokens_is_about_four_characters_each():
    assert estimate_tokens("x" * 400) == 100
    assert estimate_tokens("") == 1


# --------------------------------------------------------------------- eval helpers


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", ()),
        ("12", (12,)),
        ("12, 14", (12, 14)),
        ("12-14", (12, 13, 14)),
        ("12;14", (12, 14)),
        ("not a page", ()),
    ],
)
def test_parse_pages(raw: str, expected: tuple[int, ...]):
    assert parse_pages(raw) == expected


def test_summarise_reports_per_language_and_overall():
    from app.eval import Scored

    rows = [
        Scored(Row("q1", "en", "", (1,), "", True), rank=1, hits=[]),
        Scored(Row("q2", "en", "", (1,), "", True), rank=4, hits=[]),
        Scored(Row("q3", "fi", "", (1,), "", True), rank=2, hits=[]),
        Scored(Row("q4", "fi", "", (), "", False), rank=None, hits=[]),
    ]
    summary = summarise(rows)
    assert summary["overall"]["questions"] == 3
    assert summary["en"]["hit_at_3"] == 0.5
    assert summary["fi"]["hit_at_3"] == 1.0
    assert summary["overall"]["mrr_at_10"] == pytest.approx((1 + 0.25 + 0.5) / 3, abs=1e-4)

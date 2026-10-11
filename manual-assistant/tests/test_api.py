"""API tests: login, search, the streamed answer, files, admin and health.

The model is a real HTTP server started by the stub_model fixture, so the app's
own HTTP client, its Server-Sent Events parsing and its timeouts are exercised.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import db
from app.ingest import ingest_path

from conftest import TEST_PASSWORD, build_broken_pdf, build_sample_pdf, sign_in


def load_manual(client, tmp_path: Path, *, title="Example Engineering Handbook",
                status=db.STATUS_CURRENT, name="handbook.pdf"):
    from app import search as search_module

    settings = client.app_module.settings
    result = ingest_path(
        build_sample_pdf(tmp_path / name),
        title=title,
        product="Other Bentley product",
        version="1.0",
        status=status,
        settings=settings,
    )
    assert result.status == db.INGEST_READY
    search_module.reset_indexes()
    return result


def read_events(response) -> list[tuple[str, object]]:
    """Parse a Server-Sent Events body into (event name, payload) pairs."""
    events: list[tuple[str, object]] = []
    name = "message"
    data: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if data:
                events.append((name, json.loads("\n".join(data))))
            name, data = "message", []
            continue
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if data:
        events.append((name, json.loads("\n".join(data))))
    return events


def ask(client, question: str, history: list[dict] | None = None):
    with client.stream(
        "POST",
        "/api/ask",
        json={"question": question, "history": history or []},
    ) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        return read_events(response)


# --------------------------------------------------------------------- login


def test_pages_redirect_to_login_when_not_signed_in(api):
    for path in ("/", "/admin"):
        response = api.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("post", "/api/search", {"question": "x"}),
        ("post", "/api/ask", {"question": "x"}),
        ("get", "/api/docs", None),
        ("get", "/api/admin/documents", None),
        ("get", "/files/1", None),
        ("get", "/api/page/1/1.png", None),
        ("post", "/api/feedback", {"question_id": 1, "rating": "up"}),
    ],
)
def test_api_requires_a_session(api, method, path, body):
    call = getattr(api, method)
    response = call(path, json=body) if body is not None else call(path)
    assert response.status_code == 401


def test_login_page_is_reachable_without_a_session(api):
    response = api.get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text


def test_wrong_password_is_rejected(api):
    response = api.post(
        "/login", data={"username": "engineer", "password": "wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "Wrong username or password" in response.text


def test_sign_in_and_out(api):
    sign_in(api)
    assert api.get("/", follow_redirects=False).status_code == 200
    api.post("/logout", follow_redirects=False)
    assert api.get("/", follow_redirects=False).status_code == 303


# ----------------------------------------------------------------- the pages


def test_chat_page_takes_its_wording_from_config(api):
    sign_in(api)
    response = api.get("/")
    assert response.status_code == 200
    settings = api.app_module.settings
    assert settings.app_name in response.text
    assert settings.disclaimer in response.text
    for example in settings.example_questions:
        assert example in response.text


def test_no_product_wording_is_baked_into_the_html_files(api):
    """The HTML on disk carries no product name, greeting or example question."""
    settings = api.app_module.settings
    for name in ("index.html", "admin.html", "login.html"):
        text = (settings.static_dir / name).read_text(encoding="utf-8")
        assert "<!--APP_CONFIG-->" in text
        for value in [settings.greeting, *settings.example_questions, *settings.products]:
            assert value not in text


def test_admin_page_needs_the_admin_role(api):
    sign_in(api, "engineer")
    assert api.get("/admin", follow_redirects=False).status_code == 403
    api.post("/logout", follow_redirects=False)
    sign_in(api, "admin")
    assert api.get("/admin", follow_redirects=False).status_code == 200


# ---------------------------------------------------------------- /api/search


def test_search_returns_the_cards_without_calling_the_model(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    before = api.stub.chat_calls

    response = api.post("/api/search", json={"question": "borehole import"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["sections"], payload
    assert api.stub.chat_calls == before, "search must not call the language model"

    first = payload["sections"][0]
    for field in (
        "n", "doc_title", "section_path", "page_label", "pdf_page", "snippet",
        "highlights", "file_url", "page_image_url", "chunk_type",
    ):
        assert field in first
    assert first["doc_title"] == "Example Engineering Handbook"
    assert first["section_path"] == "Importing data > Borehole import"
    assert first["page_label"] == "4"
    assert first["pdf_page"] == 6
    assert first["page_label"] != str(first["pdf_page"])
    assert first["file_url"] == "/files/1#page=6"
    assert first["page_image_url"] == "/api/page/1/6.png"


def test_search_rejects_an_empty_question(api):
    sign_in(api)
    assert api.post("/api/search", json={"question": "  "}).status_code == 400


# ------------------------------------------------------------------- /api/ask


def test_ask_streams_sources_then_tokens_then_done(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)

    events = ask(api, "How do I import borehole data?")
    names = [name for name, _ in events]
    assert names[0] == "sources"
    assert names[-1] == "done"
    assert names.count("token") >= 2, names

    sources = events[0][1]
    assert sources["sections"]
    assert sources["sent_to_model"]

    answer = "".join(p["t"] for name, p in events if name == "token")
    assert answer == api.stub.answer

    done = events[-1][1]
    assert done["question_id"] > 0
    assert done["source_count"] == len(sources["sent_to_model"])
    assert done["not_found"] is False
    assert done["latency_ms"] >= 0


def test_whole_chunks_reach_the_model(api, tmp_path: Path):
    """No 500 character cut. The prompt carries each chunk in full."""
    sign_in(api)
    result = load_manual(api, tmp_path)
    ask(api, "field descriptions import column")

    answer_requests = [
        r for r in api.stub.requests
        if "numbered sources" in (r["messages"][0]["content"] or "")
    ]
    assert answer_requests, "the answer call should carry the configured system prompt"
    prompt = answer_requests[-1]["messages"][1]["content"]

    settings = api.app_module.settings
    with db.session(settings.db_path) as conn:
        chunks = {r["id"]: r["text"] for r in db.chunks_for_doc(conn, result.doc_id)}
    long_chunks = [t for t in chunks.values() if len(t) > 500]
    assert long_chunks, "fixture should produce a chunk longer than 500 characters"
    assert any(t in prompt for t in long_chunks), "a long chunk was not sent whole"
    assert "…" not in prompt


def test_prompt_numbers_the_sources_as_the_page_does(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    events = ask(api, "borehole import")
    sources = events[0][1]

    prompt = api.stub.requests[-1]["messages"][1]["content"]
    for section in sources["sections"]:
        if section["n"] in sources["sent_to_model"]:
            assert f"[{section['n']}] Manual: {section['doc_title']}" in prompt
            assert f"PDF page {section['pdf_page']}" in prompt
            assert f"printed {section['page_label']}" in prompt


def test_model_parameters_follow_the_specification(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    ask(api, "borehole import")
    answer_request = [
        r for r in api.stub.requests
        if "numbered sources" in (r["messages"][0]["content"] or "")
    ][-1]
    assert answer_request["temperature"] == pytest.approx(0.1)
    assert answer_request["max_tokens"] == 700
    assert answer_request["stream"] is True
    assert answer_request["model"] == api.app_module.settings.llm_model


def test_not_found_when_the_search_finds_nothing(api, tmp_path: Path):
    """With no vectors, a question sharing no words with the manual finds nothing.

    An embedding search always returns its nearest neighbours, however distant,
    so the empty case is the keyword-only one. When the model is given sources it
    is the model that decides they do not answer, which the next test covers.
    """
    from app import search as search_module

    sign_in(api)
    load_manual(api, tmp_path)
    settings = api.app_module.settings
    for path in settings.vectors_dir.glob("*.npz"):
        path.unlink()
    search_module.reset_indexes()
    before = api.stub.chat_calls

    events = ask(api, "zzzqqq unrelated terminology xyzzy")
    assert events[0][1]["sections"] == []
    answer = "".join(p["t"] for name, p in events if name == "token")
    assert "Not found in the uploaded manuals" in answer
    done = events[-1][1]
    assert done["not_found"] is True
    assert done["source_count"] == 0
    # There was nothing to answer from, so the model was not asked.
    assert api.stub.chat_calls == before


def test_closest_sections_are_still_shown_when_the_model_says_not_found(
    api, tmp_path: Path
):
    """The model was given sources and judged that they do not answer."""
    sign_in(api)
    load_manual(api, tmp_path)
    api.stub.answer = (
        "Not found in the uploaded manuals\n\nThe closest section is [1]."
    )

    events = ask(api, "borehole import")
    sources = events[0][1]
    assert sources["sections"], "the closest sections must still be listed"
    assert sources["sent_to_model"], "the model was asked"
    assert events[-1][1]["not_found"] is True
    assert events[-1][1]["source_count"] > 0


def test_a_model_failure_is_reported_and_the_sections_survive(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    api.stub.chat_status = 500

    events = ask(api, "borehole import")
    names = [name for name, _ in events]
    assert names[0] == "sources"
    assert "error" in names
    assert names[-1] == "done"
    assert events[0][1]["sections"], "sections are shown even when the model fails"


# -------------------------------------------------------------- follow-ups, Finnish


def test_a_follow_up_question_is_rewritten_before_searching(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    api.stub.rewrite = "Which fields are read from a borehole file?"

    events = ask(
        api,
        "and which fields?",
        history=[{"question": "How do I import borehole data?", "answer": "Use the importer [1]"}],
    )
    sources = events[0][1]
    assert sources["rewritten"] is True
    assert sources["question"] == "Which fields are read from a borehole file?"
    assert sources["original_question"] == "and which fields?"
    assert "question rewritten from the conversation" in sources["notes"]

    rewrite_requests = [
        r for r in api.stub.requests if "rewrite" in r["messages"][0]["content"].lower()
    ]
    assert rewrite_requests
    conversation = rewrite_requests[0]["messages"][1]["content"]
    assert "How do I import borehole data?" in conversation


def test_only_the_last_three_turns_are_sent(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    history = [
        {"question": f"question number {i}", "answer": f"answer number {i}"}
        for i in range(1, 6)
    ]
    ask(api, "and now?", history=history)

    rewrite_requests = [
        r for r in api.stub.requests if "rewrite" in r["messages"][0]["content"].lower()
    ]
    conversation = rewrite_requests[0]["messages"][1]["content"]
    assert "question number 5" in conversation
    assert "question number 3" in conversation
    assert "question number 2" not in conversation


def test_a_finnish_question_is_also_searched_in_english(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    api.stub.english_query = "report templates"

    events = ask(api, "Mistä löydän raporttipohjat?")
    sources = events[0][1]
    assert sources["language"] == "fi"
    assert sources["english_query"] == "report templates"
    assert "searched also with an English query" in sources["notes"]
    assert sources["sections"], "the English query should find the English manual"
    assert "Reporting" in {s["section_path"] for s in sources["sections"]}


def test_an_english_question_is_not_translated(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    ask(api, "How do I import borehole data?")
    assert not [
        r for r in api.stub.requests if "search query" in r["messages"][0]["content"].lower()
    ]


def test_the_answer_language_is_requested(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    api.stub.english_query = "report templates"
    ask(api, "Mistä löydän raporttipohjat?")
    prompt = api.stub.requests[-1]["messages"][1]["content"]
    assert "Answer in Finnish" in prompt


# ------------------------------------------------------- superseded documents


def test_superseded_documents_are_never_cited(api, tmp_path: Path):
    sign_in(api, "admin")
    current = load_manual(api, tmp_path, title="Handbook 2024", name="v2.pdf")
    settings = api.app_module.settings

    # A second copy of the same content, marked superseded.
    from app import search as search_module

    old_pdf = tmp_path / "v1.pdf"
    build_sample_pdf(old_pdf)
    old = ingest_path(
        old_pdf, title="Handbook 2019", status=db.STATUS_SUPERSEDED, settings=settings
    )
    with db.session(settings.db_path) as conn:
        db.update_document(conn, old.doc_id, status=db.STATUS_SUPERSEDED)
    search_module.reset_indexes()

    events = ask(api, "borehole import")
    titles = {s["doc_title"] for s in events[0][1]["sections"]}
    assert "Handbook 2024" in titles
    assert "Handbook 2019" not in titles

    prompt = api.stub.requests[-1]["messages"][1]["content"]
    assert "Handbook 2019" not in prompt

    # An admin can still find it on purpose.
    response = api.post(
        "/api/search", json={"question": "borehole import", "include_superseded": True}
    )
    assert "Handbook 2019" in {s["doc_title"] for s in response.json()["sections"]}


def test_a_plain_user_cannot_search_superseded_documents(api, tmp_path: Path):
    sign_in(api, "engineer")
    settings = api.app_module.settings
    from app import search as search_module

    ingest_path(
        build_sample_pdf(tmp_path / "old.pdf"),
        title="Handbook 2019",
        status=db.STATUS_SUPERSEDED,
        settings=settings,
    )
    search_module.reset_indexes()
    response = api.post(
        "/api/search", json={"question": "borehole import", "include_superseded": True}
    )
    assert response.json()["sections"] == []


# --------------------------------------------------------------- files, pages


def test_files_serves_the_pdf_and_supports_range(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)

    whole = api.get("/files/1")
    assert whole.status_code == 200
    assert whole.headers["content-type"] == "application/pdf"
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.content.startswith(b"%PDF")

    part = api.get("/files/1", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-99/{len(whole.content)}"
    assert part.content == whole.content[:100]

    middle = api.get("/files/1", headers={"Range": "bytes=100-199"})
    assert middle.status_code == 206
    assert middle.content == whole.content[100:200]


def test_files_rejects_an_unknown_document(api):
    sign_in(api)
    assert api.get("/files/999").status_code == 404


def test_page_image_is_rendered_and_cached(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    settings = api.app_module.settings

    response = api.get("/api/page/1/6.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    cached = list(settings.pagecache_dir.glob("1-6-*.png"))
    assert len(cached) == 1
    stamp = cached[0].stat().st_mtime_ns
    again = api.get("/api/page/1/6.png")
    assert again.status_code == 200
    assert cached[0].stat().st_mtime_ns == stamp, "the page was rendered twice"


def test_page_image_past_the_end_is_a_404(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    assert api.get("/api/page/1/999.png").status_code == 404


def test_api_docs_lists_only_current_documents(api, tmp_path: Path):
    sign_in(api)
    settings = api.app_module.settings
    load_manual(api, tmp_path, title="Current handbook", name="cur.pdf")
    ingest_path(
        build_sample_pdf(tmp_path / "old.pdf"),
        title="Old handbook",
        status=db.STATUS_SUPERSEDED,
        settings=settings,
    )
    titles = {d["title"] for d in api.get("/api/docs").json()["documents"]}
    assert titles == {"Current handbook"}


# ------------------------------------------------------------------- feedback


def test_feedback_is_stored_against_the_question(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    events = ask(api, "borehole import")
    question_id = events[-1][1]["question_id"]

    response = api.post(
        "/api/feedback",
        json={"question_id": question_id, "rating": "down", "comment": "wrong page"},
    )
    assert response.status_code == 200

    settings = api.app_module.settings
    with db.session(settings.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM feedback WHERE question_id = ?", (question_id,)
        ).fetchone()
        question = conn.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
    assert row["rating"] == "down"
    assert row["comment"] == "wrong page"
    assert question["username"] == "engineer"
    assert question["source_ids"]
    assert question["latency_ms"] >= 0


def test_feedback_rating_is_validated(api, tmp_path: Path):
    sign_in(api)
    load_manual(api, tmp_path)
    question_id = ask(api, "borehole import")[-1][1]["question_id"]
    response = api.post(
        "/api/feedback", json={"question_id": question_id, "rating": "sideways"}
    )
    assert response.status_code == 422


def test_feedback_on_an_unknown_question_is_a_404(api):
    sign_in(api)
    assert api.post(
        "/api/feedback", json={"question_id": 4242, "rating": "up"}
    ).status_code == 404


# ---------------------------------------------------------------------- admin


def test_admin_endpoints_refuse_a_plain_user(api):
    sign_in(api, "engineer")
    assert api.get("/api/admin/documents").status_code == 403
    assert api.post("/api/admin/reindex/1").status_code == 403
    assert api.delete("/api/admin/documents/1").status_code == 403


def test_admin_upload_indexes_the_document(api, tmp_path: Path):
    sign_in(api, "admin")
    pdf = build_sample_pdf(tmp_path / "upload.pdf")
    response = api.post(
        "/api/admin/upload",
        files={"file": ("upload.pdf", pdf.read_bytes(), "application/pdf")},
        data={
            "title": "Uploaded handbook",
            "product": "Other Bentley product",
            "version": "2.0",
            "doc_status": "current",
            "licence_confirmed": "true",
        },
    )
    assert response.status_code == 201, response.text
    document = response.json()["document"]
    assert document["ingest_status"] == "ready"
    assert document["page_count"] == 9
    assert document["chunk_count"] > 0
    assert document["licence_confirmed_by"] == "admin"
    assert document["licence_confirmed_at"]
    assert document["uploaded_by"] == "admin"


def test_admin_upload_requires_the_licence_checkbox(api, tmp_path: Path):
    sign_in(api, "admin")
    pdf = build_sample_pdf(tmp_path / "upload.pdf")
    response = api.post(
        "/api/admin/upload",
        files={"file": ("upload.pdf", pdf.read_bytes(), "application/pdf")},
        data={"title": "Uploaded handbook", "doc_status": "current"},
    )
    assert response.status_code == 400
    assert "licence" in response.json()["detail"].lower()


def test_admin_upload_rejects_a_file_that_is_not_a_pdf(api):
    sign_in(api, "admin")
    response = api.post(
        "/api/admin/upload",
        files={"file": ("notes.pdf", b"PK\x03\x04 a zip in disguise", "application/pdf")},
        data={"title": "Not a manual", "doc_status": "current", "licence_confirmed": "true"},
    )
    assert response.status_code == 400
    assert "not a PDF" in response.json()["detail"]


def test_a_broken_pdf_shows_failed_and_the_other_documents_keep_working(
    api, tmp_path: Path
):
    sign_in(api, "admin")
    load_manual(api, tmp_path, title="Good handbook", name="good.pdf")

    broken = build_broken_pdf(tmp_path / "broken.pdf")
    response = api.post(
        "/api/admin/upload",
        files={"file": ("broken.pdf", broken.read_bytes(), "application/pdf")},
        data={"title": "Broken handbook", "doc_status": "current", "licence_confirmed": "true"},
    )
    assert response.status_code == 201
    document = response.json()["document"]
    assert document["ingest_status"] == "failed"
    assert document["ingest_error"]

    listing = api.get("/api/admin/documents").json()["documents"]
    states = {d["title"]: d["ingest_status"] for d in listing}
    assert states == {"Good handbook": "ready", "Broken handbook": "failed"}

    # The app is still answering from the manual that did work.
    events = ask(api, "borehole import")
    assert events[0][1]["sections"]
    assert events[-1][1]["not_found"] is False
    assert api.get("/healthz").json()["library"]["failed_documents"] == 1


def test_admin_can_reindex_and_delete(api, tmp_path: Path):
    sign_in(api, "admin")
    result = load_manual(api, tmp_path)
    settings = api.app_module.settings

    assert api.get("/api/page/1/6.png").status_code == 200
    assert list(settings.pagecache_dir.glob("1-*.png"))

    reindexed = api.post(f"/api/admin/reindex/{result.doc_id}")
    assert reindexed.status_code == 200
    assert reindexed.json()["document"]["ingest_status"] == "ready"
    assert not list(settings.pagecache_dir.glob("1-*.png")), "page cache should be cleared"

    deleted = api.delete(f"/api/admin/documents/{result.doc_id}")
    assert deleted.status_code == 200
    assert api.get("/api/admin/documents").json()["documents"] == []
    assert not list(settings.manuals_dir.glob("*.pdf"))
    assert api.post("/api/search", json={"question": "borehole"}).json()["sections"] == []


def test_admin_can_mark_a_document_superseded(api, tmp_path: Path):
    sign_in(api, "admin")
    result = load_manual(api, tmp_path)
    response = api.post(
        f"/api/admin/documents/{result.doc_id}", data={"doc_status": "superseded"}
    )
    assert response.status_code == 200
    assert response.json()["document"]["status"] == "superseded"
    assert api.post("/api/search", json={"question": "borehole"}).json()["sections"] == []


def test_reindex_of_an_unknown_document_is_a_404(api):
    sign_in(api, "admin")
    assert api.post("/api/admin/reindex/999").status_code == 404
    assert api.delete("/api/admin/documents/999").status_code == 404


# --------------------------------------------------------------------- health


def test_healthz_reports_database_model_and_embeddings(api, tmp_path: Path):
    load_manual(api, tmp_path)
    response = api.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"]["ok"] is True
    assert payload["model"]["ok"] is True
    assert payload["embeddings"]["ok"] is True
    assert payload["library"]["documents"] == 1
    assert payload["library"]["chunks"] > 0
    assert payload["library"]["failed_documents"] == 0


def test_healthz_marks_a_model_that_is_down(api):
    api.stub.chat_status = 503
    api.stub.embed_status = 503
    payload = api.get("/healthz").json()
    assert payload["status"] == "ok", "the app itself is still up"
    assert payload["model"]["ok"] is False
    assert payload["embeddings"]["ok"] is False


def test_healthz_needs_no_session(api):
    assert api.get("/healthz").status_code == 200

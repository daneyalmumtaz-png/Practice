"""Test fixtures.

Every PDF used by the tests is generated here with PyMuPDF. No real manual is
read, copied or referenced by the test suite.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FOOTER = "Example Engineering Handbook - internal test fixture - page {n} of 9"
BODY_SIZE = 10.0
HEAD_SIZE = 16.0


def _text(page: pymupdf.Page, y: float, text: str, size: float = BODY_SIZE,
          bold: bool = False) -> float:
    font = "hebo" if bold else "helv"
    for line in text.split("\n"):
        page.insert_text((60, y), line, fontsize=size, fontname=font)
        y += size * 1.45
    return y


def _footer(page: pymupdf.Page, n: int) -> None:
    # The same line on every page, with only the digits changing, so the
    # boilerplate rule has to normalise digits to spot it.
    page.insert_text((60, 800), FOOTER.format(n=n), fontsize=8, fontname="helv")


def _grid_table(page: pymupdf.Page, top: float) -> None:
    """A bordered table, so find_tables() has lines to work with."""
    rows = [
        ["Column", "Unit", "Note"],
        ["Depth", "m", "below ground level"],
        ["Water content", "percent", "laboratory value"],
        ["Sample number", "text", "free text field"],
    ]
    x = [60, 200, 300, 470]
    row_h = 22
    for r in range(len(rows) + 1):
        y = top + r * row_h
        page.draw_line(pymupdf.Point(x[0], y), pymupdf.Point(x[-1], y))
    for cx in x:
        page.draw_line(
            pymupdf.Point(cx, top), pymupdf.Point(cx, top + len(rows) * row_h)
        )
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text(
                (x[c] + 4, top + r * row_h + 15),
                cell,
                fontsize=9,
                fontname="hebo" if r == 0 else "helv",
            )


def build_sample_pdf(path: Path) -> Path:
    """A nine page fixture exercising every part of the ingest pipeline.

    Page 1 (label i)    title page
    Page 2 (label ii)   table of contents, dot leaders, must be skipped
    Page 3 (label 1)    section 1 body text
    Page 4 (label 2)    section 1.1 body text, Finnish sentence included
    Page 5 (label 3)    section 2 body text
    Page 6 (label 4)    section 2.1 body text with the bordered table and a caption
    Page 7 (label 5)    section 2.2 long body text, forces more than one chunk
    Page 8 (label 6)    section 3 body text
    Page 9 (label 7)    index, page references, must be skipped
    """
    doc = pymupdf.open()
    for _ in range(9):
        doc.new_page(width=595, height=842)

    p = doc[0]
    _text(p, 120, "Example Engineering Handbook", HEAD_SIZE + 6, bold=True)
    _text(p, 170, "A synthetic document written for the test suite.")
    _footer(p, 1)

    p = doc[1]
    _text(p, 90, "Contents", HEAD_SIZE, bold=True)
    _text(
        p,
        130,
        "1 Getting started . . . . . . . . . . . . . . . . 1\n"
        "1.1 Opening a project . . . . . . . . . . . . . 2\n"
        "2 Importing data . . . . . . . . . . . . . . . . 3\n"
        "2.1 Borehole import . . . . . . . . . . . . . . 4\n"
        "2.2 Field descriptions . . . . . . . . . . . . 5\n"
        "3 Reporting . . . . . . . . . . . . . . . . . . 6",
    )
    _footer(p, 2)

    p = doc[2]
    _text(p, 90, "1 Getting started", HEAD_SIZE, bold=True)
    _text(
        p,
        130,
        "This chapter explains how to start the example program and open an\n"
        "existing project. The program stores every project in a single folder\n"
        "together with its survey data and its drawing settings.",
    )
    _footer(p, 3)

    p = doc[3]
    _text(p, 90, "1.1 Opening a project", HEAD_SIZE, bold=True)
    _text(
        p,
        130,
        "Choose the project folder and confirm the coordinate system before any\n"
        "survey data is loaded. The coordinate system cannot be changed later\n"
        "without rebuilding the project.\n"
        "Projektin kansio valitaan ensin ja koordinaatisto vahvistetaan.",
    )
    _footer(p, 4)

    p = doc[4]
    _text(p, 90, "2 Importing data", HEAD_SIZE, bold=True)
    _text(
        p,
        130,
        "Survey data is imported one file at a time. The importer checks the\n"
        "column names against the field list before it writes anything into the\n"
        "project database.\n"
        "Pohjatutkimustiedot tuodaan yksi tiedosto kerrallaan.",
    )
    _footer(p, 5)

    p = doc[5]
    _text(p, 90, "2.1 Borehole import", HEAD_SIZE, bold=True)
    y = _text(
        p,
        130,
        "Borehole records are read from a delimited text file. Each record needs\n"
        "a depth and a sample number.",
    )
    _text(p, y + 10, "Table 1 Fields read from a borehole file", 9)
    _grid_table(p, y + 30)
    _footer(p, 6)

    p = doc[6]
    _text(p, 90, "2.2 Field descriptions", HEAD_SIZE, bold=True)
    long_body = "\n".join(
        f"Field description line {i} explains one column of the import file and "
        f"what the importer does when the column is missing or empty."
        for i in range(1, 22)
    )
    _text(p, 130, long_body)
    _footer(p, 7)

    p = doc[7]
    _text(p, 90, "3 Reporting", HEAD_SIZE, bold=True)
    _text(
        p,
        130,
        "Reports are produced from a template. The template controls which\n"
        "fields appear and in what order.",
    )
    _footer(p, 8)

    p = doc[8]
    _text(p, 90, "Index", HEAD_SIZE, bold=True)
    _text(
        p,
        130,
        "Borehole import, 4\n"
        "Coordinate system, 2, 3\n"
        "Field descriptions, 5\n"
        "Import, 3, 4\n"
        "Project folder, 2\n"
        "Reporting, 6\n"
        "Sample number, 4\n"
        "Survey data, 3, 5\n"
        "Template, 6\n"
        "Water content, 4",
    )
    _footer(p, 9)

    # Front matter in roman numerals, body from 1. The printed label and the PDF
    # page number therefore disagree on every page, which is the point.
    doc.set_page_labels(
        [
            {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
            {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1},
        ]
    )
    doc.set_toc(
        [
            [1, "Getting started", 3],
            [2, "Opening a project", 4],
            [1, "Importing data", 5],
            [2, "Borehole import", 6],
            [2, "Field descriptions", 7],
            [1, "Reporting", 8],
        ]
    )
    doc.save(str(path))
    doc.close()
    return path


def build_no_bookmarks_pdf(path: Path) -> Path:
    """Same shape, no bookmarks, so section paths must come from the headings."""
    doc = pymupdf.open()
    for _ in range(4):
        doc.new_page(width=595, height=842)

    p = doc[0]
    _text(p, 90, "3 Reporting", HEAD_SIZE, bold=True)
    _text(p, 130, "Reports are produced from a template stored in the project.")
    p = doc[1]
    _text(p, 90, "3.1 Report templates", HEAD_SIZE - 2, bold=True)
    _text(p, 130, "A template lists the fields that appear in the output.")
    p = doc[2]
    _text(p, 90, "3.2.1 Editing a template", HEAD_SIZE - 4, bold=True)
    _text(p, 130, "Open the template and change the field order.")
    p = doc[3]
    _text(p, 130, "The change takes effect the next time a report is produced.")
    doc.save(str(path))
    doc.close()
    return path


def build_broken_pdf(path: Path) -> Path:
    """A file that claims to be a PDF and is not. Ingest must record it failed."""
    path.write_bytes(b"%PDF-1.7\nthis file is deliberately truncated and invalid\n")
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A temporary data directory with the app's settings pointed at it."""
    from app import config as config_module
    from app import db

    data = tmp_path / "data"
    for sub in ("manuals", "vectors", "pagecache"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("DB_PATH", str(data / "app.db"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-not-for-production")
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    config_module.get_settings.cache_clear()
    settings = config_module.get_settings()
    db.init_db(settings.db_path)

    from app import search as search_module

    search_module.reset_indexes()
    yield settings
    search_module.reset_indexes()
    config_module.get_settings.cache_clear()


@pytest.fixture
def sample_pdf(workspace, tmp_path: Path) -> Path:
    return build_sample_pdf(tmp_path / "sample-handbook.pdf")


@pytest.fixture
def no_bookmarks_pdf(workspace, tmp_path: Path) -> Path:
    return build_no_bookmarks_pdf(tmp_path / "no-bookmarks.pdf")


# Concept dimensions shared by the English and Finnish words for the same thing.
# This is a stand-in for a multilingual embedding model, good enough to exercise
# the vector half of the search and the fusion. It says nothing about the real
# quality of bge-m3.
FAKE_CONCEPTS: tuple[tuple[str, ...], ...] = (
    ("borehole", "boreholes", "kairaus", "pohjatutkimus", "pohjatutkimustieto"),
    ("report", "reports", "reporting", "raportti", "raporttipohja", "template"),
    ("coordinate", "coordinates", "koordinaatisto", "projection"),
    ("import", "imported", "importer", "importing", "tuoda", "tuonti"),
    ("field", "fields", "column", "columns", "kentta", "sarake"),
    ("project", "projects", "folder", "projekti", "kansio"),
)


def fake_embedder(concepts: tuple[tuple[str, ...], ...] = FAKE_CONCEPTS):
    """Return a replacement for app.embed.embed_texts."""
    import numpy as np

    from app.lang import words

    def embed_texts(texts, settings=None):
        rows = []
        for text in texts:
            present = set(words(text))
            # Finnish compounds are matched on a prefix, as a real subword model
            # would, so "pohjatutkimustiedot" reaches the borehole dimension.
            vector = []
            for group in concepts:
                score = 0.0
                for term in group:
                    if term in present or any(w.startswith(term) for w in present):
                        score = 1.0
                        break
                vector.append(score)
            if not any(vector):
                vector = [0.01] * len(concepts)
            rows.append(vector)
        matrix = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    return embed_texts


# --------------------------------------------------------------------- stub model

class StubModel:
    """State of the stand-in model server, writable from a test."""

    def __init__(self) -> None:
        self.base_url = ""
        self.answer = "Summary line [1]\n\n1. First step [1]\n2. Second step [2]"
        self.rewrite = ""
        self.english_query = ""
        self.chat_status = 200
        self.chat_error = ""
        self.embed_status = 200
        self.requests: list[dict] = []
        self.chat_calls = 0
        self.embed_calls = 0

    def reply_for(self, messages: list[dict]) -> str:
        system = (messages[0].get("content") or "") if messages else ""
        if "rewrite" in system.lower():
            return self.rewrite or (messages[-1].get("content") or "")
        if "search query" in system.lower():
            return self.english_query
        return self.answer


@pytest.fixture
def stub_model():
    """A real HTTP server that speaks the two APIs the app calls.

    Chat completions in the OpenAI shape, streaming and not, and the Ollama
    embeddings endpoint. Running it over real TCP means the app's own HTTP
    client, SSE parsing and timeouts are exercised, not mocked away.
    """
    import json as json_module
    import socket
    import threading

    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    state = StubModel()

    async def chat(request):
        payload = await request.json()
        state.requests.append(payload)
        state.chat_calls += 1
        if state.chat_status != 200:
            return JSONResponse(
                {"error": state.chat_error or "stub failure"},
                status_code=state.chat_status,
            )
        text = state.reply_for(payload.get("messages") or [])
        if not payload.get("stream"):
            return JSONResponse(
                {
                    "id": "stub",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": text}}
                    ],
                }
            )

        async def events():
            # Split into small pieces so the test sees a real token stream.
            step = 12
            for start in range(0, len(text), step):
                chunk = {
                    "choices": [{"index": 0, "delta": {"content": text[start:start + step]}}]
                }
                yield f"data: {json_module.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    async def models(request):
        if state.chat_status != 200:
            return JSONResponse({"error": "down"}, status_code=state.chat_status)
        return JSONResponse({"data": [{"id": "stub-model"}]})

    async def embeddings(request):
        payload = await request.json()
        state.embed_calls += 1
        if state.embed_status != 200:
            return JSONResponse({"error": "down"}, status_code=state.embed_status)
        texts = payload.get("input")
        if isinstance(texts, str):
            texts = [texts]
        if texts is None:
            texts = [payload.get("prompt") or ""]
        vectors = fake_embedder()(texts)
        return JSONResponse({"embeddings": [row.tolist() for row in vectors]})

    stub = Starlette(
        routes=[
            Route("/v1/chat/completions", chat, methods=["POST"]),
            Route("/v1/models", models, methods=["GET"]),
            Route("/api/embed", embeddings, methods=["POST"]),
        ]
    )

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(stub, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        pytest.fail("the stub model server did not start")

    state.base_url = f"http://127.0.0.1:{port}"
    yield state
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def api(workspace, stub_model, monkeypatch: pytest.MonkeyPatch):
    """A TestClient for the app, pointed at the stub model, with two users.

    app.main reads its settings at import time, so it is reloaded here after the
    environment is in place.
    """
    import importlib

    from app import auth
    from app import config as config_module
    from app import search as search_module
    from fastapi.testclient import TestClient

    password_hash = auth.hash_password(TEST_PASSWORD)
    monkeypatch.setenv("LLM_BASE_URL", stub_model.base_url + "/v1")
    monkeypatch.setenv("EMBED_BASE_URL", stub_model.base_url)
    monkeypatch.setenv("LLM_API_KEY", "stub-key")
    monkeypatch.setenv("LLM_TIMEOUT_S", "20")
    monkeypatch.setenv("EMBED_TIMEOUT_S", "20")
    monkeypatch.setenv(
        "USERS_YAML",
        yaml_users(
            [("admin", "admin", password_hash), ("engineer", "user", password_hash)]
        ),
    )
    config_module.get_settings.cache_clear()
    auth.set_backend(None)
    search_module.reset_indexes()

    import app.main as main_module

    importlib.reload(main_module)
    client = TestClient(main_module.app)
    client.app_module = main_module          # type: ignore[attr-defined]
    client.stub = stub_model                 # type: ignore[attr-defined]
    with client:
        yield client
    auth.set_backend(None)
    search_module.reset_indexes()
    config_module.get_settings.cache_clear()


TEST_PASSWORD = "test-password-1234"


def yaml_users(entries: list[tuple[str, str, str]]) -> str:
    lines = ["users:"]
    for username, role, password_hash in entries:
        lines.append(f"  - username: {username}")
        lines.append(f"    role: {role}")
        lines.append(f'    password_hash: "{password_hash}"')
    return "\n".join(lines) + "\n"


def sign_in(client, username: str = "engineer", password: str | None = None):
    response = client.post(
        "/login",
        data={"username": username, "password": password or TEST_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response

"""The web application: pages, search, streamed answers, admin, health.

The default bind address is 127.0.0.1. In Docker the app listens on 0.0.0.0
inside the container and is reached only through the reverse proxy, which is the
only thing publishing a port.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import answer as answer_module
from . import auth, db, embed, ingest, lang, llm, pages, search as search_module
from .config import Settings, get_settings
from .logsetup import get_logger, setup_logging
from .ratelimit import RateLimiter

log = get_logger("app")

# Paths reachable without a session: the login form itself, its stylesheet, and
# the container healthcheck. Everything else requires a signed session cookie.
OPEN_PATHS = {"/login", "/logout", "/healthz"}
OPEN_PREFIXES = ("/static/",)

setup_logging()
settings = get_settings()
db.init_db(settings.db_path)
limiter = RateLimiter(settings.rate_limit_per_minute)


@contextlib.asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    # Load the lemmatiser dictionaries now, not on the first question: simplemma
    # reads its Finnish data on first use and that showed up as several seconds
    # on the first search of a cold process.
    await asyncio.to_thread(lang.warmup)
    backend = auth.get_backend(settings)
    users = getattr(backend, "count", None)
    if users == 0:
        log.warning(
            "no_users_configured",
            extra={"hint": "set USERS_YAML or create users.yaml; nobody can sign in"},
        )
    task = asyncio.create_task(retention_sweep())
    log.info(
        "started",
        extra={
            "host": settings.host,
            "port": settings.port,
            "llm_model": settings.llm_model,
            "embed_model": settings.embed_model,
            "retention_days": settings.retention_days,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "users": users,
        },
    )
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title=settings.app_name,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")


# ------------------------------------------------------------------- middleware


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """A JSON endpoint accepts at most MAX_JSON_BYTES, default 16 kB."""
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT"):
        if not request.url.path.startswith("/api/admin/upload"):
            declared = request.headers.get("content-length")
            if declared and declared.isdigit():
                if int(declared) > settings.max_json_bytes:
                    return JSONResponse(
                        {"detail": "request body too large"},
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
    return await call_next(request)


@app.middleware("http")
async def require_session(request: Request, call_next):
    """Login is required for every page and API call.

    /healthz is the one exception, because the container healthcheck runs before
    anyone has signed in. It returns status flags only, no configuration values.
    """
    path = request.url.path
    if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
        return await call_next(request)
    if auth.read_session(request, settings) is None:
        if path.startswith("/api/") or path.startswith("/files/"):
            return JSONResponse({"detail": "sign in required"}, status_code=401)
        return RedirectResponse(url="/login", status_code=303)
    return await call_next(request)


# ------------------------------------------------------------------ page serving


def render_template(name: str, user: auth.User | None = None) -> HTMLResponse:
    """Serve a static page with the configuration injected.

    The HTML files contain no product name, greeting or example question. They
    read window.APP_CONFIG, which is written in here from config.yaml.
    """
    path = settings.static_dir / name
    html = path.read_text(encoding="utf-8")
    config = {
        "appName": settings.app_name,
        "disclaimer": settings.disclaimer,
        "greeting": settings.greeting,
        "examples": list(settings.example_questions),
        "products": list(settings.products),
        "user": {"username": user.username, "role": user.role} if user else None,
    }
    block = (
        "<script>window.APP_CONFIG="
        + json.dumps(config, ensure_ascii=False)
        + ";</script>"
    )
    return HTMLResponse(html.replace("<!--APP_CONFIG-->", block))


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    if auth.read_session(request, settings) is not None:
        return RedirectResponse(url="/", status_code=303)
    return render_template("login.html")


@app.post("/login")
async def login_submit(
    request: Request, username: str = Form(""), password: str = Form("")
) -> Response:
    user = auth.get_backend(settings).authenticate(username, password)
    if user is None:
        log.warning("login_failed", extra={"username": username[:64]})
        html = (settings.static_dir / "login.html").read_text(encoding="utf-8")
        config = {
            "appName": settings.app_name,
            "disclaimer": settings.disclaimer,
            "error": "Wrong username or password.",
        }
        block = (
            "<script>window.APP_CONFIG="
            + json.dumps(config, ensure_ascii=False)
            + ";</script>"
        )
        return HTMLResponse(html.replace("<!--APP_CONFIG-->", block), status_code=401)
    response = RedirectResponse(url="/", status_code=303)
    auth.issue_session(response, user, settings)
    log.info("login_ok", extra={"username": user.username, "role": user.role})
    return response


@app.post("/logout")
async def logout() -> Response:
    response = RedirectResponse(url="/login", status_code=303)
    auth.clear_session(response)
    return response


@app.get("/", response_class=HTMLResponse)
async def chat_page(user: auth.User = Depends(auth.require_user)) -> Response:
    return render_template("index.html", user)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(user: auth.User = Depends(auth.require_admin)) -> Response:
    return render_template("admin.html", user)


# ------------------------------------------------------------------ API: search


class SearchRequest(BaseModel):
    question: str = Field("", max_length=2000)
    include_superseded: bool = False


class Turn(BaseModel):
    question: str = Field("", max_length=2000)
    answer: str = Field("", max_length=4000)


class AskRequest(BaseModel):
    question: str = Field("", max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=10)


class FeedbackRequest(BaseModel):
    question_id: int
    rating: str = Field(..., pattern="^(up|down)$")
    comment: str = Field("", max_length=2000)


def hit_payload(number: int, hit: search_module.Hit) -> dict[str, Any]:
    return {
        "n": number,
        "chunk_id": hit.chunk_id,
        "doc_id": hit.doc_id,
        "doc_title": hit.doc_title,
        "product": hit.product,
        "version": hit.version,
        "section_path": hit.section_path,
        "page_label": hit.page_label,
        "pdf_page": hit.pdf_page,
        "chunk_type": hit.chunk_type,
        "snippet": hit.snippet,
        "highlights": hit.highlights,
        "file_url": f"/files/{hit.doc_id}#page={hit.pdf_page}",
        "page_image_url": f"/api/page/{hit.doc_id}/{hit.pdf_page}.png",
    }


@app.post("/api/search")
async def api_search(
    payload: SearchRequest, user: auth.User = Depends(auth.require_user)
) -> JSONResponse:
    """The governing sections, with no model call, so it answers immediately."""
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    # Superseded manuals are visible to admins here, and never cited in answers.
    include_superseded = payload.include_superseded and user.is_admin
    outcome = search_module.search(
        [question], settings=settings, include_superseded=include_superseded
    )
    return JSONResponse(
        {
            "question": question,
            "language": outcome.language,
            "sections": [hit_payload(i, h) for i, h in enumerate(outcome.hits, 1)],
            "used_embeddings": outcome.used_embeddings,
            "elapsed_ms": outcome.elapsed_ms,
        }
    )


# --------------------------------------------------------------------- API: ask


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def ask_events(question: str, history: list[Turn], user: auth.User) -> AsyncIterator[str]:
    started = time.perf_counter()
    turns = [answer_module.Turn(t.question, t.answer) for t in history]

    try:
        plan = await answer_module.plan_queries(question, turns, settings)
        outcome = search_module.search(plan.queries, settings=settings)
        sources = search_module.select_sources(
            outcome.hits, settings.retrieval.token_budget
        )
    except Exception as exc:                       # noqa: BLE001 - reported to the page
        log.error("ask_search_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        yield sse("error", {"detail": "search failed"})
        return

    yield sse(
        "sources",
        {
            "sections": [hit_payload(i, h) for i, h in enumerate(outcome.hits, 1)],
            "sent_to_model": [s.number for s in sources],
            "question": plan.question,
            "original_question": plan.original,
            "language": plan.language,
            "rewritten": plan.rewritten,
            "english_query": plan.english_query,
            "notes": plan.notes,
            "used_embeddings": outcome.used_embeddings,
            "search_ms": outcome.elapsed_ms,
        },
    )

    collected: list[str] = []
    failed = ""
    if not sources:
        # Nothing was found, so there is nothing for the model to answer from.
        text = answer_module.not_found_message(plan.language, sources)
        collected.append(text)
        yield sse("token", {"t": text})
    else:
        messages = answer_module.build_messages(
            plan.question, sources, language=plan.language, settings=settings
        )
        try:
            async for piece in llm.stream(messages, settings=settings):
                collected.append(piece)
                yield sse("token", {"t": piece})
        except llm.LLMError as exc:
            failed = str(exc)
            log.error("llm_failed", extra={"error": failed})
            yield sse(
                "error",
                {
                    "detail": "The language model could not be reached. The sections "
                    "above are still the best matches.",
                },
            )

    text = "".join(collected)
    latency_ms = int((time.perf_counter() - started) * 1000)
    not_found = bool(sources) is False or answer_module.looks_not_found(text)

    question_id = 0
    try:
        with db.session(settings.db_path) as conn:
            question_id = db.log_question(
                conn,
                username=user.username,
                question=plan.question,
                language=plan.language,
                source_ids=",".join(str(s.hit.chunk_id) for s in sources),
                answer_chars=len(text),
                latency_ms=latency_ms,
                not_found=not_found,
            )
    except Exception as exc:                       # noqa: BLE001 - logging must not fail a reply
        log.error("question_log_failed", extra={"error": f"{type(exc).__name__}: {exc}"})

    log.info(
        "answered",
        extra={
            "username": user.username,
            "language": plan.language,
            "sources": len(sources),
            "answer_chars": len(text),
            "latency_ms": latency_ms,
            "not_found": not_found,
            "llm_error": failed,
        },
    )
    yield sse(
        "done",
        {
            "question_id": question_id,
            "source_count": len(sources),
            "answer_chars": len(text),
            "latency_ms": latency_ms,
            "search_ms": outcome.elapsed_ms,
            "not_found": not_found,
        },
    )


@app.post("/api/ask")
async def api_ask(
    payload: AskRequest, user: auth.User = Depends(auth.require_user)
) -> Response:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    allowed, wait = limiter.check(user.username)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"too many questions, try again in {int(wait) + 1} s",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    return StreamingResponse(
        ask_events(question, payload.history, user),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------- API: files, pages


def document_file(doc_id: int) -> tuple[Path, str]:
    """The stored path of a document in the library, and its title.

    Only documents in the library can be served, and only from the manuals
    directory, so no path from a request ever reaches the filesystem.
    """
    with db.session(settings.db_path) as conn:
        row = db.get_document(conn, doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such document")
    path = (settings.manuals_dir / row["filename"]).resolve()
    if path.parent != settings.manuals_dir.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="document file is missing")
    return path, row["title"]


@app.get("/files/{doc_id}")
async def serve_file(
    doc_id: int, user: auth.User = Depends(auth.require_user)
) -> FileResponse:
    path, title = document_file(doc_id)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            # Range support lets the browser's PDF viewer jump straight to the
            # cited page instead of downloading the whole manual first.
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'inline; filename="{doc_id}.pdf"',
            "X-Document-Title": title.encode("ascii", "replace").decode("ascii"),
        },
    )


@app.get("/api/page/{doc_id}/{pdf_page}.png")
async def serve_page_image(
    doc_id: int, pdf_page: int, user: auth.User = Depends(auth.require_user)
) -> FileResponse:
    path, _ = document_file(doc_id)
    try:
        image = await asyncio.to_thread(
            pages.render_page, doc_id, pdf_page, path, settings
        )
    except pages.PageRenderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        image, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"}
    )


@app.get("/api/docs")
async def api_docs(user: auth.User = Depends(auth.require_user)) -> JSONResponse:
    with db.session(settings.db_path) as conn:
        rows = db.list_documents(conn, only_current=True)
    return JSONResponse(
        {
            "documents": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "product": r["product"],
                    "version": r["version"],
                    "page_count": r["page_count"],
                    "chunk_count": r["chunk_count"],
                }
                for r in rows
            ]
        }
    )


# ----------------------------------------------------------------- API: feedback


@app.post("/api/feedback")
async def api_feedback(
    payload: FeedbackRequest, user: auth.User = Depends(auth.require_user)
) -> JSONResponse:
    with db.session(settings.db_path) as conn:
        row = conn.execute(
            "SELECT id, username FROM questions WHERE id = ?", (payload.question_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such question")
        if row["username"] != user.username and not user.is_admin:
            raise HTTPException(status_code=403, detail="not your question")
        feedback_id = db.add_feedback(
            conn,
            question_id=payload.question_id,
            rating=payload.rating,
            comment=payload.comment,
        )
    log.info(
        "feedback",
        extra={
            "username": user.username,
            "question_id": payload.question_id,
            "rating": payload.rating,
            "has_comment": bool(payload.comment.strip()),
        },
    )
    return JSONResponse({"feedback_id": feedback_id})


# -------------------------------------------------------------------- API: admin


def document_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "product": row["product"],
        "version": row["version"],
        "status": row["status"],
        "filename": row["filename"],
        "page_count": row["page_count"],
        "chunk_count": row["chunk_count"],
        "ingest_status": row["ingest_status"],
        "ingest_error": row["ingest_error"],
        "uploaded_at": row["uploaded_at"],
        "uploaded_by": row["uploaded_by"],
        "licence_confirmed_by": row["licence_confirmed_by"],
        "licence_confirmed_at": row["licence_confirmed_at"],
        "indexed_at": row["indexed_at"],
    }


@app.get("/api/admin/documents")
async def admin_documents(user: auth.User = Depends(auth.require_admin)) -> JSONResponse:
    with db.session(settings.db_path) as conn:
        rows = db.list_documents(conn)
    return JSONResponse({"documents": [document_payload(r) for r in rows]})


@app.post("/api/admin/upload")
async def admin_upload(
    user: auth.User = Depends(auth.require_admin),
    file: UploadFile = Form(...),
    title: str = Form(...),
    product: str = Form(""),
    version: str = Form(""),
    doc_status: str = Form(db.STATUS_CURRENT),
    licence_confirmed: str = Form(""),
) -> JSONResponse:
    if licence_confirmed.strip().lower() not in ("1", "true", "on", "yes"):
        raise HTTPException(
            status_code=400,
            detail="Tick the licence confirmation before uploading.",
        )
    data = await file.read(settings.max_upload_bytes + 1)
    try:
        doc_id = ingest.register_upload(
            data=data,
            filename=file.filename or title,
            title=title,
            product=product,
            version=version,
            status=doc_status,
            uploaded_by=user.username,
            licence_confirmed_by=user.username,
            settings=settings,
        )
    except ingest.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await asyncio.to_thread(ingest.index_document, doc_id, settings=settings)
    search_module.reset_indexes()
    with db.session(settings.db_path) as conn:
        row = db.get_document(conn, doc_id)
    return JSONResponse(
        {"document": document_payload(row), "error": result.error}, status_code=201
    )


@app.post("/api/admin/reindex/{doc_id}")
async def admin_reindex(
    doc_id: int, user: auth.User = Depends(auth.require_admin)
) -> JSONResponse:
    try:
        result = await asyncio.to_thread(
            ingest.index_document, doc_id, settings=settings, force=True
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such document") from exc
    pages.drop_cached_pages(doc_id, settings)
    search_module.reset_indexes()
    with db.session(settings.db_path) as conn:
        row = db.get_document(conn, doc_id)
    return JSONResponse({"document": document_payload(row), "error": result.error})


@app.post("/api/admin/documents/{doc_id}")
async def admin_update(
    doc_id: int,
    user: auth.User = Depends(auth.require_admin),
    title: str = Form(None),
    product: str = Form(None),
    version: str = Form(None),
    doc_status: str = Form(None),
) -> JSONResponse:
    fields: dict[str, Any] = {}
    if title is not None and title.strip():
        fields["title"] = title.strip()
    if product is not None:
        fields["product"] = product.strip()
    if version is not None:
        fields["version"] = version.strip()
    if doc_status is not None:
        if doc_status not in (db.STATUS_CURRENT, db.STATUS_SUPERSEDED):
            raise HTTPException(status_code=400, detail="unknown status")
        fields["status"] = doc_status
    with db.session(settings.db_path) as conn:
        if db.get_document(conn, doc_id) is None:
            raise HTTPException(status_code=404, detail="no such document")
        db.update_document(conn, doc_id, **fields)
        row = db.get_document(conn, doc_id)
    search_module.reset_indexes()
    return JSONResponse({"document": document_payload(row)})


@app.delete("/api/admin/documents/{doc_id}")
async def admin_delete(
    doc_id: int, user: auth.User = Depends(auth.require_admin)
) -> JSONResponse:
    pages.drop_cached_pages(doc_id, settings)
    removed = await asyncio.to_thread(ingest.remove_document, doc_id, settings)
    if not removed:
        raise HTTPException(status_code=404, detail="no such document")
    search_module.reset_indexes()
    log.info("document_removed", extra={"doc_id": doc_id, "by": user.username})
    return JSONResponse({"deleted": doc_id})


# --------------------------------------------------------------------- health


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Status flags for the container healthcheck. No configuration is returned."""
    database_ok = True
    database_detail = "ok"
    documents = 0
    chunks = 0
    failed = 0
    try:
        with db.session(settings.db_path) as conn:
            documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE ingest_status = ?",
                (db.INGEST_FAILED,),
            ).fetchone()[0]
    except Exception as exc:                       # noqa: BLE001
        database_ok = False
        database_detail = f"{type(exc).__name__}"

    model_ok, model_detail = await llm.service_status(settings)
    embed_ok, embed_detail = await asyncio.to_thread(embed.service_status, settings)

    payload = {
        # The app is up as long as its own database works. A model that is down
        # degrades the service; it does not make the container unhealthy.
        "status": "ok" if database_ok else "error",
        "database": {"ok": database_ok, "detail": database_detail},
        "model": {"ok": model_ok, "detail": model_detail},
        "embeddings": {"ok": embed_ok, "detail": embed_detail},
        "library": {
            "documents": documents,
            "chunks": chunks,
            "failed_documents": failed,
        },
    }
    return JSONResponse(payload, status_code=200 if database_ok else 503)


# ------------------------------------------------------------------- background


async def retention_sweep() -> None:
    """Delete question log entries past the retention window, once a day."""
    while True:
        try:
            with db.session(settings.db_path) as conn:
                removed = db.purge_old_questions(conn, settings.retention_days)
            if removed:
                log.info(
                    "retention_sweep",
                    extra={"removed": removed, "retention_days": settings.retention_days},
                )
        except Exception as exc:                   # noqa: BLE001
            log.error("retention_sweep_failed", extra={"error": str(exc)})
        await asyncio.sleep(24 * 3600)


def run() -> None:
    import uvicorn

    # No log configuration is passed: importing this module has already pointed
    # uvicorn's loggers at the JSON handler.
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()

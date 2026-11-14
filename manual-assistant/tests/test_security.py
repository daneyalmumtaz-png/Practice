"""Security and operation tests.

The app will be reachable from the internet, so these are not optional extras.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path

import pytest

from app import auth, db
from app.config import load_settings
from app.ingest import ingest_path
from app.logsetup import JsonFormatter, setup_logging
from app.ratelimit import RateLimiter

from conftest import TEST_PASSWORD, build_sample_pdf, sign_in, yaml_users


# ------------------------------------------------------------------ bind address


def test_default_bind_address_is_loopback(monkeypatch: pytest.MonkeyPatch):
    """Nothing is exposed to the network unless someone asks for it."""
    monkeypatch.delenv("HOST", raising=False)
    assert load_settings().host == "127.0.0.1"


def test_bind_address_can_be_opened_deliberately(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOST", "0.0.0.0")
    assert load_settings().host == "0.0.0.0"


# ------------------------------------------------------------- every route guarded


def test_every_route_requires_a_session(api):
    """Walk the app's own routes rather than a list written by hand.

    A route added later without a session check fails this test.
    """
    from app.main import OPEN_PATHS, OPEN_PREFIXES

    checked = 0
    for route in api.app_module.app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", "")
        if not methods or not path:
            continue
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            continue
        concrete = (
            path.replace("{doc_id}", "1").replace("{pdf_page}", "1")
        )
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            response = api.request(
                method, concrete, json={}, follow_redirects=False
            )
            assert response.status_code in (
                401, 303
            ), f"{method} {concrete} answered {response.status_code} with no session"
            checked += 1
    assert checked >= 10, "the route walk found too few routes to be meaningful"


def test_static_files_and_login_stay_open(api):
    assert api.get("/static/style.css").status_code == 200
    assert api.get("/login").status_code == 200
    assert api.get("/healthz").status_code == 200


# -------------------------------------------------------------------- passwords


def test_passwords_are_stored_as_bcrypt_hashes():
    hashed = auth.hash_password(TEST_PASSWORD)
    assert hashed.startswith("$2b$")
    assert TEST_PASSWORD not in hashed
    assert auth.verify_password(TEST_PASSWORD, hashed)
    assert not auth.verify_password(TEST_PASSWORD + "x", hashed)


def test_a_broken_hash_does_not_raise():
    assert auth.verify_password("anything", "not-a-hash") is False
    assert auth.verify_password("anything", "") is False


def test_an_unknown_role_is_downgraded_to_user(monkeypatch: pytest.MonkeyPatch):
    from app import config as config_module

    hashed = auth.hash_password(TEST_PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("USERS_YAML", yaml_users([("someone", "superuser", hashed)]))
    config_module.get_settings.cache_clear()
    backend = auth.LocalUsersBackend(config_module.get_settings())
    user = backend.authenticate("someone", TEST_PASSWORD)
    assert user is not None
    assert user.role == auth.ROLE_USER
    assert user.is_admin is False
    config_module.get_settings.cache_clear()


def test_a_user_with_no_hash_is_ignored(monkeypatch: pytest.MonkeyPatch):
    from app import config as config_module

    monkeypatch.setenv("SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("USERS_YAML", "users:\n  - username: ghost\n    role: admin\n")
    config_module.get_settings.cache_clear()
    backend = auth.LocalUsersBackend(config_module.get_settings())
    assert backend.count == 0
    assert backend.authenticate("ghost", "") is None
    config_module.get_settings.cache_clear()


# --------------------------------------------------------------------- sessions


def test_session_cookie_is_http_only_and_same_site(api):
    response = sign_in(api)
    header = response.headers["set-cookie"]
    assert auth.SESSION_COOKIE in header
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower()
    # The username is signed into the cookie, not readable from it as plain text.
    assert "engineer" not in header.split(";")[0].split("=", 1)[1]


def test_cookie_is_marked_secure_when_configured(monkeypatch: pytest.MonkeyPatch):
    from fastapi import Response

    from app import config as config_module

    monkeypatch.setenv("SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("COOKIE_SECURE", "1")
    config_module.get_settings.cache_clear()
    settings = config_module.get_settings()
    response = Response()
    auth.issue_session(response, auth.User("someone"), settings)
    assert "secure" in response.headers["set-cookie"].lower()
    config_module.get_settings.cache_clear()


def test_a_tampered_cookie_is_refused(api):
    sign_in(api)
    assert api.get("/", follow_redirects=False).status_code == 200

    token = api.cookies[auth.SESSION_COOKIE]
    api.cookies.set(auth.SESSION_COOKIE, token[:-4] + "AAAA")
    assert api.get("/", follow_redirects=False).status_code == 303
    assert api.get("/api/docs").status_code == 401


def test_a_cookie_signed_with_another_secret_is_refused(api):
    from itsdangerous import URLSafeTimedSerializer

    forged = URLSafeTimedSerializer("a-different-secret", salt="manual-assistant")
    api.cookies.set(auth.SESSION_COOKIE, forged.dumps({"u": "admin", "r": "admin"}))
    assert api.get("/api/docs").status_code == 401


def test_an_expired_session_is_refused(api, monkeypatch: pytest.MonkeyPatch):
    """Signed with yesterday's clock, so no test has to wait for a real expiry."""
    import itsdangerous.timed
    from fastapi import Response
    from starlette.requests import Request

    settings = dataclasses.replace(api.app_module.settings, session_max_age_s=3600)

    def request_with(token: str) -> Request:
        return Request(
            {
                "type": "http",
                "headers": [(b"cookie", f"{auth.SESSION_COOKIE}={token}".encode())],
            }
        )

    fresh = Response()
    auth.issue_session(fresh, auth.User("engineer"), settings)
    fresh_token = fresh.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    assert auth.read_session(request_with(fresh_token), settings) is not None

    class YesterdayClock:
        def time(self) -> float:
            return time.time() - 86400

    monkeypatch.setattr(itsdangerous.timed, "time", YesterdayClock())
    stale = Response()
    auth.issue_session(stale, auth.User("engineer"), settings)
    stale_token = stale.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    monkeypatch.undo()

    assert auth.read_session(request_with(stale_token), settings) is None


def test_a_cookie_for_a_removed_user_is_refused(api):
    sign_in(api, "admin")
    assert api.get("/api/admin/documents").status_code == 200

    class NobodyBackend:
        def authenticate(self, username, password):
            return None

        def get_user(self, username):
            return None

    auth.set_backend(NobodyBackend())
    assert api.get("/api/admin/documents").status_code == 401


def test_losing_the_admin_role_takes_effect_at_once(api):
    """The role is re-read on each request, not trusted from the cookie."""
    sign_in(api, "admin")
    assert api.get("/api/admin/documents").status_code == 200

    class DowngradeBackend:
        def authenticate(self, username, password):
            return None

        def get_user(self, username):
            return auth.User(username, auth.ROLE_USER)

    auth.set_backend(DowngradeBackend())
    assert api.get("/api/admin/documents").status_code == 403
    assert api.get("/api/docs").status_code == 200


def test_a_missing_session_secret_is_an_error_not_an_unsigned_cookie(
    monkeypatch: pytest.MonkeyPatch,
):
    from fastapi import Response

    from app import config as config_module

    monkeypatch.setenv("SESSION_SECRET", "")
    config_module.get_settings.cache_clear()
    settings = config_module.get_settings()
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        auth.issue_session(Response(), auth.User("someone"), settings)
    config_module.get_settings.cache_clear()


# ----------------------------------------------------------------- role checks


def test_a_plain_user_cannot_reach_any_admin_endpoint(api):
    sign_in(api, "engineer")
    for method, path in [
        ("GET", "/api/admin/documents"),
        ("POST", "/api/admin/reindex/1"),
        ("POST", "/api/admin/documents/1"),
        ("DELETE", "/api/admin/documents/1"),
        ("POST", "/api/admin/upload"),
    ]:
        response = api.request(method, path, data={})
        assert response.status_code == 403, f"{method} {path} answered {response.status_code}"


def test_feedback_cannot_be_left_on_another_user_question(api, tmp_path: Path):
    settings = api.app_module.settings
    with db.session(settings.db_path) as conn:
        question_id = db.log_question(
            conn, username="someone-else", question="q", language="en",
            source_ids="", answer_chars=10, latency_ms=5,
        )
    sign_in(api, "engineer")
    response = api.post(
        "/api/feedback", json={"question_id": question_id, "rating": "up"}
    )
    assert response.status_code == 403

    # An admin may, so that feedback can be reviewed.
    api.post("/logout", follow_redirects=False)
    sign_in(api, "admin")
    assert api.post(
        "/api/feedback", json={"question_id": question_id, "rating": "up"}
    ).status_code == 200


# -------------------------------------------------------------------- limits


def test_json_bodies_over_the_limit_are_refused(api):
    sign_in(api)
    settings = api.app_module.settings
    assert settings.max_json_bytes == 16 * 1024
    response = api.post(
        "/api/search", json={"question": "x" * (settings.max_json_bytes + 100)}
    )
    assert response.status_code == 413


def test_a_body_within_the_limit_is_accepted(api):
    sign_in(api)
    response = api.post("/api/search", json={"question": "borehole"})
    assert response.status_code == 200


def test_questions_are_rate_limited_per_user(api, tmp_path: Path):
    from app.ingest import ingest_path as ingest
    from app import search as search_module

    sign_in(api)
    ingest(build_sample_pdf(tmp_path / "h.pdf"), title="Handbook",
           settings=api.app_module.settings)
    search_module.reset_indexes()

    limit = api.app_module.settings.rate_limit_per_minute
    assert limit == 20
    api.app_module.limiter.reset()

    for n in range(limit):
        with api.stream("POST", "/api/ask", json={"question": "borehole import"}) as r:
            assert r.status_code == 200, f"question {n + 1} was refused"
            r.read()
    refused = api.post("/api/ask", json={"question": "borehole import"})
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1


def test_the_rate_limit_is_counted_per_user():
    limiter = RateLimiter(2)
    assert limiter.check("a")[0] and limiter.check("a")[0]
    assert limiter.check("a")[0] is False
    assert limiter.check("b")[0] is True


def test_search_is_not_rate_limited(api):
    """Only questions put to the model are limited, so the section list stays fast."""
    sign_in(api)
    api.app_module.limiter.reset()
    for _ in range(25):
        assert api.post("/api/search", json={"question": "borehole"}).status_code == 200


def test_model_calls_have_timeouts(api):
    settings = api.app_module.settings
    assert settings.llm_timeout_s > 0
    assert settings.embed_timeout_s > 0


def test_the_upload_limit_is_two_hundred_megabytes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    assert load_settings().max_upload_bytes == 200 * 1024 * 1024


# ---------------------------------------------------------------- file serving


def test_files_will_not_serve_a_path_outside_the_manuals_directory(api):
    """A crafted filename in the database must not reach the filesystem."""
    sign_in(api)
    settings = api.app_module.settings
    with db.session(settings.db_path) as conn:
        doc_id = db.create_document(
            conn, title="Crafted", product="", version="", status=db.STATUS_CURRENT,
            filename="../../../../etc/passwd", sha256="x", uploaded_by="tester",
            licence_confirmed_by="tester",
        )
    assert api.get(f"/files/{doc_id}").status_code == 404
    assert api.get(f"/api/page/{doc_id}/1.png").status_code == 404


def test_files_only_serves_documents_in_the_library(api, tmp_path: Path):
    sign_in(api)
    ingest_path(
        build_sample_pdf(tmp_path / "h.pdf"), title="Handbook",
        settings=api.app_module.settings,
    )
    assert api.get("/files/1").status_code == 200
    assert api.get("/files/2").status_code == 404
    assert api.get("/files/0").status_code == 404


# ------------------------------------------------------ escaping, no raw HTML


def test_the_front_end_never_assigns_server_text_to_inner_html(api):
    """Checked as source, because this is the rule that keeps the page safe.

    The property access is what is looked for, not the word, which appears in the
    comment at the top of chat.js explaining the rule.
    """
    import re

    settings = api.app_module.settings
    forbidden = re.compile(
        r"\.\s*(innerHTML|outerHTML)\b|insertAdjacentHTML|document\.write|\beval\s*\("
    )
    for name in ("chat.js", "admin.js"):
        source = (settings.static_dir / name).read_text(encoding="utf-8")
        found = forbidden.findall(source)
        assert not found, f"{name} uses {found}"


def test_document_text_containing_markup_is_returned_as_data(api, tmp_path: Path):
    """A manual may contain angle brackets. They stay text all the way out."""
    import pymupdf

    from app import search as search_module

    path = tmp_path / "markup.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (60, 100),
        "Borehole markup example <script>alert(1)</script> and <b>bold</b> text.",
        fontsize=11,
    )
    doc.save(str(path))
    doc.close()

    ingest_path(path, title="Markup handbook", settings=api.app_module.settings)
    search_module.reset_indexes()
    sign_in(api)

    response = api.post("/api/search", json={"question": "borehole markup example"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["sections"]
    snippet = payload["sections"][0]["snippet"]
    assert "<script>" in snippet, "the text is returned as it appears in the manual"
    # It is JSON data, not markup: the response is not HTML and the page inserts
    # it with textContent.
    assert response.headers["content-type"].startswith("application/json")
    assert "<script>" in json.dumps(payload)


# ----------------------------------------------------- question log, retention


def test_the_question_log_records_what_the_requirement_asks_for(api, tmp_path: Path):
    from app import search as search_module

    ingest_path(
        build_sample_pdf(tmp_path / "h.pdf"), title="Handbook",
        settings=api.app_module.settings,
    )
    search_module.reset_indexes()
    sign_in(api)

    with api.stream("POST", "/api/ask", json={"question": "borehole import"}) as r:
        r.read()

    settings = api.app_module.settings
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM questions ORDER BY id DESC LIMIT 1").fetchone()
    assert row["username"] == "engineer"
    assert row["asked_at"]
    assert row["question"] == "borehole import"
    assert row["language"] == "en"
    assert row["source_ids"]
    assert row["answer_chars"] > 0
    assert row["latency_ms"] >= 0


def test_the_retention_default_is_ninety_days(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    assert load_settings().retention_days == 90


def test_entries_past_the_retention_window_are_deleted(workspace):
    with db.session(workspace.db_path) as conn:
        fresh = db.log_question(
            conn, username="a", question="recent", language="en", source_ids="1",
            answer_chars=5, latency_ms=1,
        )
        old = db.log_question(
            conn, username="a", question="old", language="en", source_ids="1",
            answer_chars=5, latency_ms=1,
        )
        stale = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120 * 86400)
        )
        conn.execute("UPDATE questions SET asked_at = ? WHERE id = ?", (stale, old))

    with db.session(workspace.db_path) as conn:
        removed = db.purge_old_questions(conn, 90)
    assert removed == 1

    with db.session(workspace.db_path) as conn:
        remaining = [r["id"] for r in conn.execute("SELECT id FROM questions")]
    assert remaining == [fresh]


def test_retention_of_zero_days_deletes_nothing(workspace):
    """Zero means the sweep is off, not that everything is deleted."""
    with db.session(workspace.db_path) as conn:
        db.log_question(
            conn, username="a", question="q", language="en", source_ids="",
            answer_chars=1, latency_ms=1,
        )
        assert db.purge_old_questions(conn, 0) == 0
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1


def test_deleting_a_question_takes_its_feedback_with_it(workspace):
    with db.session(workspace.db_path) as conn:
        question_id = db.log_question(
            conn, username="a", question="q", language="en", source_ids="",
            answer_chars=1, latency_ms=1,
        )
        db.add_feedback(conn, question_id=question_id, rating="up", comment="")
        stale = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 200 * 86400)
        )
        conn.execute(
            "UPDATE questions SET asked_at = ? WHERE id = ?", (stale, question_id)
        )
        db.purge_old_questions(conn, 90)
        assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0


# ---------------------------------------------------------------- structured logs


def test_logs_are_one_json_object_per_line(capsys: pytest.CaptureFixture[str]):
    setup_logging()
    logging.getLogger("test").info(
        "answered", extra={"username": "engineer", "latency_ms": 42}
    )
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["msg"] == "answered"
    assert payload["username"] == "engineer"
    assert payload["latency_ms"] == 42
    assert payload["ts"].endswith("Z")


def test_an_exception_is_logged_as_one_line():
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", None, None)
    try:
        raise ValueError("inner")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    line = JsonFormatter().format(record)
    assert "\n" not in line
    payload = json.loads(line)
    assert "ValueError" in payload["exc"]

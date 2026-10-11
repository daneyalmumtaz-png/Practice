"""SQLite storage for documents, chunks, the question log and feedback.

Embeddings are not stored here. They live as one numpy .npy matrix per document
under data/vectors/, because SQLite is a poor fit for dense float arrays.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    title                TEXT    NOT NULL,
    product              TEXT    NOT NULL DEFAULT '',
    version              TEXT    NOT NULL DEFAULT '',
    status               TEXT    NOT NULL DEFAULT 'current',
    filename             TEXT    NOT NULL,
    sha256               TEXT    NOT NULL,
    page_count           INTEGER NOT NULL DEFAULT 0,
    ingest_status        TEXT    NOT NULL DEFAULT 'pending',
    ingest_error         TEXT    NOT NULL DEFAULT '',
    chunk_count          INTEGER NOT NULL DEFAULT 0,
    uploaded_at          TEXT    NOT NULL,
    uploaded_by          TEXT    NOT NULL DEFAULT '',
    licence_confirmed_by TEXT    NOT NULL DEFAULT '',
    licence_confirmed_at TEXT    NOT NULL DEFAULT '',
    indexed_at           TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      INTEGER NOT NULL,
    section_path TEXT    NOT NULL DEFAULT '',
    page_label   TEXT    NOT NULL DEFAULT '',
    pdf_page     INTEGER NOT NULL,
    chunk_type   TEXT    NOT NULL DEFAULT 'text',
    text         TEXT    NOT NULL,
    char_len     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS questions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT    NOT NULL DEFAULT '',
    asked_at     TEXT    NOT NULL,
    question     TEXT    NOT NULL,
    language     TEXT    NOT NULL DEFAULT '',
    source_ids   TEXT    NOT NULL DEFAULT '',
    answer_chars INTEGER NOT NULL DEFAULT 0,
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    not_found    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_questions_asked ON questions(asked_at);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    rating      TEXT    NOT NULL,
    comment     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_question ON feedback(question_id);
"""

INGEST_PENDING = "pending"
INGEST_READY = "ready"
INGEST_FAILED = "failed"
STATUS_CURRENT = "current"
STATUS_SUPERSEDED = "superseded"


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- documents


def create_document(
    conn: sqlite3.Connection,
    *,
    title: str,
    product: str,
    version: str,
    status: str,
    filename: str,
    sha256: str,
    uploaded_by: str,
    licence_confirmed_by: str = "",
) -> int:
    now = utcnow()
    cur = conn.execute(
        """INSERT INTO documents
           (title, product, version, status, filename, sha256, ingest_status,
            uploaded_at, uploaded_by, licence_confirmed_by, licence_confirmed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            title, product, version, status, filename, sha256, INGEST_PENDING,
            now, uploaded_by, licence_confirmed_by, now if licence_confirmed_by else "",
        ),
    )
    return int(cur.lastrowid)


def get_document(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


def document_by_hash(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM documents WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
    ).fetchone()


def list_documents(
    conn: sqlite3.Connection, *, only_current: bool = False
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM documents"
    params: tuple[Any, ...] = ()
    if only_current:
        sql += " WHERE status = ? AND ingest_status = ?"
        params = (STATUS_CURRENT, INGEST_READY)
    sql += " ORDER BY product, title, version, id"
    return list(conn.execute(sql, params))


def update_document(conn: sqlite3.Connection, doc_id: int, **fields: Any) -> None:
    allowed = {
        "title", "product", "version", "status", "page_count", "ingest_status",
        "ingest_error", "chunk_count", "indexed_at", "sha256",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    assignments = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(
        f"UPDATE documents SET {assignments} WHERE id = ?",
        (*sets.values(), doc_id),
    )


def mark_failed(conn: sqlite3.Connection, doc_id: int, error: str) -> None:
    """A document that cannot be read is recorded and forgotten about.

    The app keeps serving every other document; nothing waits on this one.
    """
    update_document(
        conn,
        doc_id,
        ingest_status=INGEST_FAILED,
        ingest_error=error[:2000],
        chunk_count=0,
        indexed_at=utcnow(),
    )
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))


def delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


# ----------------------------------------------------------------------------- chunks


def replace_chunks(
    conn: sqlite3.Connection, doc_id: int, rows: Iterable[dict[str, Any]]
) -> int:
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    payload = [
        (
            doc_id, i, r["section_path"], r["page_label"], r["pdf_page"],
            r["chunk_type"], r["text"], len(r["text"]),
        )
        for i, r in enumerate(rows)
    ]
    conn.executemany(
        """INSERT INTO chunks
           (doc_id, ordinal, section_path, page_label, pdf_page, chunk_type,
            text, char_len)
           VALUES (?,?,?,?,?,?,?,?)""",
        payload,
    )
    return len(payload)


def chunks_for_search(
    conn: sqlite3.Connection, *, include_superseded: bool = False
) -> list[sqlite3.Row]:
    """Chunks joined to their document.

    Superseded documents are included only when an admin asks for them. They are
    never offered to the model, so they can never be cited in an answer.
    """
    sql = """
        SELECT c.id, c.doc_id, c.section_path, c.page_label, c.pdf_page,
               c.chunk_type, c.text, d.title AS doc_title, d.product, d.version,
               d.status
        FROM chunks c JOIN documents d ON d.id = c.doc_id
        WHERE d.ingest_status = ?
    """
    params: list[Any] = [INGEST_READY]
    if not include_superseded:
        sql += " AND d.status = ?"
        params.append(STATUS_CURRENT)
    sql += " ORDER BY c.doc_id, c.ordinal"
    return list(conn.execute(sql, params))


def chunks_for_doc(conn: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY ordinal", (doc_id,)
        )
    )


# ---------------------------------------------------------------- question log, feedback


def log_question(
    conn: sqlite3.Connection,
    *,
    username: str,
    question: str,
    language: str,
    source_ids: str,
    answer_chars: int,
    latency_ms: int,
    not_found: bool = False,
) -> int:
    cur = conn.execute(
        """INSERT INTO questions
           (username, asked_at, question, language, source_ids, answer_chars,
            latency_ms, not_found)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            username, utcnow(), question, language, source_ids, answer_chars,
            latency_ms, 1 if not_found else 0,
        ),
    )
    return int(cur.lastrowid)


def add_feedback(
    conn: sqlite3.Connection, *, question_id: int, rating: str, comment: str
) -> int:
    cur = conn.execute(
        "INSERT INTO feedback (question_id, rating, comment, created_at) VALUES (?,?,?,?)",
        (question_id, rating, comment[:2000], utcnow()),
    )
    return int(cur.lastrowid)


def purge_old_questions(conn: sqlite3.Connection, retention_days: int) -> int:
    """Delete question log entries older than the retention window."""
    if retention_days <= 0:
        return 0
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - retention_days * 86400)
    )
    cur = conn.execute("DELETE FROM questions WHERE asked_at < ?", (cutoff,))
    return cur.rowcount or 0

"""Ingest a PDF into the library.

A manual that cannot be read is recorded with status `failed` and its error
message. The app keeps serving every other manual. Nothing ever waits forever.

Command line:
    python -m app.ingest data/manuals/<file>.pdf --title ... --product ... --version ...
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import db
from .chunker import chunk_blocks
from .config import Settings, get_settings
from .logsetup import get_logger, setup_logging
from .pdfutil import extract

log = get_logger("ingest")

PDF_MAGIC = b"%PDF"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ValidationError(ValueError):
    """Upload rejected before anything is written to disk."""


def safe_filename(name: str) -> str:
    """A file name safe to join onto the manuals directory.

    Path separators, traversal and odd characters are removed rather than
    escaped, because the stored name is also shown in the admin page.
    """
    base = Path(name or "").name
    base = _SAFE.sub("_", base).strip("._-")
    if not base:
        base = "document"
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base[:150]


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for n in range(2, 1000):
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValidationError("too many files with that name")


def validate_pdf(data: bytes, *, max_bytes: int) -> None:
    """A real PDF, within the size limit.

    The magic bytes are checked, not the file extension, because the extension is
    chosen by whoever uploads the file.
    """
    if not data:
        raise ValidationError("file is empty")
    if len(data) > max_bytes:
        limit = (
            f"{max_bytes // (1024 * 1024)} MB"
            if max_bytes >= 1024 * 1024
            else f"{max_bytes} bytes"
        )
        raise ValidationError(f"file is larger than the {limit} limit")
    if not data[:1024].lstrip()[:4] == PDF_MAGIC:
        raise ValidationError("file is not a PDF (missing %PDF header)")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class IngestResult:
    doc_id: int
    status: str
    page_count: int = 0
    chunk_count: int = 0
    table_count: int = 0
    skipped_pages: int = 0
    section_source: str = ""
    error: str = ""
    cached: bool = False


def index_document(
    doc_id: int, *, settings: Settings | None = None, force: bool = False
) -> IngestResult:
    """Parse one document and replace its chunks.

    Caching is by SHA-256 of the file content. A file whose content has not
    changed since it was indexed is not parsed again, however its timestamps look.
    """
    s = settings or get_settings()
    with db.session(s.db_path) as conn:
        row = db.get_document(conn, doc_id)
        if row is None:
            raise KeyError(f"no document with id {doc_id}")
        path = s.manuals_dir / row["filename"]
        title = row["title"]

        if not path.exists():
            db.mark_failed(conn, doc_id, f"file is missing: {row['filename']}")
            log.error("ingest_failed", extra={"doc_id": doc_id, "reason": "missing_file"})
            return IngestResult(doc_id, db.INGEST_FAILED, error="file is missing")

        digest = sha256_file(path)
        if (
            not force
            and row["ingest_status"] == db.INGEST_READY
            and row["sha256"] == digest
            and row["chunk_count"] > 0
        ):
            log.info("ingest_cached", extra={"doc_id": doc_id, "title": title})
            return IngestResult(
                doc_id,
                db.INGEST_READY,
                page_count=row["page_count"],
                chunk_count=row["chunk_count"],
                cached=True,
            )

        try:
            data = path.read_bytes()
            validate_pdf(data, max_bytes=s.max_upload_bytes)
            del data
            extraction = extract(path)
            chunks = chunk_blocks(
                extraction.blocks,
                chunk_chars=s.retrieval.chunk_chars,
                overlap_chars=s.retrieval.chunk_overlap_chars,
            )
            if not chunks:
                raise ValueError(
                    "no text could be extracted; the PDF may be a scan with no text layer"
                )
        except Exception as exc:                       # noqa: BLE001 - recorded, not raised
            message = f"{type(exc).__name__}: {exc}"
            db.mark_failed(conn, doc_id, message)
            log.error(
                "ingest_failed",
                extra={"doc_id": doc_id, "title": title, "error": message},
            )
            return IngestResult(doc_id, db.INGEST_FAILED, error=message)

        count = db.replace_chunks(conn, doc_id, [c.as_row() for c in chunks])
        db.update_document(
            conn,
            doc_id,
            sha256=digest,
            page_count=extraction.page_count,
            chunk_count=count,
            ingest_status=db.INGEST_READY,
            ingest_error="",
            indexed_at=db.utcnow(),
        )
        skipped = sum(1 for p in extraction.pages if p.skipped)
        log.info(
            "ingest_ready",
            extra={
                "doc_id": doc_id,
                "title": title,
                "pages": extraction.page_count,
                "chunks": count,
                "tables": extraction.table_count,
                "skipped_pages": skipped,
                "sections_from": extraction.section_source,
            },
        )
        return IngestResult(
            doc_id,
            db.INGEST_READY,
            page_count=extraction.page_count,
            chunk_count=count,
            table_count=extraction.table_count,
            skipped_pages=skipped,
            section_source=extraction.section_source,
        )


def register_upload(
    *,
    data: bytes,
    filename: str,
    title: str,
    product: str,
    version: str,
    status: str,
    uploaded_by: str,
    licence_confirmed_by: str,
    settings: Settings | None = None,
) -> int:
    """Validate and store an uploaded PDF, returning the new document id."""
    s = settings or get_settings()
    validate_pdf(data, max_bytes=s.max_upload_bytes)
    if not title.strip():
        raise ValidationError("title is required")
    if not licence_confirmed_by:
        raise ValidationError("the licence confirmation must be ticked")
    if status not in (db.STATUS_CURRENT, db.STATUS_SUPERSEDED):
        raise ValidationError("status must be 'current' or 'superseded'")

    s.manuals_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(s.manuals_dir, safe_filename(filename))
    target.write_bytes(data)
    with db.session(s.db_path) as conn:
        return db.create_document(
            conn,
            title=title.strip(),
            product=product.strip(),
            version=version.strip(),
            status=status,
            filename=target.name,
            sha256=sha256_bytes(data),
            uploaded_by=uploaded_by,
            licence_confirmed_by=licence_confirmed_by,
        )


def ingest_path(
    path: Path,
    *,
    title: str,
    product: str = "",
    version: str = "",
    status: str = db.STATUS_CURRENT,
    uploaded_by: str = "cli",
    licence_confirmed_by: str = "cli",
    settings: Settings | None = None,
    force: bool = False,
) -> IngestResult:
    """Ingest a file already on disk. Used by the command line."""
    s = settings or get_settings()
    db.init_db(s.db_path)
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValidationError(f"no such file: {path}")

    s.manuals_dir.mkdir(parents=True, exist_ok=True)
    inside = path.parent == s.manuals_dir.resolve()
    if inside:
        target = path
    else:
        target = unique_path(s.manuals_dir, safe_filename(path.name))
        shutil.copy2(path, target)

    digest = sha256_file(target)
    with db.session(s.db_path) as conn:
        existing = db.document_by_hash(conn, digest)
        if existing is not None and not force:
            doc_id = int(existing["id"])
        else:
            doc_id = db.create_document(
                conn,
                title=title.strip(),
                product=product.strip(),
                version=version.strip(),
                status=status,
                filename=target.name,
                sha256=digest,
                uploaded_by=uploaded_by,
                licence_confirmed_by=licence_confirmed_by,
            )
    return index_document(doc_id, settings=s, force=force)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest",
        description="Add a PDF manual to the library and index it.",
    )
    parser.add_argument("pdf", type=Path, help="path to the PDF")
    parser.add_argument("--title", required=True, help="manual title shown to users")
    parser.add_argument("--product", default="", help="product the manual belongs to")
    parser.add_argument("--version", default="", help="manual or software version")
    parser.add_argument(
        "--status",
        default=db.STATUS_CURRENT,
        choices=[db.STATUS_CURRENT, db.STATUS_SUPERSEDED],
        help="superseded manuals stay searchable for admins but are never cited",
    )
    parser.add_argument("--uploaded-by", default="cli")
    parser.add_argument(
        "--licence-confirmed-by",
        default="cli",
        help="who confirms Destia is licensed to use this document in this tool",
    )
    parser.add_argument(
        "--force", action="store_true", help="re-parse even if the content hash matches"
    )
    args = parser.parse_args(argv)

    setup_logging()
    try:
        result = ingest_path(
            args.pdf,
            title=args.title,
            product=args.product,
            version=args.version,
            status=args.status,
            uploaded_by=args.uploaded_by,
            licence_confirmed_by=args.licence_confirmed_by,
            force=args.force,
        )
    except ValidationError as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 2

    if result.status == db.INGEST_FAILED:
        print(f"failed: document {result.doc_id}: {result.error}", file=sys.stderr)
        return 1
    print(
        f"document {result.doc_id}: {result.status}"
        + (" (cached, content unchanged)" if result.cached else "")
        + f", {result.page_count} pages, {result.chunk_count} chunks"
        + (f", {result.table_count} tables" if result.table_count else "")
        + (f", {result.skipped_pages} navigation pages skipped" if result.skipped_pages else "")
        + (f", sections from {result.section_source}" if result.section_source else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

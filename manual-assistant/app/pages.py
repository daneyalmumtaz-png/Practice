"""Page images for the Preview button, rendered once and cached on disk."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .config import Settings, get_settings
from .logsetup import get_logger

log = get_logger("pages")


class PageRenderError(RuntimeError):
    pass


def cache_path(settings: Settings, doc_id: int, pdf_page: int) -> Path:
    return settings.pagecache_dir / f"{doc_id}-{pdf_page}-{settings.page_render_dpi}.png"


def render_page(
    doc_id: int, pdf_page: int, pdf_path: Path, settings: Settings | None = None
) -> Path:
    """Return the PNG for one page, rendering it only the first time."""
    s = settings or get_settings()
    target = cache_path(s, doc_id, pdf_page)
    if target.exists() and target.stat().st_size > 0:
        return target
    if pdf_page < 1:
        raise PageRenderError("page numbers start at 1")

    s.pagecache_dir.mkdir(parents=True, exist_ok=True)
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:                       # noqa: BLE001
        raise PageRenderError(f"{type(exc).__name__}: {exc}") from exc
    try:
        if pdf_page > doc.page_count:
            raise PageRenderError(
                f"page {pdf_page} is past the end of a {doc.page_count} page document"
            )
        pixmap = doc[pdf_page - 1].get_pixmap(dpi=s.page_render_dpi)
        # Written beside the target and renamed, so a half-written file is never
        # served. The format is passed explicitly because the temporary name does
        # not end in .png, and PyMuPDF otherwise infers the format from it.
        temporary = target.with_suffix(".part")
        pixmap.save(str(temporary), output="png")
        temporary.replace(target)
    finally:
        doc.close()
    log.info("page_rendered", extra={"doc_id": doc_id, "pdf_page": pdf_page})
    return target


def drop_cached_pages(doc_id: int, settings: Settings | None = None) -> int:
    s = settings or get_settings()
    removed = 0
    for path in s.pagecache_dir.glob(f"{doc_id}-*.png"):
        path.unlink(missing_ok=True)
        removed += 1
    return removed

"""Put one small example manual in the library, so the app is not empty at first.

The PDF is generated here by the test suite's own generator. It is a synthetic
nine page document about an imaginary program, written to exercise the indexing:
bookmarks, a repeated footer, roman then arabic page numbers, a table with a
caption, a contents page and an index page. It is not a Bentley manual and
contains nothing real.

    python scripts/demo_manual.py
    python scripts/demo_manual.py --remove

Delete it once you have uploaded your own manuals, either with --remove or with
the Delete button on the Admin page.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

TITLE = "Example Engineering Handbook (demonstration only)"
FILENAME = "example-handbook-demo.pdf"


def load_local_env() -> None:
    """Use the same settings the app runs with, if setup has been done."""
    path = PROJECT / "local.env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/demo_manual.py",
        description="Add or remove the example manual.",
    )
    parser.add_argument(
        "--remove", action="store_true", help="delete the example manual and its index"
    )
    args = parser.parse_args(argv)

    load_local_env()
    os.environ.setdefault("SESSION_SECRET", "only-needed-so-settings-load")

    # This script talks to a person, so the app's machine-readable JSON logs are
    # kept out of the way. A real failure is still reported, by what this script
    # prints itself. PyMuPDF also prints one hint about page layout analysis that
    # it offers no way to silence; it is harmless and can be ignored.
    logging.getLogger().addHandler(logging.NullHandler())
    logging.getLogger().setLevel(logging.ERROR)

    from app import db
    from app.config import get_settings
    from app.ingest import ingest_path, remove_document

    settings = get_settings()
    db.init_db(settings.db_path)

    with db.session(settings.db_path) as conn:
        existing = [r for r in db.list_documents(conn) if r["title"] == TITLE]

    if args.remove:
        if not existing:
            print("The example manual is not in the library.")
            return 0
        for row in existing:
            remove_document(int(row["id"]), settings)
            print(f"Removed: {row['title']}")
        return 0

    if existing:
        print(f"Already in the library: {TITLE}")
        print("Open the app and ask a question, or run with --remove to delete it.")
        return 0

    try:
        from conftest import build_sample_pdf
    except ImportError:
        print("The tests folder is missing, so the example PDF cannot be generated.")
        return 2

    target = settings.manuals_dir / FILENAME
    build_sample_pdf(target)
    result = ingest_path(
        target,
        title=TITLE,
        product="Other Bentley product",
        version="demo",
        uploaded_by="demo_manual.py",
        licence_confirmed_by="not applicable, this document is generated locally",
        settings=settings,
    )
    if result.status != db.INGEST_READY:
        print(f"The example manual could not be indexed: {result.error}")
        return 1

    print(f"Added: {TITLE}")
    print(
        f"  {result.page_count} pages, {result.chunk_count} chunks, "
        f"{result.table_count} table, {result.skipped_pages} navigation pages skipped"
    )
    if result.vector_count:
        print(f"  {result.vector_count} embeddings built")
    else:
        print("  no embeddings: Ollama was not reachable, so search is keywords only")
    print()
    print("Try asking:")
    print("  How do I import borehole data?")
    print("  What fields are read from a borehole file?")
    print("  Miten tuon pohjatutkimustiedot ohjelmaan?")
    print()
    print("This is a generated test document, not a real manual. Remove it with:")
    print("  python scripts/demo_manual.py --remove")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

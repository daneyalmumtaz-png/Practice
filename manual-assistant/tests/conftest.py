"""Test fixtures.

Every PDF used by the tests is generated here with PyMuPDF. No real manual is
read, copied or referenced by the test suite.
"""

from __future__ import annotations

import os
import sys
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
        "together with its survey data and its report templates.",
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
    yield settings
    config_module.get_settings.cache_clear()


@pytest.fixture
def sample_pdf(workspace, tmp_path: Path) -> Path:
    return build_sample_pdf(tmp_path / "sample-handbook.pdf")


@pytest.fixture
def no_bookmarks_pdf(workspace, tmp_path: Path) -> Path:
    return build_no_bookmarks_pdf(tmp_path / "no-bookmarks.pdf")

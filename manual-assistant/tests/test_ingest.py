"""Ingestion tests: the facts every answer depends on being right."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import db
from app.chunker import chunk_blocks
from app.ingest import (
    ValidationError,
    index_document,
    ingest_path,
    register_upload,
    safe_filename,
    validate_pdf,
)
from app.pdfutil import (
    extract,
    find_boilerplate,
    is_index_page,
    is_toc_page,
    normalise_line,
    table_to_markdown,
)

from conftest import (
    FOOTER,
    build_broken_pdf,
    build_no_bookmarks_pdf,
    build_sample_pdf,
)


# ------------------------------------------------------------------- page labels


def test_printed_label_and_pdf_page_are_both_kept(sample_pdf: Path):
    """Front matter is i, ii; the body restarts at 1. Both numbers are needed."""
    extraction = extract(sample_pdf)
    labels = {p.pdf_page: p.page_label for p in extraction.pages}
    assert labels[1] == "i"
    assert labels[2] == "ii"
    assert labels[3] == "1"
    assert labels[9] == "7"
    # The printed label disagrees with the PDF page on every page of the body,
    # which is exactly the confusion the old prototype showed to users.
    assert all(labels[n] != str(n) for n in range(3, 10))


def test_label_falls_back_to_pdf_page_number(tmp_path: Path):
    import pymupdf

    path = tmp_path / "unlabelled.pdf"
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((60, 100), f"Body text on sheet {i} of the document.")
    doc.save(str(path))
    doc.close()

    extraction = extract(path)
    assert [p.page_label for p in extraction.pages] == ["1", "2", "3"]


# --------------------------------------------------------------- boilerplate


def test_repeated_footer_is_dropped(sample_pdf: Path):
    extraction = extract(sample_pdf)
    assert extraction.boilerplate == [normalise_line(FOOTER.format(n=1))]
    assert not any("internal test fixture" in b.text for b in extraction.blocks)


def test_boilerplate_needs_more_than_the_configured_share():
    pages = {i: [f"unique body line {i}", "RUNNING FOOTER"] for i in range(10)}
    pages[0].append("appears twice only")
    pages[1].append("appears twice only")
    drop = find_boilerplate(pages, share=0.2)
    assert normalise_line("RUNNING FOOTER") in drop
    assert normalise_line("appears twice only") not in drop


def test_digits_are_normalised_so_page_numbers_do_not_defeat_the_rule():
    pages = {i: [f"Page {i} of 9", "body"] for i in range(9)}
    drop = find_boilerplate(pages, share=0.2)
    assert normalise_line("Page 3 of 9") in drop


# ------------------------------------------------------- contents and index pages


def test_contents_and_index_pages_are_skipped(sample_pdf: Path):
    extraction = extract(sample_pdf)
    skipped = {p.pdf_page: p.skipped for p in extraction.pages if p.skipped}
    assert skipped == {2: "toc", 9: "index"}
    pages_with_content = {b.pdf_page for b in extraction.blocks}
    assert 2 not in pages_with_content
    assert 9 not in pages_with_content


def test_body_text_ending_in_a_number_is_not_mistaken_for_an_index():
    """A procedure page is full of lines like "Set the tolerance to 10"."""
    lines = [
        "Set the tolerance to 10",
        "Open the dialog and press OK",
        "Check that the value is 5",
        "Enter the depth 12",
        "Review the log",
        "Press Apply",
        "The limit is 30",
        "Save the file",
        "Type 25 in the field",
        "Close the dialog",
    ]
    assert not is_index_page(lines)
    assert not is_toc_page(lines)


def test_dot_leaders_mark_a_contents_page():
    assert is_toc_page(
        [
            "1 Getting started . . . . . . . . 1",
            "2 Importing data . . . . . . . . 3",
            "3 Reporting . . . . . . . . . . . 6",
            "4 Appendix . . . . . . . . . . . 9",
        ]
    )


# --------------------------------------------------------------------- tables


def test_table_is_one_chunk_that_keeps_its_header(sample_pdf: Path):
    extraction = extract(sample_pdf)
    tables = [b for b in extraction.blocks if b.kind == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert "| Column | Unit | Note |" in table.text
    assert "| --- | --- | --- |" in table.text
    # Every data row is present, and in the same chunk as the header.
    for row in ("Depth", "Water content", "Sample number"):
        assert row in table.text
    assert table.text.startswith("Table 1 Fields read from a borehole file")

    chunks = chunk_blocks(extraction.blocks, chunk_chars=200, overlap_chars=20)
    table_chunks = [c for c in chunks if c.chunk_type == "table"]
    assert len(table_chunks) == 1, "a small chunk size must not split the table"
    assert table_chunks[0].text == table.text
    assert "| Column | Unit | Note |" in table_chunks[0].text


def test_table_cells_are_not_repeated_in_the_running_text(sample_pdf: Path):
    extraction = extract(sample_pdf)
    text_blocks = [b for b in extraction.blocks if b.kind == "text"]
    assert not any("below ground level" in b.text for b in text_blocks)
    assert not any(b.text.startswith("Table 1 Fields") for b in text_blocks)


def test_table_markdown_handles_a_pipe_in_a_cell():
    class FakeTable:
        def extract(self):
            return [["a|b", "c"], ["1", "2"]]

    markdown = table_to_markdown(FakeTable())
    assert markdown.splitlines()[0] == r"| a\|b | c |"
    assert markdown.splitlines()[1] == "| --- | --- |"


# ------------------------------------------------------------------- sections


def test_section_paths_come_from_the_bookmarks(sample_pdf: Path):
    extraction = extract(sample_pdf)
    assert extraction.section_source == "toc"
    by_page = {b.pdf_page: b.section_path for b in extraction.blocks}
    assert by_page[3] == "Getting started"
    assert by_page[4] == "Getting started > Opening a project"
    assert by_page[5] == "Importing data"
    assert by_page[6] == "Importing data > Borehole import"
    assert by_page[7] == "Importing data > Field descriptions"
    assert by_page[8] == "Reporting"


def test_section_paths_fall_back_to_detected_headings(no_bookmarks_pdf: Path):
    extraction = extract(no_bookmarks_pdf)
    assert extraction.section_source == "headings"
    paths = {b.pdf_page: b.section_path for b in extraction.blocks}
    assert paths[1] == "3 Reporting"
    assert paths[2] == "3 Reporting > 3.1 Report templates"
    assert "3.2.1 Editing a template" in paths[3]
    # A page with no heading of its own stays in the section it continues.
    assert paths[4] == paths[3]


def test_chunks_never_cross_a_section_boundary(sample_pdf: Path):
    extraction = extract(sample_pdf)
    chunks = chunk_blocks(extraction.blocks, chunk_chars=400, overlap_chars=50)
    assert len(chunks) > len({c.section_path for c in chunks}), "expected a split section"
    for chunk in chunks:
        assert chunk.section_path != "" or chunk.pdf_page == 1
        assert chunk.pdf_page >= 1
        assert chunk.page_label


def test_long_section_is_split_with_overlap(sample_pdf: Path):
    extraction = extract(sample_pdf)
    chunks = chunk_blocks(extraction.blocks, chunk_chars=500, overlap_chars=120)
    field = [c for c in chunks if c.section_path.endswith("Field descriptions")]
    assert len(field) >= 2
    # The tail of one chunk reappears at the head of the next.
    tail = field[0].text[-60:].split("\n")[-1]
    assert tail and tail in field[1].text


# ------------------------------------------------------- upload validation


@pytest.mark.parametrize(
    "raw, reason",
    [
        (b"", "empty"),
        (b"PK\x03\x04 a zip renamed to .pdf", "not a PDF"),
        (b"<html><body>no</body></html>", "not a PDF"),
    ],
)
def test_upload_validation_rejects_non_pdf_content(raw: bytes, reason: str):
    with pytest.raises(ValidationError) as excinfo:
        validate_pdf(raw, max_bytes=200 * 1024 * 1024)
    assert reason in str(excinfo.value)


def test_upload_validation_enforces_the_size_limit():
    with pytest.raises(ValidationError, match="larger than"):
        validate_pdf(b"%PDF-1.7" + b"0" * 5000, max_bytes=1024)


def test_magic_bytes_are_checked_not_the_extension(sample_pdf: Path):
    """A real PDF passes whatever it is called; a fake one fails whatever it is called."""
    validate_pdf(sample_pdf.read_bytes(), max_bytes=200 * 1024 * 1024)
    with pytest.raises(ValidationError):
        validate_pdf(b"not a pdf at all", max_bytes=200 * 1024 * 1024)


@pytest.mark.parametrize(
    "given, expected",
    [
        ("../../etc/passwd", "passwd.pdf"),
        ("manual with spaces.pdf", "manual_with_spaces.pdf"),
        ("", "document.pdf"),
        ("..", "document.pdf"),
        ("a/b/c/report.PDF", "report.PDF"),
    ],
)
def test_filenames_are_sanitised(given: str, expected: str):
    result = safe_filename(given)
    assert result == expected
    assert "/" not in result and ".." not in result


def test_upload_requires_the_licence_confirmation(workspace, sample_pdf: Path):
    with pytest.raises(ValidationError, match="licence"):
        register_upload(
            data=sample_pdf.read_bytes(),
            filename="handbook.pdf",
            title="Example Engineering Handbook",
            product="Other Bentley product",
            version="1.0",
            status=db.STATUS_CURRENT,
            uploaded_by="tester",
            licence_confirmed_by="",
            settings=workspace,
        )


# ------------------------------------------------- end to end, caching, failure


def test_ingest_path_indexes_a_document(workspace, sample_pdf: Path):
    result = ingest_path(
        sample_pdf, title="Example Engineering Handbook", product="Other Bentley product",
        version="1.0", settings=workspace,
    )
    assert result.status == db.INGEST_READY
    assert result.page_count == 9
    assert result.chunk_count > 0
    assert result.table_count == 1
    assert result.skipped_pages == 2

    with db.session(workspace.db_path) as conn:
        rows = db.chunks_for_search(conn)
    assert rows
    assert any(r["chunk_type"] == "table" for r in rows)
    assert all(r["doc_title"] == "Example Engineering Handbook" for r in rows)
    # No chunk is truncated: the old 500 character cut is gone.
    assert max(len(r["text"]) for r in rows) > 500


def test_reindex_is_cached_by_content_hash(workspace, sample_pdf: Path):
    first = ingest_path(sample_pdf, title="Handbook", settings=workspace)
    second = index_document(first.doc_id, settings=workspace)
    assert second.cached is True
    assert second.chunk_count == first.chunk_count

    forced = index_document(first.doc_id, settings=workspace, force=True)
    assert forced.cached is False
    assert forced.chunk_count == first.chunk_count


def test_cache_is_not_fooled_by_a_touched_file(workspace, sample_pdf: Path):
    """Modification time changes, content does not. Nothing is re-parsed."""
    import os
    import time

    first = ingest_path(sample_pdf, title="Handbook", settings=workspace)
    stored = workspace.manuals_dir / "sample-handbook.pdf"
    os.utime(stored, (time.time() + 60, time.time() + 60))
    again = index_document(first.doc_id, settings=workspace)
    assert again.cached is True


def test_broken_pdf_is_recorded_failed_and_does_not_block_other_documents(
    workspace, sample_pdf: Path, tmp_path: Path
):
    good = ingest_path(sample_pdf, title="Good handbook", settings=workspace)
    assert good.status == db.INGEST_READY

    broken = build_broken_pdf(tmp_path / "broken.pdf")
    bad = ingest_path(broken, title="Broken handbook", settings=workspace)
    assert bad.status == db.INGEST_FAILED
    assert bad.error

    with db.session(workspace.db_path) as conn:
        bad_row = db.get_document(conn, bad.doc_id)
        good_row = db.get_document(conn, good.doc_id)
        searchable = db.chunks_for_search(conn)

    assert bad_row["ingest_status"] == db.INGEST_FAILED
    assert bad_row["ingest_error"]
    assert good_row["ingest_status"] == db.INGEST_READY
    # The failure is recorded and the good manual stays fully searchable.
    assert {r["doc_id"] for r in searchable} == {good.doc_id}


def test_missing_file_is_recorded_failed(workspace, sample_pdf: Path):
    result = ingest_path(sample_pdf, title="Handbook", settings=workspace)
    (workspace.manuals_dir / "sample-handbook.pdf").unlink()
    again = index_document(result.doc_id, settings=workspace)
    assert again.status == db.INGEST_FAILED
    assert "missing" in again.error


def test_superseded_document_is_not_searchable_by_default(workspace, tmp_path: Path):
    current = ingest_path(
        build_sample_pdf(tmp_path / "v2.pdf"), title="Handbook v2", settings=workspace
    )
    # Genuinely different content, so this is a second document and not the same
    # file recognised by its hash.
    old = ingest_path(
        build_no_bookmarks_pdf(tmp_path / "v1.pdf"),
        title="Handbook v1",
        status=db.STATUS_SUPERSEDED,
        settings=workspace,
    )
    assert old.doc_id != current.doc_id
    with db.session(workspace.db_path) as conn:
        default = db.chunks_for_search(conn)
        with_old = db.chunks_for_search(conn, include_superseded=True)

    assert old.doc_id not in {r["doc_id"] for r in default}
    assert current.doc_id in {r["doc_id"] for r in default}
    assert old.doc_id in {r["doc_id"] for r in with_old}


def test_a_short_document_keeps_its_text(tmp_path: Path):
    """A one page PDF has every line on 100% of its pages.

    Treating that as a running footer emptied the document, so boilerplate
    removal needs a minimum number of pages before it does anything.
    """
    import pymupdf

    path = tmp_path / "one-page.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 100), "Borehole records are read from a delimited file.")
    page.insert_text((60, 130), "Each record needs a depth and a sample number.")
    doc.save(str(path))
    doc.close()

    extraction = extract(path)
    assert extraction.boilerplate == []
    assert len(extraction.blocks) == 2
    assert any("Borehole records" in b.text for b in extraction.blocks)


def test_boilerplate_needs_a_minimum_number_of_pages():
    assert find_boilerplate({0: ["only line"]}, share=0.2) == set()
    assert find_boilerplate({0: ["footer"], 1: ["footer"]}, share=0.2) == set()
    assert find_boilerplate(
        {0: ["footer"], 1: ["footer"], 2: ["footer"]}, share=0.2
    ) == {"footer"}

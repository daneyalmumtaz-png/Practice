"""PDF reading: page labels, section paths, boilerplate, TOC/index skipping, tables.

Every block that leaves this module knows which PDF page it came from (1-based),
what the printed page label says, and which section of the manual it belongs to.
Those three facts are what the old prototype got wrong, so they are carried
through the whole pipeline rather than reconstructed later.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

BOLD_FLAG = 1 << 4

# A heading like "3.2.1 Importing data". Used only when a PDF has no bookmarks.
NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(\S.{0,110})$")
# Four or more dots, optionally spaced: the dot leaders of a contents page.
DOT_LEADER = re.compile(r"(?:\.\s?){4,}")
# A short line with no sentence punctuation ending in page references:
# "Borehole import, 45, 47" or "Boreholes 45-47". Typical of a back-of-book index.
# Either a comma before the first page number, or two or more page numbers. A plain
# trailing number is not enough, or body text such as "Set the tolerance to 10"
# would be read as an index entry and the page thrown away.
INDEX_LINE = re.compile(
    r"^[^.?!]{2,60}?"
    r"(?:,\s*\d{1,4}(?:\s*[,\u2013-]\s*\d{1,4})*"
    r"|\s\d{1,4}(?:\s*[,\u2013-]\s*\d{1,4})+)$"
)
DIGITS = re.compile(r"\d+")
WS = re.compile(r"\s+")


@dataclass
class Line:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    bold: bool


@dataclass
class Block:
    """One unit of extracted content, already attributed to a page and section."""

    pdf_page: int           # 1-based, as a PDF viewer counts
    page_label: str         # what is printed on the page, e.g. "iv" or "3-2"
    section_path: str       # "Geotechnical > Importing data > Borehole import"
    text: str
    kind: str = "text"      # "text" or "table"


@dataclass
class PageInfo:
    pdf_page: int
    page_label: str
    skipped: str = ""       # "", "toc" or "index"
    line_count: int = 0


@dataclass
class Extraction:
    page_count: int
    blocks: list[Block]
    pages: list[PageInfo]
    section_source: str = "toc"      # "toc" or "headings"
    boilerplate: list[str] = field(default_factory=list)
    table_count: int = 0


# ------------------------------------------------------------------ page level reading


def page_label_for(doc: pymupdf.Document, index: int) -> str:
    """The printed page label, falling back to the 1-based PDF page number.

    A manual that numbers its front matter in roman numerals and its body from 1
    has two different numbers for the same page. Users look for the printed one;
    the PDF viewer needs the other. Both are kept.
    """
    try:
        label = (doc[index].get_label() or "").strip()
    except Exception:
        label = ""
    return label or str(index + 1)


def read_lines(page: pymupdf.Page) -> list[Line]:
    """Text lines with geometry, font size and bold flag, in reading order."""
    out: list[Line] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:          # 0 is text, 1 is an image
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans)
            if not text.strip():
                continue
            out.append(
                Line(
                    text=text.strip(),
                    bbox=tuple(line.get("bbox", (0, 0, 0, 0))),  # type: ignore[arg-type]
                    size=max((s.get("size", 0.0) for s in spans), default=0.0),
                    bold=any(int(s.get("flags", 0)) & BOLD_FLAG for s in spans),
                )
            )
    return out


def normalise_line(text: str) -> str:
    """Key used to spot a line repeated across pages.

    Digits collapse to '#' so that "Page 12 of 340" and "Page 13 of 340" count as
    the same running footer.
    """
    return WS.sub(" ", DIGITS.sub("#", text)).strip().lower()


def find_boilerplate(
    lines_by_page: dict[int, list[str]], *, share: float = 0.2, min_pages: int = 3
) -> set[str]:
    """Normalised lines appearing on more than `share` of the pages.

    Those are running headers, footers and licence stamps.

    A line must also appear on at least `min_pages` pages. Without that, a one
    page document has every one of its lines on 100% of its pages and loses all
    of its text. Below `min_pages` pages there is no way to tell a running footer
    from body text, so nothing is removed.
    """
    page_count = len(lines_by_page)
    if page_count == 0:
        return set()
    seen: dict[str, set[int]] = defaultdict(set)
    for page, lines in lines_by_page.items():
        for raw in lines:
            key = normalise_line(raw)
            if len(key) >= 2:
                seen[key].add(page)
    threshold = share * page_count
    return {
        key
        for key, pages in seen.items()
        if len(pages) > threshold and len(pages) >= min_pages
    }


def is_toc_page(lines: list[str]) -> bool:
    """A contents page: many dot leaders."""
    body = [l for l in lines if l.strip()]
    if len(body) < 4:
        return False
    leaders = sum(1 for l in body if DOT_LEADER.search(l))
    return leaders >= max(3, int(0.3 * len(body)))


def is_index_page(lines: list[str]) -> bool:
    """A back-of-book index: most lines end in page references."""
    body = [l for l in lines if l.strip()]
    if len(body) < 8:
        return False
    refs = sum(1 for l in body if INDEX_LINE.match(l))
    return refs >= 0.5 * len(body)


# ----------------------------------------------------------------------------- sections


def toc_section_paths(doc: pymupdf.Document) -> dict[int, str]:
    """Section path per 0-based page index, from the PDF bookmarks.

    Bookmarks give a page, not a position on the page, so the path changes at page
    boundaries. A page before the first bookmark has no path.
    """
    try:
        toc = doc.get_toc(simple=True) or []
    except Exception:
        return {}
    if not toc:
        return {}

    starts: list[tuple[int, str]] = []
    stack: list[str] = []
    for entry in toc:
        try:
            level, title, page = int(entry[0]), str(entry[1]), int(entry[2])
        except (TypeError, ValueError, IndexError):
            continue
        if page < 1 or level < 1:
            continue
        title = WS.sub(" ", title).strip()
        if not title:
            continue
        del stack[level - 1:]
        while len(stack) < level - 1:
            stack.append("")
        stack.append(title)
        starts.append((page - 1, " > ".join(p for p in stack if p)))

    if not starts:
        return {}
    paths: dict[int, str] = {}
    current = ""
    cursor = 0
    for index in range(doc.page_count):
        while cursor < len(starts) and starts[cursor][0] <= index:
            current = starts[cursor][1]
            cursor += 1
        paths[index] = current
    return paths


def _body_font_size(doc: pymupdf.Document, lines_by_page: dict[int, list[Line]]) -> float:
    """The most common font size, weighted by how much text is set in it."""
    weight: Counter[float] = Counter()
    for lines in lines_by_page.values():
        for line in lines:
            weight[round(line.size, 1)] += len(line.text)
    if not weight:
        return 10.0
    return weight.most_common(1)[0][0]


def heading_level_map(sizes: list[float]) -> dict[float, int]:
    """Larger font means a shallower heading. Three levels are enough."""
    ordered = sorted({round(s, 1) for s in sizes}, reverse=True)
    return {size: min(i + 1, 3) for i, size in enumerate(ordered)}


def classify_heading(
    line: Line, body_size: float, level_of_size: dict[float, int]
) -> tuple[bool, int, str]:
    """Is this line a heading, at what level, and with what title?

    Used only when the PDF has no bookmarks.
    """
    numbered = NUMBERED_HEADING.match(line.text)
    if numbered and line.size >= body_size * 0.98:
        number, title = numbered.group(1), numbered.group(2).strip()
        level = min(number.count(".") + 1, 4)
        return True, level, f"{number} {title}"
    if line.size >= body_size * 1.15 or (line.bold and line.size >= body_size * 1.05):
        if len(line.text) <= 120 and not line.text.endswith((".", ";", ",")):
            return True, level_of_size.get(round(line.size, 1), 2), line.text
    return False, 0, ""


# ------------------------------------------------------------------------------- tables


def _clean_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return WS.sub(" ", text.replace("|", r"\|")).strip()


def table_to_markdown(table: object) -> str:
    """A table as Markdown, header row always present.

    One table is one chunk and is never split, so a row can never be separated
    from the column headings that give it meaning.
    """
    try:
        rows = table.extract() or []            # type: ignore[attr-defined]
    except Exception:
        return ""
    rows = [[_clean_cell(c) for c in row] for row in rows]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return ""

    header: list[str] | None = None
    hdr = getattr(table, "header", None)
    if hdr is not None:
        names = [_clean_cell(n) for n in (getattr(hdr, "names", None) or [])]
        if any(names):
            header = names
            # When the header is a real row of the table, drop it from the body so
            # it is not printed twice.
            if not getattr(hdr, "external", False):
                if rows and [c for c in rows[0]] == [c for c in names]:
                    rows = rows[1:]
    if header is None:
        header = rows[0]
        rows = rows[1:]

    width = max([len(header)] + [len(r) for r in rows]) if rows else len(header)
    def pad(row: list[str]) -> list[str]:
        return (row + [""] * width)[:width]

    out = ["| " + " | ".join(pad(header)) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(pad(r)) + " |" for r in rows]
    return "\n".join(out)


def table_caption(
    lines: list[Line], bbox: tuple[float, float, float, float]
) -> Line | None:
    """The nearest line above the table, preferring one that starts with "Table".

    The Line itself is returned so the caller can keep it out of the running text;
    it is reproduced inside the table chunk instead of being stored twice.
    """
    x0, y0, x1, _ = bbox
    candidates: list[tuple[float, Line]] = []
    for line in lines:
        gap = y0 - line.bbox[3]
        if 0 <= gap <= 72 and line.bbox[2] > x0 - 20 and line.bbox[0] < x1 + 20:
            candidates.append((gap, line))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    labelled = [c for c in candidates if re.match(r"^tabl", c[1].text, re.I)]
    return (labelled or candidates)[0][1]


def _in_bbox(line: Line, boxes: list[tuple[float, float, float, float]]) -> bool:
    cx = (line.bbox[0] + line.bbox[2]) / 2
    cy = (line.bbox[1] + line.bbox[3]) / 2
    return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in boxes)


# ---------------------------------------------------------------------------- pipeline


def extract(path: Path) -> Extraction:
    """Read a PDF into blocks ready for chunking.

    Raises on a file that cannot be opened or has no pages. The caller records the
    error against the document and carries on with the other manuals.
    """
    doc = pymupdf.open(str(path))
    try:
        if doc.needs_pass:
            raise ValueError("PDF is password protected")
        if doc.page_count == 0:
            raise ValueError("PDF has no pages")

        labels = [page_label_for(doc, i) for i in range(doc.page_count)]
        lines_by_page: dict[int, list[Line]] = {}
        tables_by_page: dict[int, list[tuple[str, str]]] = {}
        table_count = 0

        for index in range(doc.page_count):
            page = doc[index]
            lines = read_lines(page)
            boxes: list[tuple[float, float, float, float]] = []
            found: list[tuple[str, str]] = []
            caption_ids: set[int] = set()
            try:
                tables = list(page.find_tables().tables)
            except Exception:
                tables = []
            for table in tables:
                markdown = table_to_markdown(table)
                if not markdown:
                    continue
                bbox = tuple(table.bbox)      # type: ignore[arg-type]
                caption = table_caption(lines, bbox)
                if caption is not None:
                    caption_ids.add(id(caption))
                found.append((caption.text[:200] if caption else "", markdown))
                boxes.append(bbox)
            # Text inside a table area, and the table's own caption, are dropped
            # from the running text so that each appears once, in the table chunk.
            if boxes:
                lines = [
                    l
                    for l in lines
                    if not _in_bbox(l, boxes) and id(l) not in caption_ids
                ]
            lines_by_page[index] = lines
            tables_by_page[index] = found
            table_count += len(found)

        drop = find_boilerplate(
            {i: [l.text for l in ls] for i, ls in lines_by_page.items()}
        )

        toc_paths = toc_section_paths(doc)
        section_source = "toc" if toc_paths else "headings"
        body_size = _body_font_size(doc, lines_by_page)
        level_of_size: dict[float, int] = {}
        if not toc_paths:
            sizes = [
                l.size
                for ls in lines_by_page.values()
                for l in ls
                if l.size > body_size * 1.05 or (l.bold and l.size >= body_size)
            ]
            level_of_size = heading_level_map(sizes)

        blocks: list[Block] = []
        pages: list[PageInfo] = []
        stack: list[str] = []
        current_path = ""

        for index in range(doc.page_count):
            label = labels[index]
            kept = [l for l in lines_by_page[index] if normalise_line(l.text) not in drop]
            texts = [l.text for l in kept]

            skipped = ""
            if is_toc_page(texts):
                skipped = "toc"
            elif is_index_page(texts):
                skipped = "index"
            pages.append(
                PageInfo(
                    pdf_page=index + 1,
                    page_label=label,
                    skipped=skipped,
                    line_count=len(kept),
                )
            )
            if skipped:
                # Contents and index pages are navigation, not content. Indexing
                # them buries the real sections under lists of page numbers.
                continue

            if toc_paths:
                current_path = toc_paths.get(index, current_path)
                for line in kept:
                    blocks.append(
                        Block(index + 1, label, current_path, line.text, "text")
                    )
            else:
                for line in kept:
                    is_head, level, title = classify_heading(
                        line, body_size, level_of_size
                    )
                    if is_head:
                        del stack[level - 1:]
                        while len(stack) < level - 1:
                            stack.append("")
                        stack.append(title)
                        current_path = " > ".join(p for p in stack if p)
                    blocks.append(
                        Block(index + 1, label, current_path, line.text, "text")
                    )

            for caption, markdown in tables_by_page[index]:
                text = f"{caption}\n{markdown}" if caption else markdown
                blocks.append(Block(index + 1, label, current_path, text, "table"))

        return Extraction(
            page_count=doc.page_count,
            blocks=blocks,
            pages=pages,
            section_source=section_source,
            boilerplate=sorted(drop),
            table_count=table_count,
        )
    finally:
        doc.close()

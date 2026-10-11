"""Turn extracted blocks into chunks.

Two rules shape this module. A chunk never crosses a section boundary, so a
citation always points at one place in the manual. And a table is one chunk that
is never split, so a row is never separated from its column headings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from .pdfutil import Block


@dataclass
class Chunk:
    section_path: str
    page_label: str
    pdf_page: int
    chunk_type: str      # "text" or "table"
    text: str

    def as_row(self) -> dict[str, object]:
        return {
            "section_path": self.section_path,
            "page_label": self.page_label,
            "pdf_page": self.pdf_page,
            "chunk_type": self.chunk_type,
            "text": self.text,
        }


def _runs(blocks: Iterable[Block]) -> Iterator[tuple[str, list[Block]]]:
    """Consecutive blocks sharing one section path."""
    current: list[Block] = []
    path: str | None = None
    for block in blocks:
        if path is None or block.section_path == path:
            path = block.section_path
            current.append(block)
        else:
            yield path, current
            path, current = block.section_path, [block]
    if current and path is not None:
        yield path, current


def _tail(text: str, want: int) -> str:
    """The last `want` characters, cut at a word boundary."""
    if want <= 0 or not text:
        return ""
    if len(text) <= want:
        return text
    piece = text[-want:]
    space = piece.find(" ")
    return piece[space + 1:] if space != -1 else piece


def _pieces(text: str, limit: int) -> list[str]:
    """A single line longer than one chunk is cut at whitespace, not mid-word."""
    if len(text) <= int(limit * 1.5):
        return [text]
    out: list[str] = []
    buf: list[str] = []
    length = 0
    for word in text.split(" "):
        if length and length + len(word) + 1 > limit:
            out.append(" ".join(buf))
            buf, length = [], 0
        buf.append(word)
        length += len(word) + 1
    if buf:
        out.append(" ".join(buf))
    return out


def chunk_blocks(
    blocks: Iterable[Block],
    *,
    chunk_chars: int = 1200,
    overlap_chars: int = 150,
    min_tail_chars: int = 150,
) -> list[Chunk]:
    chunks: list[Chunk] = []

    for path, run in _runs(blocks):
        section: list[Chunk] = []
        buffered: list[tuple[int, str, str]] = []   # (pdf_page, page_label, text)
        buffered_len = 0
        carry = ""

        def flush() -> None:
            nonlocal buffered, buffered_len, carry
            if not buffered:
                return
            body = "\n".join(text for _, _, text in buffered)
            full = f"{carry}\n{body}" if carry else body
            page, label, _ = buffered[0]
            section.append(Chunk(path, label, page, "text", full.strip()))
            # The overlap comes from this chunk's own content, never from the
            # carried-in text, so it cannot accumulate across many chunks.
            carry = _tail(body, overlap_chars)
            buffered, buffered_len = [], 0

        for block in run:
            if block.kind == "table":
                flush()
                carry = ""      # a table stands on its own; nothing overlaps into it
                section.append(
                    Chunk(path, block.page_label, block.pdf_page, "table", block.text)
                )
                continue
            for piece in _pieces(block.text, chunk_chars):
                if buffered_len and buffered_len + len(piece) + 1 > chunk_chars:
                    flush()
                buffered.append((block.pdf_page, block.page_label, piece))
                buffered_len += len(piece) + 1
        flush()

        # A stub left at the end of a section is folded back into the chunk before
        # it, so retrieval is not cluttered with two-line fragments.
        if (
            len(section) >= 2
            and section[-1].chunk_type == "text"
            and section[-2].chunk_type == "text"
            and len(section[-1].text) < min_tail_chars
        ):
            tail = section.pop()
            keep = section[-1]
            extra = tail.text
            if carry and extra.startswith(carry):
                extra = extra[len(carry):].lstrip("\n")
            if extra:
                keep.text = f"{keep.text}\n{extra}".strip()

        chunks.extend(c for c in section if c.text.strip())

    return chunks

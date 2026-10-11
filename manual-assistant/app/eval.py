"""Retrieval evaluation.

    python -m app.eval

Reads eval/questions.csv, runs every question through search only (no model
call), and reports the share of questions with an expected page in the top 3 and
the mean reciprocal rank over the top 10, per language and overall. Results are
written to eval/results-<date>.csv.

The expected_pages column is yours to fill in. No page number is invented here.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_DIR, Settings, get_settings
from .lang import detect_language
from .logsetup import setup_logging
from .search import Hit, search

QUESTIONS = PROJECT_DIR / "eval" / "questions.csv"
TOP_K = 10
HIT_AT = 3


@dataclass
class Row:
    question: str
    language: str
    expected_doc: str
    expected_pages: tuple[int, ...]
    notes: str
    scored: bool


@dataclass
class Scored:
    row: Row
    rank: int | None          # 1-based rank of the first expected page, within TOP_K
    hits: list[Hit]


def parse_pages(raw: str) -> tuple[int, ...]:
    pages: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part.strip("-"):
            start, _, end = part.partition("-")
            try:
                pages.extend(range(int(start), int(end) + 1))
            except ValueError:
                continue
        else:
            try:
                pages.append(int(part))
            except ValueError:
                continue
    return tuple(dict.fromkeys(pages))


def read_questions(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for record in csv.DictReader(fh):
            question = (record.get("question") or "").strip()
            if not question:
                continue
            pages = parse_pages(record.get("expected_pages") or "")
            language = (record.get("language") or "").strip().lower()
            rows.append(
                Row(
                    question=question,
                    language=language or detect_language(question),
                    expected_doc=(record.get("expected_doc") or "").strip(),
                    expected_pages=pages,
                    notes=(record.get("notes") or "").strip(),
                    scored=bool(pages),
                )
            )
    return rows


def matches(hit: Hit, row: Row) -> bool:
    if row.expected_doc and hit.doc_title.strip() != row.expected_doc:
        return False
    return hit.pdf_page in row.expected_pages


def score_row(row: Row, settings: Settings) -> Scored:
    outcome = search([row.question], settings=settings, limit=TOP_K)
    rank: int | None = None
    if row.scored:
        for position, hit in enumerate(outcome.hits, start=1):
            if matches(hit, row):
                rank = position
                break
    return Scored(row=row, rank=rank, hits=outcome.hits)


def summarise(scored: list[Scored]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[Scored]] = {"overall": []}
    for item in scored:
        if not item.row.scored:
            continue
        groups["overall"].append(item)
        groups.setdefault(item.row.language, []).append(item)

    summary: dict[str, dict[str, float | int]] = {}
    for name, items in groups.items():
        if not items:
            continue
        hit_at_3 = sum(1 for i in items if i.rank is not None and i.rank <= HIT_AT)
        mrr = sum(1.0 / i.rank for i in items if i.rank is not None) / len(items)
        summary[name] = {
            "questions": len(items),
            f"hit_at_{HIT_AT}": round(hit_at_3 / len(items), 4),
            f"mrr_at_{TOP_K}": round(mrr, 4),
        }
    return summary


def write_results(scored: list[Scored], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"results-{time.strftime('%Y-%m-%d')}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "question", "language", "expected_doc", "expected_pages", "scored",
                "rank", f"hit_at_{HIT_AT}", "reciprocal_rank", "top_result_doc",
                "top_result_section", "top_result_pdf_page", "top_result_page_label",
            ]
        )
        for item in scored:
            top = item.hits[0] if item.hits else None
            writer.writerow(
                [
                    item.row.question,
                    item.row.language,
                    item.row.expected_doc,
                    ",".join(str(p) for p in item.row.expected_pages),
                    "yes" if item.row.scored else "no",
                    item.rank if item.rank else "",
                    1 if item.rank and item.rank <= HIT_AT else 0,
                    round(1.0 / item.rank, 4) if item.rank else 0,
                    top.doc_title if top else "",
                    top.section_path if top else "",
                    top.pdf_page if top else "",
                    top.page_label if top else "",
                ]
            )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.eval", description="Score retrieval against eval/questions.csv"
    )
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--out-dir", type=Path, default=QUESTIONS.parent)
    args = parser.parse_args(argv)

    setup_logging()
    settings = get_settings()
    if not args.questions.is_file():
        print(f"no question file at {args.questions}", file=sys.stderr)
        return 2

    rows = read_questions(args.questions)
    if not rows:
        print(f"{args.questions} has no questions", file=sys.stderr)
        return 2

    scored = [score_row(row, settings) for row in rows]
    summary = summarise(scored)
    path = write_results(scored, args.out_dir)

    unscored = [s for s in scored if not s.row.scored]
    print(f"{len(rows)} questions read from {args.questions}")
    if unscored:
        print(
            f"{len(unscored)} have no expected_pages and were not scored. "
            "Fill in the expected_pages column to score them:"
        )
        for item in unscored:
            top = item.hits[0] if item.hits else None
            where = (
                f"top result: {top.doc_title} p{top.pdf_page} (printed {top.page_label})"
                f" in {top.section_path or 'no section'}"
                if top
                else "no results"
            )
            print(f"  - {item.row.question}  [{item.row.language}]  {where}")

    if summary:
        print()
        width = max(len(k) for k in summary)
        print(f"{'group'.ljust(width)}  questions  hit@{HIT_AT}  mrr@{TOP_K}")
        for name in ["overall"] + sorted(k for k in summary if k != "overall"):
            if name not in summary:
                continue
            s = summary[name]
            print(
                f"{name.ljust(width)}  {str(s['questions']).rjust(9)}  "
                f"{s[f'hit_at_{HIT_AT}']:.3f}  {s[f'mrr_at_{TOP_K}']:.3f}"
            )
    else:
        print("\nnothing scored, so no hit@3 or mrr@10 to report")

    print(f"\nwritten to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Turning a question into a prompt, and a prompt into a streamed answer.

Three model calls can happen, in this order:

1. A follow-up question is rewritten into a standalone one, using the last three
   turns, so that "and in Finnish?" can be searched at all.
2. A question that is not in English also gets a short English search query,
   because the manuals are in English.
3. The answer itself, from the numbered sources and nothing else.

Steps 1 and 2 are skipped when they are not needed, and a failure in either is
logged and ignored: the original question is searched instead. Only step 3 is
essential.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from . import llm
from .config import Settings, get_settings
from .lang import ENGLISH, detect_language
from .logsetup import get_logger
from .search import Source

log = get_logger("answer")

NOT_FOUND = "Not found in the uploaded manuals"

REWRITE_SYSTEM = (
    "You rewrite the last question in a conversation so that it can be understood "
    "on its own, without the earlier turns. Keep the original language. Keep every "
    "product, command, tab and dialog name exactly as written. Add nothing that is "
    "not already in the conversation. Reply with the rewritten question only."
)

ENGLISH_QUERY_SYSTEM = (
    "You write a short English search query for a question about software manuals. "
    "Use at most twelve words. Use only words implied by the question itself: do not "
    "add product names, command names, menu paths or version numbers. Reply with the "
    "query only."
)


@dataclass
class Turn:
    """One earlier exchange, as the browser remembers it."""

    question: str
    answer: str = ""


@dataclass
class Plan:
    """What search will be given, and how it was arrived at."""

    question: str                 # the question actually searched
    original: str
    language: str
    queries: list[str] = field(default_factory=list)
    rewritten: bool = False
    english_query: str = ""
    notes: list[str] = field(default_factory=list)


def _history_block(history: Sequence[Turn], keep: int = 3) -> str:
    recent = [t for t in history if t.question.strip()][-keep:]
    lines: list[str] = []
    for turn in recent:
        lines.append(f"Q: {turn.question.strip()}")
        if turn.answer.strip():
            # The answer is only needed for what it referred to, so a long one is
            # trimmed here. This is conversation context, not a source.
            lines.append(f"A: {turn.answer.strip()[:400]}")
    return "\n".join(lines)


async def standalone_question(
    question: str, history: Sequence[Turn], settings: Settings | None = None
) -> tuple[str, bool]:
    """The question with the conversation folded into it.

    Returns the original question unchanged when there is no history, or when the
    model is unavailable.
    """
    s = settings or get_settings()
    block = _history_block(history)
    if not block:
        return question, False
    try:
        rewritten = await llm.complete(
            [
                {"role": "system", "content": REWRITE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Conversation so far:\n{block}\n\n"
                        f"Last question: {question}\n\nRewritten question:"
                    ),
                },
            ],
            settings=s,
            temperature=0.0,
            max_tokens=120,
            timeout=min(30.0, s.llm_timeout_s),
        )
    except llm.LLMError as exc:
        log.warning("rewrite_failed", extra={"error": str(exc)})
        return question, False

    cleaned = rewritten.strip().strip('"').splitlines()[0].strip() if rewritten else ""
    if not cleaned or len(cleaned) > 400:
        return question, False
    return cleaned, cleaned.lower() != question.strip().lower()


async def english_search_query(
    question: str, settings: Settings | None = None
) -> str:
    """A short English query for a question asked in another language."""
    s = settings or get_settings()
    try:
        query = await llm.complete(
            [
                {"role": "system", "content": ENGLISH_QUERY_SYSTEM},
                {"role": "user", "content": question},
            ],
            settings=s,
            temperature=0.0,
            max_tokens=40,
            timeout=min(20.0, s.llm_timeout_s),
        )
    except llm.LLMError as exc:
        log.warning("english_query_failed", extra={"error": str(exc)})
        return ""
    cleaned = query.strip().strip('"').splitlines()[0].strip() if query else ""
    return cleaned[:200]


async def plan_queries(
    question: str,
    history: Sequence[Turn],
    settings: Settings | None = None,
) -> Plan:
    """Work out what to search for, before touching the index."""
    s = settings or get_settings()
    original = question.strip()
    searched, rewritten = await standalone_question(original, history, s)
    language = detect_language(searched)
    plan = Plan(
        question=searched,
        original=original,
        language=language,
        queries=[searched],
        rewritten=rewritten,
    )
    if rewritten:
        plan.notes.append("question rewritten from the conversation")
    if language != ENGLISH:
        english = await english_search_query(searched, s)
        if english and english.lower() != searched.lower():
            plan.english_query = english
            plan.queries.append(english)
            plan.notes.append("searched also with an English query")
    return plan


def format_sources(sources: Sequence[Source]) -> str:
    """The numbered sources, whole. Nothing here is shortened."""
    parts: list[str] = []
    for source in sources:
        hit = source.hit
        product = " ".join(p for p in (hit.product, hit.version) if p)
        heading = f"[{source.number}] Manual: {hit.doc_title}"
        if product:
            heading += f" ({product})"
        parts.append(
            "\n".join(
                [
                    heading,
                    f"Section: {hit.section_path or 'no section recorded'}",
                    f"Page: printed {hit.page_label}, PDF page {hit.pdf_page}",
                    f"Content type: {hit.chunk_type}",
                    "---",
                    hit.text,
                ]
            )
        )
    return "\n\n".join(parts)


def build_messages(
    question: str,
    sources: Sequence[Source],
    *,
    language: str,
    settings: Settings | None = None,
) -> list[llm.Message]:
    s = settings or get_settings()
    reply_language = "Finnish" if language != ENGLISH else "English"
    user_content = (
        f"Sources:\n\n{format_sources(sources)}\n\n"
        f"Question: {question}\n\n"
        f"Answer in {reply_language}. Cite the source number after every step."
    )
    return [
        {"role": "system", "content": s.system_prompt},
        {"role": "user", "content": user_content},
    ]


def not_found_message(language: str, closest: Sequence[Source]) -> str:
    """Used when search found nothing at all, so there is no point asking the model."""
    if not closest:
        if language != ENGLISH:
            return (
                f"{NOT_FOUND}\n\nHakusanoilla ei löytynyt yhtään kohtaa. "
                "Tarkista, että käsikirja on ladattu ja indeksoitu."
            )
        return (
            f"{NOT_FOUND}\n\nThe search found no sections at all. Check that the "
            "manual has been uploaded and indexed."
        )
    first = closest[0].hit
    where = f"{first.doc_title}, {first.section_path or 'no section recorded'}"
    if language != ENGLISH:
        return f"{NOT_FOUND}\n\nLähin kohta on [{closest[0].number}] {where}."
    return f"{NOT_FOUND}\n\nThe closest section is [{closest[0].number}] {where}."


def looks_not_found(answer: str) -> bool:
    return NOT_FOUND.lower() in (answer or "").lower()

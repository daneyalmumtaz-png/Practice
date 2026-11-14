"""Language detection and tokenisation for BM25.

The manuals are in English. The questions are in English or Finnish. So every
token is reduced to both its English stem and its Finnish lemma, and the same
treatment is applied to the chunks and to the question. That gives BM25 a
chance on a Finnish question whose terms happen to be shared (product names,
file formats, numbers), and the embedding half of the search covers the rest.
"""

from __future__ import annotations

import re
from functools import lru_cache

import simplemma
import snowballstemmer

FINNISH = "fi"
ENGLISH = "en"

# Letters, digits and the Finnish and Swedish letters used in Finnish text.
TOKEN = re.compile(r"[0-9a-zA-ZäöåÄÖÅ]+")

# Function words that carry no retrieval signal. Kept short on purpose: an
# aggressive stop list removes terms that matter in a software manual.
STOPWORDS = {
    ENGLISH: {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
        "for", "from", "how", "i", "in", "is", "it", "of", "on", "or", "the",
        "to", "what", "when", "where", "which", "with", "you", "your",
    },
    FINNISH: {
        "ja", "tai", "on", "ei", "se", "että", "kun", "jos", "mitä", "miten",
        "missä", "mistä", "mihin", "kuinka", "voi", "olla", "joka", "mutta",
        "myös", "niin", "sen", "tämä", "siitä",
    },
}

_FINNISH_HINT = re.compile(r"[äöå]|\b(miten|mistä|missä|mihin|kuinka|onko|voiko)\b", re.I)

_english_stemmer = snowballstemmer.stemmer("english")


def detect_language(text: str) -> str:
    """"fi" or "en". Anything that is not clearly Finnish is treated as English.

    Short questions defeat statistical detection, so an explicit Finnish hint
    (ä, ö, å or a Finnish question word) decides those.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ENGLISH
    if _FINNISH_HINT.search(stripped):
        return FINNISH
    try:
        scores = simplemma.langdetect(stripped, lang=(ENGLISH, FINNISH))
    except Exception:
        return ENGLISH
    best = {code: score for code, score in scores}
    if best.get(FINNISH, 0.0) > best.get(ENGLISH, 0.0):
        return FINNISH
    return ENGLISH


def words(text: str) -> list[str]:
    """Lowercased word-like tokens, in order."""
    return [m.group(0).lower() for m in TOKEN.finditer(text or "")]


@lru_cache(maxsize=200_000)
def _normalise_word(word: str) -> tuple[str, ...]:
    """The forms of one word that go into the index.

    The word itself, its English stem and its Finnish lemma, deduplicated. A
    one-word cache keeps this cheap across a whole corpus.
    """
    forms = {word}
    if word.isalpha():
        try:
            forms.add(_english_stemmer.stemWord(word))
        except Exception:
            pass
        try:
            lemma = simplemma.lemmatize(word, lang=FINNISH)
            if lemma:
                forms.add(lemma.lower())
        except Exception:
            pass
    return tuple(sorted(forms))


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Index and query tokens. Identical treatment on both sides."""
    out: list[str] = []
    for word in words(text):
        if drop_stopwords and (
            word in STOPWORDS[ENGLISH] or word in STOPWORDS[FINNISH]
        ):
            continue
        if len(word) == 1 and not word.isdigit():
            continue
        out.extend(_normalise_word(word))
    return out


def query_terms(text: str) -> set[str]:
    """Normalised forms of the question, for matching and highlighting."""
    return set(tokenize(text))


def highlight_words(text: str, terms: set[str]) -> list[str]:
    """Surface words in `text` whose normalised form is a query term.

    The front end highlights these exact words, so the list is of surface forms
    as they appear in the manual, not of stems.
    """
    found: list[str] = []
    seen: set[str] = set()
    for word in words(text):
        if word in seen:
            continue
        if terms.intersection(_normalise_word(word)):
            seen.add(word)
            found.append(word)
    return found


def warmup() -> None:
    """Load the lemmatiser dictionaries before the first user question.

    simplemma reads its Finnish data on first use. Left to happen on a request,
    that showed up as several seconds on the first search of a cold process,
    against a target of about one second.
    """
    tokenize("borehole import pohjatutkimustiedot raporttipohjat")
    detect_language("Miten tuon pohjatutkimustiedot ohjelmaan?")

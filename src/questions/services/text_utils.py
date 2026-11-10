"""
Small, dependency-free text helpers shared by search, retrieval and the
offline answer provider.
"""

import re

# Words that carry no topical meaning in a question. Kept deliberately small
# and explicit (instead of pulling in an NLP library) so behaviour is easy to
# reason about and test.
# A whitespace-separated block is easier to read and edit than a long list.
STOP_WORDS = frozenset(
    """
    a about an and any are as at be been being but by can could did do does
    doing for from had has have having how i if in into is it its me my of
    on or our please should so some tell than that the their them then there
    these they this those to us was we were what when where which while who
    whom whose why will with would you your explain describe define meaning
    mean know give
    """.split()  # noqa: SIM905
)

_WORD_RE = re.compile(r"\b\w+\b")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, punctuation removed."""
    return _WORD_RE.findall(text.lower())


def extract_keywords(text: str) -> list[str]:
    """
    Meaningful words from a question, in order, without duplicates.

    "What is Redis and what is it used for?" -> ["redis", "used"]
    """
    seen: set[str] = set()
    keywords = []

    for word in tokenize(text):
        if word in STOP_WORDS or word in seen:
            continue
        seen.add(word)
        keywords.append(word)

    return keywords


_EMPTY_PARENS_RE = re.compile(r"\(\s*[;,]?\s*\)")
_LEADING_PAREN_JUNK_RE = re.compile(r"\(\s*[;,]?\s+|\(\s*[;,]\s*")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def clean_extract(text: str) -> str:
    """
    Tidy Wikipedia plain-text extracts.

    Stripping markup removes pronunciation templates but leaves debris such
    as "Redis (; Remote Dictionary Server)" or "Django ( JANG-goh; ...)".
    """
    text = _EMPTY_PARENS_RE.sub("", text)
    text = _LEADING_PAREN_JUNK_RE.sub("(", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    """Naive sentence splitter; good enough for Wikipedia lead sections."""
    return [
        sentence.strip()
        for sentence in _SENTENCE_RE.split(text)
        if sentence and sentence.strip()
    ]

"""
Similarity scoring for metadata matches.

When a source returns a book, we need to decide: is THIS what we were
searching for, or a different book that happened to share some words?
The enricher calls `score_match()` with the search criteria and the
returned record; the score is used to accept, reject, or fall through
to the next source.

Two pieces:

  - **Title similarity** — token-overlap Jaccard over the normalized
    title words. Levenshtein is tempting but over-weights spacing
    differences; a book that has the same words in a different order
    should still score very high, and Jaccard handles that cleanly.
  - **Author overlap** — normalize both sides through the existing
    `normalize_author`, then check set intersection. Even a single
    matching author is good evidence the book is correct; the
    other credits may be translators, editors, or series editors
    that aren't on MAM's announce blob.

Final confidence is a weighted average: title 70%, authors 30%.
Title matters more because one author can write dozens of books;
only the title narrows it down to one of them.

The 0.65 default accept threshold is picked so that:
  - "The Way of Kings" vs "The Way of Kings" → 1.00 (exact)
  - "The Way of Kings" vs "Way of Kings" → 0.75+ (single-word drop)
  - "Foundation" vs "Foundation and Empire" → 0.4-ish (fail)
"""
from __future__ import annotations

import re

from app.filter.gate import split_authors
from app.filter.normalize import normalize_author

_WORD_RX = re.compile(r"[a-z0-9']+")


def _title_tokens(title: str) -> set[str]:
    """Normalize a title into a set of comparison tokens."""
    lowered = title.lower() if title else ""
    tokens = set(_WORD_RX.findall(lowered))
    # Drop filler articles that inflate Jaccard when titles start with "The ...".
    for stop in ("the", "a", "an", "of", "and", "in", "on"):
        tokens.discard(stop)
    return tokens


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity over normalized word sets.

    Returns 1.0 for identical (up to stopword/stripping differences),
    0.0 for disjoint. An empty set on either side returns 0.0 rather
    than nan so downstream comparisons don't blow up.
    """
    ta = _title_tokens(a)
    tb = _title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def author_overlap(
    candidates: list[str] | str, targets: list[str] | str
) -> float:
    """Fraction of target authors matched by candidates.

    Both sides accept either a pre-split list or a raw blob string.
    Normalization goes through `normalize_author` so typographic
    variants and "Lastname, Firstname" forms compare equal.

    Returns the fraction of TARGET authors that appear in the
    candidate set — 1.0 when every target matches, 0.5 when half
    match, 0.0 when none do. Anchoring on the target side means
    an announce with one author still scores high even if the
    scraped record lists three.
    """
    cand = _normalize_set(candidates)
    tgt = _normalize_set(targets)
    if not tgt:
        return 0.0
    hits = sum(1 for t in tgt if t in cand)
    return hits / len(tgt)


def _normalize_set(value: list[str] | str) -> set[str]:
    if isinstance(value, str):
        raw = split_authors(value)
    else:
        raw = value
    out: set[str] = set()
    for entry in raw:
        norm = normalize_author(entry)
        if norm:
            out.add(norm)
    return out


def score_match(
    *,
    record_title: str,
    record_authors: list[str],
    search_title: str,
    search_authors: list[str] | str,
) -> float:
    """Weighted confidence in [0, 1]. See module docstring for weights."""
    ts = title_similarity(record_title, search_title)
    au = author_overlap(record_authors, search_authors)
    return 0.7 * ts + 0.3 * au

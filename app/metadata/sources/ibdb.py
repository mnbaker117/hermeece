"""
IBDB (Internet Book Database) metadata source — REST API.

Simple JSON API at ibdb.dev with no auth required. Returns book
metadata including title, authors, ISBN, cover URL, description,
publisher, and publication date.

Known reliability issue: IBDB occasionally returns 501 errors. We
handle this gracefully and treat it as "source unavailable" rather
than a pipeline failure. Positioned as a supplementary source for
ISBN and publisher when primary sources miss.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.metadata.record import MetaRecord
from app.metadata.sources.base import MetaSource

_log = logging.getLogger("hermeece.metadata.ibdb")

_SEARCH_URL = "https://ibdb.dev/api/search"


class IbdbSource(MetaSource):
    name = "ibdb"
    default_timeout = 15.0

    def __init__(self, *, rate_limit: float = 1.0):
        super().__init__(rate_limit=rate_limit)

    async def search_book(
        self, title: str, author: str
    ) -> Optional[MetaRecord]:
        if not title:
            return None

        query = f"{title} {author}".strip()
        try:
            resp = await self._get(
                _SEARCH_URL,
                params={"q": query},
            )
        except Exception:
            _log.debug("ibdb: search failed or 501")
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        # IBDB returns different response shapes. Handle both list
        # and object-with-results patterns.
        results = []
        if isinstance(data, list):
            results = data
        elif isinstance(data, dict):
            results = data.get("results", data.get("books", data.get("data", [])))
            if not isinstance(results, list):
                results = []

        if not results:
            return None

        # Score and pick best match.
        from app.metadata.scoring import score_match
        best = None
        best_score = 0.0
        for item in results[:10]:
            if not isinstance(item, dict):
                continue
            item_title = (
                item.get("title") or item.get("name") or ""
            )
            item_authors = _extract_authors(item)
            score = score_match(
                record_title=item_title,
                record_authors=item_authors,
                search_title=title,
                search_authors=author,
            )
            if score > best_score:
                best = item
                best_score = score

        if best is None or best_score < 0.3:
            return None

        return _item_to_record(best)


def _extract_authors(item: dict) -> list[str]:
    """Extract author names from various IBDB response shapes."""
    authors = item.get("authors") or item.get("author") or []
    if isinstance(authors, str):
        return [authors]
    if isinstance(authors, list):
        out = []
        for a in authors:
            if isinstance(a, str):
                out.append(a)
            elif isinstance(a, dict):
                out.append(a.get("name", str(a)))
        return out
    return []


def _item_to_record(item: dict) -> MetaRecord:
    title = item.get("title") or item.get("name") or ""
    authors = _extract_authors(item)

    isbn = item.get("isbn_13") or item.get("isbn") or item.get("isbn_10")
    cover = item.get("cover") or item.get("image") or item.get("thumbnail")
    description = item.get("description") or item.get("summary")
    publisher = item.get("publisher")
    pub_date = item.get("publication_date") or item.get("publish_date")
    pages = item.get("pages") or item.get("page_count")
    language = item.get("language")

    series_name = item.get("series") or item.get("series_name")
    series_index = None
    if item.get("series_number") or item.get("series_index"):
        try:
            series_index = float(
                item.get("series_number") or item.get("series_index")
            )
        except (ValueError, TypeError):
            pass

    return MetaRecord(
        title=title,
        authors=authors,
        series=series_name if isinstance(series_name, str) else None,
        series_index=series_index,
        description=description,
        isbn=str(isbn).replace("-", "") if isbn else None,
        publisher=publisher,
        pub_date=str(pub_date)[:10] if pub_date else None,
        page_count=int(pages) if pages else None,
        language=language,
        cover_url=cover,
        source="ibdb",
        external_id=str(item.get("id", "")),
    )

"""
Scaffold-only metadata sources.

Each class below implements the MetaSource contract but returns None
from `search_book`. They exist so:

  1. The enricher's provider priority list compiles and runs
  2. Settings can reference them by name without conditional imports
  3. Implementers can replace a stub with real scraping one at a time
     without touching the enricher or pipeline

When you fill one in:
  - Keep `name` unchanged (enricher uses it for config lookups)
  - Preserve book-centric semantics: `search_book(title, author)`
  - Return None on misses, raise only for hard protocol errors
  - Add a fixture-driven test under tests/metadata/sources/
  - Update `app/metadata/sources/__init__.py` / enricher registry

Priority notes from the user's feature spec (#21):
  - Amazon: CWA has it; adapt from CWA's `metadata_provider/amazon.py`
  - Hardcover: BOTH have impls; combine AthenaScout's GraphQL client +
    CWA's similarity scoring
  - Kobo: BOTH have impls; compare and pick the best
  - IBDB: CWA-only; simple REST at ibdb.dev/api/search
  - Google Books: CWA has it; free public API, was poor in AthenaScout
"""
from __future__ import annotations

from typing import Optional

from app.metadata.record import MetaRecord
from app.metadata.sources.base import MetaSource


class _Stub(MetaSource):
    async def search_book(
        self, title: str, author: str
    ) -> Optional[MetaRecord]:
        return None


class AmazonSource(_Stub):
    name = "amazon"


class HardcoverSource(_Stub):
    name = "hardcover"


class KoboSource(_Stub):
    name = "kobo"


class IbdbSource(_Stub):
    name = "ibdb"


class GoogleBooksSource(_Stub):
    name = "google_books"

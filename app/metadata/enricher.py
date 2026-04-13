"""
Metadata enricher — orchestrates scraper sources and merges results.

Called from the pipeline's prepare-book phase with the announce title
+ author blob. Walks the configured source priority list, calling
`search_book` on each until we either:
  - find a source whose result scores above the accept threshold
    (we stop there and return it), OR
  - exhaust the list and return whatever we gathered

Merge semantics across multiple sources:
  - First non-None value wins for each field (the highest-priority
    source that had data for a given field takes precedence)
  - Confidence becomes the MAX confidence seen across all sources
    (a strong match from any source is enough)
  - Cover URL is preferred from the highest-confidence source so we
    don't accidentally pick Goodreads' tiny thumbnail over Amazon's
    full-size cover

Per-source timeout + fail-safe: a stuck scraper never blocks the
pipeline. Each `search_book` is wrapped in `asyncio.wait_for()` with
the configured timeout (default 15s). Exceptions and timeouts are
logged and treated as "this source returned nothing" — the loop
advances to the next provider.

Feature flag: the pipeline only invokes the enricher when
`metadata_enrichment_enabled` is True in settings. Default is
False so existing deployments don't suddenly start making outbound
HTTP calls to every scraper on every book.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.metadata.record import MetaRecord
from app.metadata.scoring import score_match
from app.metadata.sources.base import MetaSource
from app.metadata.sources.goodreads import GoodreadsSource
from app.metadata.sources.amazon import AmazonSource
from app.metadata.sources.google_books import GoogleBooksSource
from app.metadata.sources.hardcover import HardcoverSource
from app.metadata.sources.ibdb import IbdbSource
from app.metadata.sources.kobo import KoboSource
from app.metadata.sources.mam_search import MamSearchSource

_log = logging.getLogger("hermeece.metadata.enricher")

# Default provider priority. MAM runs first (free, authoritative,
# uses the cached torrent_info response). External scrapers follow
# in the user's spec order (#21) for fields MAM doesn't carry
# (covers, page count, pub date, ISBN).
DEFAULT_PRIORITY: tuple[str, ...] = (
    "mam",
    "goodreads",
    "amazon",
    "hardcover",
    "kobo",
    "ibdb",
    "google_books",
)

# Accept threshold — records with confidence >= this are considered
# good enough to stop searching. Tuned so exact and near-exact
# matches short-circuit but lower matches fall through.
_ACCEPT_CONFIDENCE = 0.8

# Per-source timeout in seconds. Protects the pipeline from a single
# stuck scraper. Matches CWA's documented default.
_PER_SOURCE_TIMEOUT = 15.0


@dataclass
class EnrichmentConfig:
    """Runtime knobs for the enricher.

    Built from settings.json in `main.py`. Kept distinct from the
    source instances themselves so tests can construct an enricher
    with a fixed config without reading settings.
    """

    enabled: bool = False
    priority: tuple[str, ...] = DEFAULT_PRIORITY
    per_source_timeout: float = _PER_SOURCE_TIMEOUT
    accept_confidence: float = _ACCEPT_CONFIDENCE
    disabled_sources: frozenset[str] = field(default_factory=frozenset)


_SOURCE_REGISTRY: dict[str, type[MetaSource]] = {
    MamSearchSource.name: MamSearchSource,
    GoodreadsSource.name: GoodreadsSource,
    AmazonSource.name: AmazonSource,
    HardcoverSource.name: HardcoverSource,
    KoboSource.name: KoboSource,
    IbdbSource.name: IbdbSource,
    GoogleBooksSource.name: GoogleBooksSource,
}


class MetadataEnricher:
    """Coordinates metadata lookup across the configured sources."""

    def __init__(
        self,
        config: EnrichmentConfig,
        *,
        sources: Optional[list[MetaSource]] = None,
    ):
        self.config = config
        if sources is not None:
            # Test / custom override.
            self._sources = sources
        else:
            self._sources = _build_default_sources(config)

    async def enrich(
        self,
        *,
        title: str,
        author: str,
        mam_torrent_id: str = "",
        mam_token: str = "",
    ) -> Optional[MetaRecord]:
        """Run the priority list and return the best merged record.

        When `mam_torrent_id` and `mam_token` are provided, the MAM
        source gets an exact-ID lookup (confidence=1.0) for free —
        it reuses the cached torrent_info from the policy engine.
        External scrapers then fill any gaps (covers, page count, etc.)

        Returns None when every source returned None or errored.
        """
        if not self.config.enabled:
            return None
        if not title and not author:
            return None

        # Build the source list, injecting a MAM source with the
        # torrent ID if available. This is per-call because the
        # torrent ID changes for each book.
        sources = list(self._sources)
        if mam_torrent_id and mam_token:
            mam_src = MamSearchSource(
                mam_token=mam_token, torrent_id=mam_torrent_id
            )
            # Insert at the front so MAM runs first.
            sources = [mam_src] + [s for s in sources if s.name != "mam"]

        merged: Optional[MetaRecord] = None
        for src in sources:
            result = await self._safe_search(src, title=title, author=author)
            if result is None:
                continue
            # Exact-ID lookups (like MAM with torrent_id) already set
            # confidence=1.0. Only re-score with Jaccard when the source
            # did a fuzzy text search (confidence not already pinned).
            if result.confidence < 1.0:
                result.confidence = score_match(
                    record_title=result.title or title,
                    record_authors=result.authors or [],
                    search_title=title,
                    search_authors=author,
                )
            _log.info(
                "enricher: %s → confidence %.2f (title=%r)",
                src.name, result.confidence, result.title,
            )
            merged = _merge_records(merged, result)
            if result.confidence >= self.config.accept_confidence:
                break  # good enough; don't hit more providers
        return merged

    async def _safe_search(
        self, source: MetaSource, *, title: str, author: str
    ) -> Optional[MetaRecord]:
        try:
            return await asyncio.wait_for(
                source.search_book(title, author),
                timeout=self.config.per_source_timeout,
            )
        except asyncio.TimeoutError:
            _log.warning(
                "enricher: %s timed out after %.0fs",
                source.name, self.config.per_source_timeout,
            )
            return None
        except Exception:
            _log.exception("enricher: %s raised", source.name)
            return None

    async def aclose(self) -> None:
        for src in self._sources:
            try:
                await src.close()
            except Exception:
                pass


def _build_default_sources(config: EnrichmentConfig) -> list[MetaSource]:
    out: list[MetaSource] = []
    for name in config.priority:
        if name in config.disabled_sources:
            continue
        cls = _SOURCE_REGISTRY.get(name)
        if cls is None:
            _log.warning("enricher: unknown source %r in priority list", name)
            continue
        out.append(cls())
    return out


def _merge_records(
    into: Optional[MetaRecord], new: MetaRecord
) -> MetaRecord:
    """First-non-None-wins merge.

    `into` is the accumulator (highest-priority so far); `new` is
    the next source's result. When a field is already populated on
    `into`, keep it. Confidence takes the max so we can stop once
    any source is above the threshold.
    """
    if into is None:
        return new

    def _pick(a, b):
        return a if a not in (None, "", []) else b

    into.title = _pick(into.title, new.title)
    if not into.authors:
        into.authors = list(new.authors)
    into.series = _pick(into.series, new.series)
    into.series_index = _pick(into.series_index, new.series_index)
    into.description = _pick(into.description, new.description)
    into.isbn = _pick(into.isbn, new.isbn)
    into.publisher = _pick(into.publisher, new.publisher)
    into.pub_date = _pick(into.pub_date, new.pub_date)
    into.page_count = _pick(into.page_count, new.page_count)
    into.language = _pick(into.language, new.language)
    if not into.tags:
        into.tags = list(new.tags)
    # Cover preference: stick with the current cover (higher-priority
    # source) unless it's empty. Highest-priority non-empty wins.
    into.cover_url = _pick(into.cover_url, new.cover_url)
    # Confidence is a max over all sources — any strong match boosts
    # our belief that the merged record is correct.
    into.confidence = max(into.confidence, new.confidence)
    return into

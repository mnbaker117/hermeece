"""
Amazon metadata source — web scraping.

Scrapes Amazon's Kindle Store search + product detail pages. No API
key required — uses UA spoofing to get through basic bot detection.

Two-pass flow:
  1. Search: `amazon.com/s?k={title}+{author}&i=digital-text` → find
     the ASIN of the best matching result by title similarity.
  2. Detail: `amazon.com/dp/{ASIN}` → extract rich metadata from the
     RPI carousel cards (series, page count, pub date, language) +
     detail bullets (ASIN, ISBN-13) + description + cover image.

The RPI carousel cards (`#rpi-attribute-*`) are the cleanest and
most stable selectors on Amazon product pages — more structured than
the legacy detail-bullets list and less likely to break across
redesigns.

Amazon aggressively blocks automated requests. This source uses
cloudscraper like Kobo to handle Cloudflare challenges. If Amazon
starts returning CAPTCHAs consistently, this source degrades
gracefully (returns None) and the enricher falls through to the
next provider.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Optional

from app.metadata.record import MetaRecord
from app.metadata.sources.base import MetaSource

_log = logging.getLogger("hermeece.metadata.amazon")

_SEARCH_URL = "https://www.amazon.com/s"
_PRODUCT_URL = "https://www.amazon.com/dp"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)


def _create_scraper():
    try:
        import cloudscraper
        return cloudscraper.create_scraper(
            browser={"custom": _UA},
        )
    except ImportError:
        _log.warning("cloudscraper not installed — Amazon source disabled")
        return None


class AmazonSource(MetaSource):
    name = "amazon"
    default_timeout = 30.0

    def __init__(self, *, rate_limit: float = 2.0):
        super().__init__(rate_limit=rate_limit)
        self._session = None

    def _get_session(self):
        if self._session is None:
            self._session = _create_scraper()
        return self._session

    def _fetch_sync(self, url: str, params: dict = None) -> Optional[str]:
        session = self._get_session()
        if not session:
            return None
        time.sleep(self.rate_limit)
        try:
            headers = {
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
            r = session.get(
                url, params=params, headers=headers,
                timeout=self.default_timeout,
            )
            if r.status_code == 200:
                return r.text
            _log.info("amazon: HTTP %d for %s", r.status_code, url)
            return None
        except Exception as e:
            _log.debug("amazon fetch error: %s", e)
            return None

    async def _fetch(self, url: str, params: dict = None) -> Optional[str]:
        return await asyncio.to_thread(self._fetch_sync, url, params)

    async def search_book(
        self, title: str, author: str
    ) -> Optional[MetaRecord]:
        if not title:
            return None

        # Search the Kindle Store for the book.
        query = f"{title} {author}".strip()
        search_html = await self._fetch(
            _SEARCH_URL,
            params={"k": query, "i": "digital-text"},
        )
        if not search_html:
            return None

        from bs4 import BeautifulSoup
        from app.metadata.scoring import score_match

        soup = BeautifulSoup(search_html, "lxml")
        results = soup.select("[data-asin]")

        best_asin = None
        best_score = 0.0
        for r in results:
            asin = r.get("data-asin", "").strip()
            if not asin:
                continue
            title_el = r.select_one("h2 a span, .a-text-normal")
            if not title_el:
                continue
            result_title = title_el.get_text(strip=True)
            sc = score_match(
                record_title=result_title,
                record_authors=[],
                search_title=title,
                search_authors=author,
            )
            if sc > best_score:
                best_asin = asin
                best_score = sc

        if not best_asin or best_score < 0.25:
            return None

        # Fetch the product detail page.
        detail_html = await self._fetch(f"{_PRODUCT_URL}/{best_asin}")
        if not detail_html:
            return None

        return _parse_detail_page(detail_html, best_asin)

    async def close(self) -> None:
        self._session = None
        await super().close()


def _parse_detail_page(html_text: str, asin: str) -> Optional[MetaRecord]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, "lxml")

    # Title.
    title_el = soup.select_one("#productTitle")
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        return None

    # RPI carousel cards — the cleanest selectors on the page.
    rpi = {}
    for card in soup.select("[id^='rpi-attribute-']"):
        card_id = card.get("id", "")
        val_el = (
            card.select_one(".rpi-attribute-value a span")
            or card.select_one(".rpi-attribute-value span")
        )
        lab_el = card.select_one(".rpi-attribute-label span")
        rpi[card_id] = {
            "value": val_el.get_text(strip=True) if val_el else "",
            "label": lab_el.get_text(strip=True) if lab_el else "",
        }

    # Series from RPI.
    series_name = None
    series_index = None
    series_card = rpi.get("rpi-attribute-book_details-series", {})
    if series_card.get("value"):
        series_name = series_card["value"]
        label = series_card.get("label", "")
        m = re.search(r"Book\s+(\d+(?:\.\d+)?)", label)
        if m:
            try:
                series_index = float(m.group(1))
            except ValueError:
                pass

    # Strip series from title if present.
    if series_name and series_name in title:
        title = re.sub(
            r"\s*\(" + re.escape(series_name) + r"[^)]*\)\s*$", "", title
        ).strip()

    # Page count.
    pages = None
    pages_card = rpi.get("rpi-attribute-book_details-ebook_pages", {})
    if pages_card.get("value"):
        m = re.search(r"(\d+)", pages_card["value"])
        if m:
            pages = int(m.group(1))

    # Publication date.
    pub_date = None
    date_card = rpi.get("rpi-attribute-book_details-publication_date", {})
    if date_card.get("value"):
        pub_date = _parse_amazon_date(date_card["value"])

    # Language.
    language = None
    lang_card = rpi.get("rpi-attribute-language", {})
    if lang_card.get("value"):
        language = lang_card["value"]

    # ISBN-13 + ASIN from detail bullets.
    isbn = None
    for li in soup.select(
        "#detailBulletsWrapper_feature_div li, "
        "#detailBullets_feature_div li"
    ):
        spans = li.select("span.a-text-bold")
        for s in spans:
            label = s.get_text(strip=True).replace("\u200f", "").replace("\u200e", "")
            val_span = s.find_next_sibling("span")
            val = val_span.get_text(strip=True) if val_span else ""
            if "ISBN-13" in label and val:
                isbn = val.replace("-", "")
            # Also try pub date from bullets as fallback.
            if "Publication date" in label and val and not pub_date:
                pub_date = _parse_amazon_date(val)

    # Description.
    description = None
    desc_el = soup.select_one(
        "#bookDescription_feature_div .a-expander-content"
    )
    if desc_el:
        description = desc_el.get_text(strip=True)[:2000]

    # Cover image.
    cover_url = None
    for sel in ("#imgBlkFront", "#ebooksImgBlkFront", "#landingImage"):
        img = soup.select_one(sel)
        if img:
            cover_url = img.get("src") or ""
            if cover_url:
                # Upgrade to larger image: strip size constraints.
                cover_url = re.sub(
                    r"\._[A-Z][A-Z0-9_]+_\.", ".", cover_url
                )
                break

    return MetaRecord(
        title=title,
        authors=[],  # Amazon search doesn't reliably surface in a parseable way
        series=series_name,
        series_index=series_index,
        description=description,
        isbn=isbn,
        pub_date=pub_date,
        page_count=pages,
        language=language,
        cover_url=cover_url,
        source="amazon",
        source_url=f"https://www.amazon.com/dp/{asin}",
        external_id=asin,
    )


def _parse_amazon_date(text: str) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %Y", "%b %Y", "%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

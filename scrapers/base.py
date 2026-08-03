"""Shared base scraper and utilities for site-specific scrapers.

This provides a lightweight `BaseScraper` that site scrapers can subclass.
It includes a simple retry/backoff helper and a standard `getting_data`
pipeline that fetches an index, expands URLs, fetches pages concurrently,
parses and normalizes results.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, Iterable, List, Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


async def _retry(coro_func: Callable[..., Any], *args, max_retries: int = 3, backoff_factor: float = 0.5, **kwargs):
    """Simple retry helper with exponential backoff and jitter.

    Args:
        coro_func: coroutine function to call
        max_retries: number of attempts (including first)
        backoff_factor: base wait in seconds; actual wait = backoff_factor * (2 ** n) + jitter
    """
    attempt = 0
    while True:
        try:
            return await coro_func(*args, **kwargs)
        except Exception as exc:
            attempt += 1
            if attempt >= max_retries:
                logger.debug("Retry exhausted: %s", exc)
                raise
            # exponential backoff with small jitter
            wait = backoff_factor * (2 ** (attempt - 1))
            wait = wait + random.uniform(0, wait * 0.1)
            logger.debug("Retry %d/%d failed: %s; sleeping %.2fs", attempt, max_retries, exc, wait)
            await asyncio.sleep(wait)


class BaseScraper:
    """Base class for scrapers.

    Subclasses should override `fetch_index`, `parse_index`, and `parse_page`.
    The `getting_data` method provides a standard pipeline and concurrency
    controls.
    """

    name: str = "base"

    def __init__(
        self,
        index_url: str,
        days_back: int = 30,
        threshold: float = 0.7,
        session: Optional[aiohttp.ClientSession] = None,
        timeout: int = 30,
        max_retries: int = 3,
        concurrency: int = 8,
    ) -> None:
        self.index_url = index_url
        self.days_back = days_back
        self.threshold = threshold
        self._session = session
        self._own_session = session is None
        self.timeout = timeout
        self.max_retries = max_retries
        self.concurrency = concurrency

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; Opportunities-Details/1.0; +https://example.com)"
            )
        }

    # -- Subclass hooks -------------------------------------------------
    async def fetch_index(self) -> str:
        """Fetch the index/sitemap content and return raw text.

        Subclasses may override to implement custom index retrieval.
        """
        return await self._get(self.index_url)

    async def parse_index(self, index_content: str) -> Iterable[str]:
        """Parse `index_content` and yield site page URLs.

        Must be implemented by subclasses.
        """
        raise NotImplementedError()

    async def parse_page(self, page_content: str, url: str) -> Optional[dict]:
        """Given the HTML/text of a page, return a normalized dict or None.

        Must be implemented by subclasses.
        """
        raise NotImplementedError()

    async def normalize(self, item: dict) -> dict:
        """Optional post-processing / normalization of parsed items."""
        return item

    # -- Internal helpers -----------------------------------------------
    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout, headers=self.headers)
            self._own_session = True
        return self._session

    async def _close_session(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, url: str) -> str:
        """Fetch URL and return text, with retries."""
        session = await self._ensure_session()

        async def _do_get(u: str):
            async with session.get(u) as resp:
                resp.raise_for_status()
                return await resp.text()

        return await _retry(_do_get, url, max_retries=self.max_retries, backoff_factor=0.5)

    # -- Pipeline -------------------------------------------------------
    async def getting_data(self) -> List[dict]:
        """High level pipeline: fetch index → expand urls → fetch & parse pages.

        Returns list of normalized item dicts.
        """
        logger.info("%s: starting getting_data", self.name)
        session = await self._ensure_session()
        try:
            index_content = await self.fetch_index()
            urls = list(await self.parse_index(index_content))
            logger.info("%s: discovered %d urls", self.name, len(urls))

            sem = asyncio.Semaphore(self.concurrency)

            async def _fetch_and_parse(u: str):
                async with sem:
                    try:
                        text = await _retry(self._get, u, max_retries=self.max_retries, backoff_factor=0.5)
                        parsed = await self.parse_page(text, u)
                        if parsed:
                            return await self.normalize(parsed)
                    except Exception as exc:
                        logger.exception("%s: error fetching/parsing %s: %s", self.name, u, exc)
                return None

            tasks = [asyncio.create_task(_fetch_and_parse(u)) for u in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            items = [r for r in results if isinstance(r, dict)]

            logger.info("%s: finished, items=%d", self.name, len(items))
            return items
        finally:
            await self._close_session()


def extract_links_from_sitemap(xml_text: str) -> List[str]:
    """Utility: extract <loc> entries from sitemap XML/text."""
    soup = BeautifulSoup(xml_text, "lxml-xml")
    return [loc.text.strip() for loc in soup.find_all("loc") if loc.text]

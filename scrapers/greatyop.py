import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import icecream as ic
from slugify import slugify # It's python-slugify not slugify (pip3 install python-slugify not pip3 install slugify)
import asyncio
import trafilatura
import traceback
import aiohttp
import json

class GreatYopScraper:
    def __init__(self, index_url, days_back=30, threshold=0.7):
        self.index_url        = index_url
        self.days_back        = days_back
        self.threshold        = threshold
        self.latest_url       = None
        self.links            = []
        self.raw              = []
        self.normalized       = []
        self.slugs            = []
        self.slug_tokens      = []
        self.unique_urls      = []
        self.duplicates       = []

        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/113.0.0.0 Safari/537.36'
            )
        }

    async def get_latest_post_sitemap(self, session):
        """Fetch the sitemap_index and pick the post-sitemap with the highest value of N"""
        async with session.get(self.index_url, headers=self.headers) as resp:
            content = await resp.read()
            ctype = resp.headers.get('Content-Type', '')
            parser = 'lxml-xml' if 'xml' in ctype else 'html.parser'
            soup = BeautifulSoup(content, parser)

        max_n = -1
        for loc in soup.find_all('loc'):
            m = re.search(r'post-sitemap(\d*)\.xml$', loc.text)
            if m:
                n = int(m.group(1) or 0)
                if n > max_n:
                    max_n = n
                    self.latest_url = loc.text

        ic.ic(self.latest_url)
        if not self.latest_url:
            raise RuntimeError("No post-sitemap found in index!")
        return self.latest_url

    async def dump_recent_links(self, session):
        """Extract only <loc> URLs with <lastmod> in the past `days_back` days."""
        if not self.latest_url:
            await self.get_latest_post_sitemap(session)

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        
        async with session.get(self.latest_url, headers=self.headers) as resp:
            content = await resp.read()
            soup = BeautifulSoup(content, 'lxml-xml')

        self.links = []
        for url in soup.find_all('url'):
            lm = url.find('lastmod')
            if not lm:
                continue
            try:
                # Ensure we handle timezones correctly for comparison
                dt = datetime.fromisoformat(lm.text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                
                if dt >= cutoff:
                    self.links.append(url.find('loc').text)
            except ValueError:
                continue

        ic.ic(len(self.links))
        return self.links

    @staticmethod
    def normalize_url(link):
        return link.lower().rstrip('/')

    @staticmethod
    def slugify_links(u):
        seg = u.split("/")[-1]
        s   = slugify(seg)
        return re.sub(r'-(\d{4}|\d{4}-\d{2}-\d{2})$', '', s)

    @staticmethod
    def jaccard(a, b):
        return len(a & b) / len(a | b) if (a or b) else 0.0

    async def process(self, session):
        """Full pipeline: detect sitemap → dump recent → normalize → slugify → deduplicate."""
        # Fetch & filter
        await self.dump_recent_links(session)

        # Clean & normalize
        self.raw        = [ln.strip() for ln in self.links if ln.strip()]
        self.normalized = [self.normalize_url(ln) for ln in self.raw]

        # Slugify & tokenize
        self.slugs       = [self.slugify_links(ln) for ln in self.normalized]
        self.slug_tokens = [set(slug.split('-')) for slug in self.slugs]

        # Deduplicate by Jaccard with seen_tokens
        self.unique_urls = []
        self.duplicates  = []
        seen_tokens      = []

        for link, tokens in zip(self.normalized, self.slug_tokens):
            if not any(self.jaccard(tokens, prev) >= self.threshold for prev in seen_tokens):
                self.unique_urls.append(link)
                seen_tokens.append(tokens)
            else:
                self.duplicates.append(link)

        # ic.ic(self.duplicates)
        return self.unique_urls
    
    async def fetch_url(self, index, session, url):
        count = 0
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        try:
            # Add delay to avoid rate limiting
            await asyncio.sleep(1.0)
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                page_data = await response.text()
            end_result = trafilatura.extract(page_data, include_comments=False)
            # Fix: Derive name directly from the URL to avoid index mismatch after filtering
            name = self.slugify_links(url).replace("-", " ")
            if end_result:
                end_result = end_result.replace('\n', ' ')
                count += 1
                return {
                    "name": name,
                    "url": url,
                    "content": end_result
                }
            # ic.ic(f"Total processed: {count}")
        except Exception as e:
            print(f"Error processing {url}: {e}")
            traceback.print_exc()
        return None
    


    async def getting_data(self):
        timeout = aiohttp.ClientTimeout(total=50)
        # Use only 5 connection per host to avoid rate limiting (sequential)
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=10, ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            final_urls = await self.process(session)
            tasks =  [self.fetch_url(index, session, url) for index, url in enumerate(final_urls)]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            result  = [item for item in responses if item and not isinstance(item, Exception)]

        # with open("sampleofydict.txt", "w", encoding='utf-8') as f:
        #     json.dump(result, f, indent=2)
        print(f"Total items fetched | greatyop: {len(result)}")
        return result

if __name__ == '__main__':
    scraper = GreatYopScraper(
        index_url='https://greatyop.com/sitemap_index.xml',
        days_back=30,
        threshold=0.7
    )
    # unique, dup = scraper.process()
    # print(f"Unique URLs: {len(unique)}, Duplicates: {len(dup)}")

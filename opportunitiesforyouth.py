import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
from slugify import slugify
import icecream as ic
import json
import aiohttp
import asyncio
import traceback
import trafilatura
import time

class OpportunitiesForYouth:
    def __init__(self, sitemap_url, days_back=30, threshold=0.7):
        self.sitemap_url = sitemap_url
        self.days_back   = days_back
        self.threshold   = threshold
        self.links       = []
        self.raw         = []
        self.normalized  = []
        self.slugs       = []
        self.slug_tokens = []
        self.unique_urls = []
        self.duplicates  = []

    def dump_links(self):
        headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36'
        }
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        soup = BeautifulSoup(requests.get(self.sitemap_url, headers=headers).content, 'lxml-xml')

        self.links = []
        for url in soup.find_all('url'):
            lm  = url.find('lastmod')
            loc = url.find('loc').text
            if lm and datetime.fromisoformat(lm.text) >= cutoff:
                self.links.append(loc)
        return self.links

    @staticmethod
    def normalize_url(link):
        """Lowercase & strip trailing slash."""
        return link.lower().rstrip('/')

    @staticmethod
    def slugify_links(u):
        seg = u.split("/")[-1]
        s   = slugify(seg)
        return re.sub(r'-(\d{4}|\d{4}-\d{2}-\d{2})$', '', s)

    @staticmethod
    def jaccard(a, b):
        return len(a & b) / len(a | b) if (a or b) else 0.0

    def process(self):
        # Fetch & filter
        self.dump_links()

        # Clean & normalize
        self.raw        = [ln.strip() for ln in self.links if ln.strip()]
        self.normalized = [self.normalize_url(ln) for ln in self.raw]

        # Slugify & tokenize
        self.slugs       = [self.slugify_links(ln) for ln in self.normalized]
        self.slug_tokens = [set(slug.split('-')) for slug in self.slugs]

        # Deduplicate by Jaccard with seen_tokens
        self.unique_urls = []
        self.duplicates  = []
        seen_tokens = []

        for link, tokens in zip(self.normalized, self.slug_tokens):
            if not any(self.jaccard(tokens, prev) >= self.threshold for prev in seen_tokens):
                self.unique_urls.append(link)
                seen_tokens.append(tokens)
            else:
                self.duplicates.append(link)
            
        # ic.ic(self.unique_urls)

        return self.unique_urls
 
    async def fetch_url(self, index, session, url):
        count = 0
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36'
        }
        try:
            # Add delay to avoid rate limiting
            await asyncio.sleep(1.0)
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                page_data = await response.text()
            end_result = trafilatura.extract(page_data, include_comments=False)
            name = self.slugs[index].replace("-", " ")
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
        ic.ic("Getting Data")
        final_urls = self.process()
        timeout = aiohttp.ClientTimeout(total=30)
        # Use only 1 connection per host to avoid rate limiting (sequential)
        connector = aiohttp.TCPConnector(limit=1, limit_per_host=1, ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            tasks =  [self.fetch_url(index, session, url) for index, url in enumerate(final_urls)]
            responses = await asyncio.gather(*tasks)
            result  = [item for item in responses if item]

        with open("sampleofydict.txt", "w", encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        print(f"Total items fetched: {len(result)}")
        return result

if __name__ == '__main__':
    ofy = OpportunitiesForYouth(
        sitemap_url='https://opportunitiesforyouth.org/sitemap-1.xml',
        days_back=30,
        threshold=0.7
    )
    # unique, dup = ofy.process()
    # print(f"Found {len(unique)} unique links, {len(dup)} duplicates.")
    asyncio.run(ofy.getting_data())


from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
from slugify import slugify
import aiohttp
import asyncio
import trafilatura

class OpportunitiesForYouth:
    def __init__(self, index_url, days_back=30, threshold=0.7):
        self.index_url   = index_url
        self.days_back   = days_back
        self.threshold   = threshold
        self.links       = []
        self.raw         = []
        self.normalized  = []
        self.slugs       = []
        self.slug_tokens = []
        self.unique_urls = []
        self.duplicates  = []

    async def dump_links(self, session):
        headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36'
        }
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        async with session.get(self.index_url, headers=headers) as resp:
            content = await resp.read()
        soup = BeautifulSoup(content, 'lxml-xml')

        self.links = []
        for url in soup.find_all('url'):
            lm  = url.find('lastmod')
            loc = url.find('loc')
            if lm and loc:
                try:
                    dt = datetime.fromisoformat(lm.text)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt >= cutoff:
                        self.links.append(loc.text)
                except ValueError:
                    continue
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

    async def process(self, session):
        # Fetch & filter
        await self.dump_links(session)

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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        try:
            # Stagger delays based on index to ensure a clean 2-second gap between requests
            await asyncio.sleep(index * 2.0)
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                page_data = await response.text()
            end_result = trafilatura.extract(page_data, include_comments=False)
            # Fix: Derive name directly from the URL to avoid index mismatch
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
        except asyncio.TimeoutError:
            print(f"Error processing {url}: Timeout error")
        except aiohttp.ClientError as e:
            print(f"Error processing {url}: {type(e).__name__}")
        except Exception as e:
            print(f"Error processing {url}: {type(e).__name__}")
        return None
    


    async def getting_data(self):
        # Allow sufficient time for the staggered sequential requests
        timeout = aiohttp.ClientTimeout(total=90)
        # Use only 1 connection per host to avoid rate limiting (sequential)
        connector = aiohttp.TCPConnector(limit=1, limit_per_host=1, ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            final_urls = await self.process(session)
            tasks =  [self.fetch_url(index, session, url) for index, url in enumerate(final_urls)]
            responses = await asyncio.gather(*tasks)
            result  = [item for item in responses if item]

        # with open("sampleofydict.txt", "w", encoding='utf-8') as f:
        #     json.dump(result, f, indent=2)
        print(f"Total items fetched | opportunitiesforyouth: {len(result)}")
        return result

if __name__ == '__main__':
    ofy = OpportunitiesForYouth(
        index_url='https://opportunitiesforyouth.org/sitemap-1.xml',
        days_back=30,
        threshold=0.7
    )
    # unique, dup = ofy.process()
    # print(f"Found {len(unique)} unique links, {len(dup)} duplicates.")
    # asyncio.run(ofy.getting_data())


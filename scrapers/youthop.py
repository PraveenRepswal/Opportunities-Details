import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import icecream as ic
from slugify import slugify
import asyncio
import trafilatura
import aiohttp
import warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')


class YouthOP:

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
        """Fetch the sitemap_index and pick the post-sitemap with the highest value of N."""
        async with session.get(self.index_url, headers=self.headers, ssl=False) as resp:
            status = resp.status
            ctype = resp.headers.get('Content-Type', '')
            content = await resp.read()
            
            if status != 200:
                print(f"[YouthOP] Index fetch failed with status {status}")
                raise RuntimeError(f"Index fetch failed: {status}")
            
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
        
        # If no post-sitemap found in index, try fallback probe
        if not self.latest_url:
            print("[YouthOP] No post-sitemap in index, trying fallback probe...")
            base = "https://www.youthop.com/post-sitemap"
            for suffix in ["7", "6", "5", "4", "3", "2", "1", ""]:
                test_url = f"{base}{suffix}.xml" if suffix else f"{base}.xml"
                try:
                    async with session.head(test_url, headers=self.headers, ssl=False, timeout=aiohttp.ClientTimeout(total=5)) as probe:
                        if probe.status == 200:
                            print(f"[YouthOP] Found via fallback: {test_url}")
                            self.latest_url = test_url
                            break
                except Exception:
                    pass
        
        if not self.latest_url:
            raise RuntimeError("No post-sitemap found in index!")
        return self.latest_url

    async def dump_recent_links(self, session):
        """Extract only <loc> URLs with <lastmod> in the past `days_back` days."""
        if not self.latest_url:
            await self.get_latest_post_sitemap(session)

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        
        async with session.get(self.latest_url, headers=self.headers, ssl=False) as resp:
            content = await resp.read()
            soup = BeautifulSoup(content, 'lxml-xml')

        self.links = []
        for url in soup.find_all('url'):
            lm = url.find('lastmod')
            if not lm:
                continue
            try:
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
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        try:
            # Reduced delay for faster processing
            await asyncio.sleep(0.1)
            async with session.get(url, headers=headers, ssl=False) as response:
                response.raise_for_status()
                page_data = await response.text()
            end_result = trafilatura.extract(page_data, include_comments=False)
            # Fix: Derive name directly from the URL to avoid index mismatch
            name = self.slugify_links(url).replace("-", " ")
            if end_result:
                end_result = end_result.replace('\n', ' ')
                return {
                    "name": name,
                    "url": url,
                    "content": end_result
                }
        except asyncio.TimeoutError:
            print(f"Error processing {url}: Timeout error")
        except aiohttp.ClientError as e:
            print(f"Error processing {url}: {type(e).__name__}")
        except Exception as e:
            print(f"Error processing {url}: {type(e).__name__}")
        return None

    async def getting_data(self):
        timeout = aiohttp.ClientTimeout(total=100)
        connector = aiohttp.TCPConnector(limit=15, limit_per_host=10, ssl=False)
        
        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                # Retry logic for sitemap processing
                final_urls = None
                for attempt in range(3):
                    try:
                        final_urls = await self.process(session)
                        if final_urls:
                            print(f"[YouthOP] Successfully found {len(final_urls)} unique URLs")
                            break
                    except RuntimeError as e:
                        if attempt < 2:
                            print(f"[YouthOP] Attempt {attempt + 1} failed: {e}. Retrying in 2s...")
                            await asyncio.sleep(2)
                        else:
                            print(f"[YouthOP] All attempts failed: {e}")
                            return []
                    except Exception as e:
                        print(f"[YouthOP] Unexpected error during process: {type(e).__name__}: {e}")
                        if attempt < 2:
                            await asyncio.sleep(2)
                        else:
                            return []
                
                if not final_urls:
                    print("[YouthOP] No URLs found in date range")
                    return []
                
                # Fetch content from URLs with 60-second time limit
                # External timeout is 70s, leaving 10s buffer for sitemap/cleanup
                print(f"[YouthOP] Starting content fetch for {len(final_urls)} URLs (60s limit)...")
                tasks = [asyncio.create_task(self.fetch_url(index, session, url)) 
                         for index, url in enumerate(final_urls)]
                
                # Wait for tasks with timeout - get as many as possible in 60 seconds
                try:
                    done, pending = await asyncio.wait(tasks, timeout=60)
                    
                    # Cancel any remaining tasks
                    if pending:
                        print(f"[YouthOP] Timeout reached. Cancelling {len(pending)} remaining tasks...")
                        for task in pending:
                            task.cancel()
                        # Wait for cancellations to complete
                        await asyncio.gather(*pending, return_exceptions=True)
                    
                    # Collect successful results from completed tasks
                    result = []
                    for task in done:
                        try:
                            item = task.result()
                            if item and not isinstance(item, Exception):
                                result.append(item)
                        except Exception as e:
                            print(f"[YouthOP] Task error: {type(e).__name__}")
                            pass
                
                except Exception as e:
                    print(f"[YouthOP] Error during wait: {type(e).__name__}: {e}")
                    # Cancel all tasks
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    return []

            print(f"Total items fetched | youthop: {len(result)} (out of {len(final_urls)} URLs)")
            return result
            
        except asyncio.TimeoutError:
            print("[YouthOP] Overall timeout exceeded")
            return []
        except Exception as e:
            print(f"[YouthOP] Fatal error: {type(e).__name__}: {e}")
            return []

if __name__ == '__main__':
    scraper = YouthOP(
        index_url='https://www.youthop.com/sitemap_index.xml',
        days_back=30,
        threshold=0.7
    )
    # asyncio.run(scraper.getting_data())


import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
from slugify import slugify
import icecream as ic
import trafilatura
import json
import aiohttp
import asyncio

class OpportunitiesCorners:
    def __init__(self, index_url, days_back, threshold):
        self.index_url   = index_url
        self.days_back   = days_back
        self.threshold   = threshold
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/113.0.0.0 Safari/537.36'
            )
        }
        self.latest_url  = None
        self.links       = []
        self.raw         = []
        self.normalized  = []
        self.slugs       = []
        self.slug_tokens = []
        self.unique_urls = []
        self.duplicates  = []

    async def get_latest_post_sitemap(self, session):
        """Fetch the sitemap_index and pick the post-sitemap with the highest value of N."""
        async with session.get(self.index_url, headers=self.headers) as resp:
            content = await resp.read()
            content_type = resp.headers.get('Content-Type', '')
        parser = 'lxml-xml' if 'xml' in content_type else 'html.parser'
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

    async def dump_links(self, session):
        if not self.latest_url:
            await self.get_latest_post_sitemap(session)
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        ic.ic(f"Cutoff date: {cutoff}")
        
        try:
            async with session.get(self.latest_url, headers=self.headers) as response:
                response.raise_for_status()
                content = await response.read()
                content_type = response.headers.get('Content-Type', '')
            
            # Check if the response is XML or HTML
            if 'xml' in content_type or content.strip().startswith(b'<?xml'):
                soup = BeautifulSoup(content, 'lxml-xml')
            else:
                # Fallback for HTML sitemaps (like Yoast sometimes serves)
                soup = BeautifulSoup(content, 'html.parser')
                
        except Exception as e:
            ic.ic(f"Error fetching sitemap: {e}")
            return []

        self.links = []
        
        # Handle standard XML sitemap
        for url in soup.find_all('url'):
            lm  = url.find('lastmod')
            loc = url.find('loc')
            if loc:
                loc_text = loc.text
                if lm:
                    try:
                        dt = datetime.fromisoformat(lm.text)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt >= cutoff:
                            self.links.append(loc_text)
                    except ValueError:
                        continue
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
        return len(a & b) / len(a | b) if a or b else 0.0

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
        seen_tokens      = []

        for link, tokens in zip(self.normalized, self.slug_tokens):
            # compare against token that were already accepted
            if not any(self.jaccard(tokens, prev) >= self.threshold for prev in seen_tokens):
                self.unique_urls.append(link)
                seen_tokens.append(tokens)
            else:
                self.duplicates.append(link)

        # ic.ic(self.duplicates)
        return self.unique_urls

    # def save(self, filepath='testSLUG.txt'):
    #     with open(filepath, 'w', encoding='utf-8') as f:
    #         f.write('\n'.join(self.unique_urls))

    async def fetch_url(self, index, session, url):
        count = 0
        try:
            async with session.get(url) as response:
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
        # ic.ic(f"Found {len(final_urls)} unique URLs to fetch")
        timeout = aiohttp.ClientTimeout(total=20)
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=7, ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            final_urls = await self.process(session)
            tasks =  [self.fetch_url(index, session, url) for index, url in enumerate(final_urls)]
            responses = await asyncio.gather(*tasks)
            result  = [item for item in responses if item]

            # for url in final_urls:
            #     try:
            #         # response = requests.get(url)
            #         async with session.get(url) as response:
            #             response.raise_for_status()
            #             page_data = await response.text()
            #         end_result = trafilatura.extract(page_data, include_comments=False)
            #         if end_result:
            #             item = {
            #                 "url": url,
            #                 "content": end_result
            #             }
            #             result.append(item)
            #             count += 1
            #     except Exception as e:
            #         print(f"Error processing {url}: {e}")
        # with open("sample.txt", "a", encoding='utf-8') as f:
        #     f.write(end_result + "\n\n\n")
        # with open("opportunitiescorner.txt", "w", encoding='utf-8') as f:
        #     json.dump(result, f, indent=2)
        # ic.ic(f"Type of data: {type(result)}")
        # print(f"Total processed: {count}")
        # ic.ic(type(result))
        # ic.ic(type(result[0]))
        # print(f"Type of result: {type(result)}")
        print(f"Total items fetched | opportunitiescorners: {len(result)}")
        return result




if __name__ == '__main__':
    oc = OpportunitiesCorners(
        index_url='https://opportunitiescorners.com/sitemap_index.xml',
        days_back=30,   
        threshold=0.7
    )
    # unique, dup = oc.process()
    # print(f"Unique URLs: {len(unique)}, Duplicates: {len(dup)}")
    asyncio.run(oc.getting_data())
    # oc.save('testSLUG.txt')

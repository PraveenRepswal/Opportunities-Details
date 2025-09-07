import traceback
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
    def __init__(self, sitemap_url, days_back, threshold):
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
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/113.0.0.0 Safari/537.36'
            )
        }
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        soup   = BeautifulSoup(
            requests.get(self.sitemap_url, headers=headers).content,
            'lxml-xml'
        )

        self.links = []
        for url in soup.find_all('url'):
            lm  = url.find('lastmod')
            loc = url.find('loc').text
            if lm and datetime.fromisoformat(lm.text) >= cutoff:
                self.links.append(loc)

        # ic.ic(len(self.links))
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
            name = self.slugs[index].replace("-", " ")
            if end_result:
                end_result = end_result.replace('\n', ' ')
                count += 1
                return {
                    "name": name,
                    "url": url,
                    "content": end_result
                }
            ic.ic(f"Total processed: {count}")
        except Exception as e:
            print(f"Error processing {url}: {e}")
            traceback.print_exc()
        return None
    


    async def getting_data(self):
        ic.ic("Starting end process")
        final_urls = self.process()
        timeout = aiohttp.ClientTimeout(total=20)
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=7)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
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
        with open("sampledict.txt", "w", encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        ic.ic(f"Type of data: {type(result)}")
        # print(f"Total processed: {count}")
        # ic.ic(type(result))
        # ic.ic(type(result[0]))
        print(f"Type of result: {type(result)}")
        return result




if __name__ == '__main__':
    oc = OpportunitiesCorners(
        sitemap_url='https://opportunitiescorners.com/post-sitemap.xml',
        days_back=30,   
        threshold=0.7
    )
    # unique, dup = oc.process()
    # print(f"Unique URLs: {len(unique)}, Duplicates: {len(dup)}")
    asyncio.run(oc.getting_data())
    # oc.save('testSLUG.txt')

import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from slugify import slugify # It's python-slugify not slugify (pip3 install python-slugify not pip3 install slugify)
import icecream as ic

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

    def get_latest_post_sitemap(self):
        """Fetch the sitemap_index and pick the post-sitemap with the highest value of N."""
        resp = requests.get(self.index_url, headers=self.headers)
        parser = 'lxml-xml' if 'xml' in resp.headers.get('Content-Type', '') else 'html.parser'
        soup = BeautifulSoup(resp.content, parser)

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

    def dump_recent_links(self):
        """Extract only <loc> URLs with <lastmod> in the past `days_back` days."""
        if not self.latest_url:
            self.get_latest_post_sitemap()

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        soup = BeautifulSoup(requests.get(self.latest_url, headers=self.headers).content, 'lxml-xml')

        self.links = []
        for url in soup.find_all('url'):
            lm = url.find('lastmod')
            if not lm:
                continue
            if datetime.fromisoformat(lm.text) >= cutoff:
                self.links.append(url.find('loc').text)

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

    def process(self):
        """Full pipeline: detect sitemap → dump recent → normalize → slugify → deduplicate."""
        # Fetch & filter
        self.dump_recent_links()

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
        return self.unique_urls, self.duplicates

if __name__ == '__main__':
    scraper = GreatYopScraper(
        index_url='https://greatyop.com/sitemap_index.xml',
        days_back=30,
        threshold=0.7
    )
    unique, dup = scraper.process()
    print(f"Unique URLs: {len(unique)}, Duplicates: {len(dup)}")

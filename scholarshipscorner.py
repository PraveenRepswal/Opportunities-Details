import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import icecream as ic

def get_latest_post_sitemap(index_url):
    """Fetch sitemap_index.xml, find all post-sitemapN.xml, and return the URL with the highest N."""
        
        
    headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36'
    }

    resp = requests.get(index_url, headers=headers)
    soup = BeautifulSoup(resp.content, 'lxml-xml')


    max_n = -1
    latest_url = None

    for loc in soup.find_all('loc'):
        m = re.search(r'post-sitemap(\d*)\.xml$', loc.text)
        
        if m:
            n = int(m.group(1)) if m.group(1) else 0 
            if n > max_n:
                max_n = n
                latest_url = loc.text

    ic.ic(latest_url)


    if not latest_url:
        raise RuntimeError("No post-sitemap found in index!")
    return latest_url

def dump_recent_links(sitemap_url, output_file, days_back=30):
    """Extract only <loc> URLs with <lastmod> in the past `days_back` days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    soup   = BeautifulSoup(requests.get(sitemap_url).content, 'lxml-xml')
    

    links = []
    links.append(str(cutoff) +  '\n')
    for url in soup.find_all('url'):
        lm = url.find('lastmod')
        if not lm:
            continue
        dt = datetime.fromisoformat(lm.text)
        if dt >= cutoff:
            links.append(url.find('loc').text)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(links))

if __name__ == '__main__':
    INDEX_URL = 'https://scholarshipscorner.website/sitemap_index.xml'

    latest = get_latest_post_sitemap(INDEX_URL)
    print(f"✨ Found latest post-sitemap: {latest}")

    
    dump_recent_links(latest, 'scholarshipscorner-last30d-links.txt')


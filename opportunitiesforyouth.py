import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re



def dump_links(sitemap_url, output_file, days_back=30):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    soup   = BeautifulSoup(requests.get(sitemap_url).content, 'lxml-xml')

    links = []
    for url in soup.find_all('url'):
        lm  = url.find('lastmod')
        loc = url.find('loc').text
        if lm and datetime.fromisoformat(lm.text) >= cutoff:
            links.append(loc)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(links))

if __name__ == '__main__':
    dump_links(
        'https://opportunitiesforyouth.org/sitemap-1.xml',
        'opportunitiesforyouth-month-links.txt'
    )

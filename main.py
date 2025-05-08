import os
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import time
import re
import icecream as ic
from datetime import datetime, timedelta, timezone
from slugify import slugify


def dump_links(sitemap_url, days_back=30):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    soup   = BeautifulSoup(requests.get(sitemap_url).content, 'lxml-xml')

    links = []
    for url in soup.find_all('url'):
        lm  = url.find('lastmod')
        loc = url.find('loc').text
        if lm and datetime.fromisoformat(lm.text) >= cutoff:
            links.append(loc)

    # with open(output_file, 'w', encoding='utf-8') as f:
    #     f.write('\n'.join(links))\
    ic.ic(links)
    return links




def normalize_url(link):
    return link.lower().rstrip('/')


def slugify_links(u):
    seg = u.split("/")[-1]
    s   = slugify(seg)
    # remove trailing 4‑digit years or full dates
    return re.sub(r'-(\d{4}|\d{4}-\d{2}-\d{2})$', '', s)




#Jaccard function to compare two sets   
def jaccard(a, b):
    return len(a&b) / len(a|b) 




if __name__ == '__main__':
    dump_links(
        'https://opportunitiescorners.com/post-sitemap.xml',
        'opportunitiescorners-month-links.txt'
    )

# with open('opportunitiescorners-month-links.txt', 'r', encoding='utf-8') as f:
#     raw = [link.strip() for link in f if link.strip()]

# norm_link = [normalize_url(link) for link in raw]




# slugs = [slugify_links(link) for link in norm_link]

# slug_tokens = [set(s.split('-')) for s in slugs]





threshold = 0.6
unique_urls = []
seen_tokens = []
duplicate = []

# for link, tokens in zip(norm_link, slug_tokens):
    
#     # check if this tokens set is too similar to any we've kept
#     if not any(jaccard(tokens, prev) >= threshold for prev in seen_tokens):
#         unique_urls.append(link)
#         seen_tokens.append(tokens)
#     else:
#         duplicate.append(link)

ic.ic(duplicate)
with open('testSLUG.txt', 'w') as f:
    f.write('\n'.join(unique_urls))
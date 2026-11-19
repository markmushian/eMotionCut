# -*- coding: utf-8 -*-
"""Small stdlib RSS/Atom parser: preserve provenance instead of scraping XML with regex."""
import html
import re
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
import xml.etree.ElementTree as ET


def clean_text(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', text or ''))).strip()


def canonical_url(url, base=''):
    full = urlparse(urljoin(base, url.strip()))
    if full.scheme not in ('http', 'https') or not full.netloc:
        return ''
    query = [(k, v) for k, v in parse_qsl(full.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunparse((full.scheme.lower(), full.netloc.lower(), full.path or '/', '', urlencode(query), ''))


def parse_feed(raw, source, limit=12):
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('资讯源内容过大')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', raw, re.I):
        raise ValueError('不接受带外部实体的资讯源')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    local = lambda tag: tag.rsplit('}', 1)[-1].lower()
    nodes = [n for n in root.iter() if local(n.tag) in ('item', 'entry')]
    items, seen = [], set()
    for node in nodes:
        fields = {}
        links = []
        for child in node:
            tag = local(child.tag)
            text = ''.join(child.itertext())
            fields.setdefault(tag, text)
            if tag == 'link':
                href = child.get('href') or text
                if child.get('rel', 'alternate') == 'alternate':
                    links.insert(0, href)
                else:
                    links.append(href)
        title = clean_text(fields.get('title', ''))
        link = next((x for x in links if x.strip()), fields.get('guid', ''))
        url = canonical_url(link, source) if link else ''
        if not title or not url or url in seen:
            continue
        seen.add(url)
        items.append({'text': title, 'url': url, 'source': source,
                      'published': clean_text(fields.get('pubdate') or fields.get('published') or fields.get('updated') or fields.get('date', '')),
                      'summary': clean_text(fields.get('description') or fields.get('summary') or fields.get('encoded') or '')[:500]})
        if len(items) >= limit:
            break
    return items

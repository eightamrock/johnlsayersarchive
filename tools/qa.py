#!/usr/bin/env python3
"""
Phase 8 — checks the built site/ before it is published.

  1. Every internal link and image resolves to a file in site/, and every
     #p<post_id> anchor exists on the page it points to.
  2. Privacy scan: no email addresses, IPv4 addresses, phpBB session ids
     (sid=) or phone numbers anywhere in the generated pages. Any hit fails.
  3. The {{REMOVAL_CONTACT}} placeholder has been replaced.

Exits non-zero if anything fails. Prints the final counts either way.

Usage: python3 tools/qa.py
"""
import html
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SITE = os.path.join(ROOT, 'site')

LINK_RE = re.compile(r'(?:href|src)="(/[^"]*)"')
ID_RE = re.compile(r'id="(p\d+)"')

sys.path.insert(0, os.path.join(ROOT, 'tools'))
from lib import pii  # noqa: E402

# Same patterns the build's final scrub uses (tools/lib/pii.py), so a pass
# here means that scrub caught everything. See pii.is_personal_ipv4 for which
# dotted numbers count as a person's IP address.
EMAIL_RE = pii.STRICT_EMAIL_RE
IPV4_RE = pii.IPV4_RE
SID_RE = pii.SID_RE
SITE_DOMAIN = 'johnlsayersarchive.com'
# North American and international-style numbers; same shapes tools/lib/pii.py scrubs.
PHONE_RE = re.compile(r'(?<![\w/.-])(?:\+\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w/-])')

# Pagefind's own bundle and the old-link map are machine data, not page text.
SKIP_DIRS = {'pagefind', 'img', 'files', 'static'}


def page_files():
    for dirpath, dirnames, filenames in os.walk(SITE):
        rel = os.path.relpath(dirpath, SITE)
        if rel.split(os.sep)[0] in SKIP_DIRS:
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.endswith('.html'):
                yield os.path.join(dirpath, fn)


def target_file(path):
    path = path.split('#', 1)[0].split('?', 1)[0]
    if path.endswith('/'):
        return os.path.join(SITE, path.strip('/'), 'index.html')
    return os.path.join(SITE, path.lstrip('/'))


def visible_text(raw):
    """Page text plus attribute values a reader could see (href/src/alt/title)."""
    return html.unescape(raw)


def main():
    failures = []
    pages = sorted(page_files())
    print(f'Checking {len(pages):,} pages...')

    id_cache = {}

    def ids_for(f):
        if f not in id_cache:
            with open(f, encoding='utf-8') as fh:
                id_cache[f] = set(ID_RE.findall(fh.read()))
        return id_cache[f]

    broken = Counter()
    broken_examples = {}
    privacy = Counter()
    privacy_examples = []
    placeholder_pages = 0
    links_checked = 0
    exists_cache = {}

    for f in pages:
        with open(f, encoding='utf-8') as fh:
            raw = fh.read()
        if '{{REMOVAL_CONTACT}}' in raw:
            placeholder_pages += 1
        for link in LINK_RE.findall(raw):
            if link.startswith('//'):
                continue
            links_checked += 1
            tf = target_file(link)
            ok = exists_cache.get(tf)
            if ok is None:
                ok = os.path.isfile(tf)
                exists_cache[tf] = ok
            if ok and '#p' in link:
                ok = link.split('#', 1)[1] in ids_for(tf)
            if not ok:
                broken[link] += 1
                broken_examples.setdefault(link, os.path.relpath(f, SITE))

        text = visible_text(raw)
        for label, rx in (('email', EMAIL_RE), ('ipv4', IPV4_RE), ('sid', SID_RE), ('phone', PHONE_RE)):
            for m in rx.finditer(text):
                if label == 'ipv4' and not pii.is_personal_ipv4(text, m):
                    continue
                if label == 'email' and m.group(0).lower().endswith('@' + SITE_DOMAIN):
                    continue  # the archive's own removal-request address
                privacy[label] += 1
                if len(privacy_examples) < 40:
                    ctx = text[max(0, m.start() - 50):m.end() + 30].replace('\n', ' ')
                    privacy_examples.append((label, os.path.relpath(f, SITE), ctx))

    print(f'  {links_checked:,} internal links and images checked.')
    if broken:
        failures.append(f'{len(broken)} broken internal links')
        for link, n in broken.most_common(15):
            print(f'  BROKEN {link} ({n}x, e.g. on {broken_examples[link]})')
    if privacy:
        failures.append('privacy scan hits: ' + ', '.join(f'{k} {v}' for k, v in privacy.items()))
        for label, page, ctx in privacy_examples:
            print(f'  PRIVACY [{label}] {page}: …{ctx}…')
    if placeholder_pages:
        failures.append(f'removal contact placeholder still on {placeholder_pages:,} pages (set JLS_REMOVAL_CONTACT)')

    # --- final counts ---------------------------------------------------------
    threads = sum(1 for p in pages if re.search(r'/t/\d+/index\.html$', p))
    thread_pages = sum(1 for p in pages if '/t/' in p and '/key-posts/' not in p)
    imgs = len(os.listdir(os.path.join(SITE, 'img'))) if os.path.isdir(os.path.join(SITE, 'img')) else 0
    files = len(os.listdir(os.path.join(SITE, 'files'))) if os.path.isdir(os.path.join(SITE, 'files')) else 0
    placeholders = 0
    posts = 0
    removed = 0
    for p in pages:
        if '/t/' not in p or '/key-posts/' in p:
            continue
        with open(p, encoding='utf-8') as fh:
            raw = fh.read()
        posts += raw.count('<article class="post"')
        placeholders += raw.count('class="bb-attachment-missing"') + raw.count('class="bb-external-img"')
        removed += raw.count('[removed]')
    print('\nFinal counts')
    print(f'  threads:              {threads:,} ({thread_pages:,} pages)')
    print(f'  posts:                {posts:,}')
    print(f'  images on the site:   {imgs:,} (plus {files} recovered files)')
    print(f'  lost-image notices:   {placeholders:,}')
    print(f'  contact details removed from posts: {removed:,}')
    pf = os.path.join(SITE, 'pagefind', 'pagefind-entry.json')
    if os.path.exists(pf):
        entry = json.load(open(pf))
        n = sum(v.get('page_count', 0) for v in entry.get('languages', {}).values())
        print(f'  search index:         {n:,} pages')
    else:
        failures.append('no Pagefind search index (run tools/build.py without --no-search)')

    if failures:
        print('\nQA FAILED:')
        for f in failures:
            print(f'  - {f}')
        sys.exit(1)
    print('\nQA passed.')


if __name__ == '__main__':
    main()

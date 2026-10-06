#!/usr/bin/env python3
"""
Phase 3b — External image recovery from the Wayback Machine.

tools/03_wayback.py (Phase 3a) recovers images uploaded through phpBB's own
attachment system. Many posts also hotlink external images via
[img]URL[/img] pointing at third-party hosts (Photobucket, ImageShack,
personal sites, dead file.php links on other forums, etc) — about 11.6k
distinct URLs across the kept forums (see PLAN.md section 4, "lower
priority; do it after the attachments"). Most of these hosts are long
dead, but Wayback may have crawled the image directly regardless.

This script:
  1. Scans every kept post's post_text for [img]...[/img] targets, decodes
     phpBB's numeric-entity URL obfuscation (e.g. "&#58;" -> ":"), dedupes,
     and drops anything pointing back at johnlsayers.com itself (already
     handled as an attachment, not a hotlink).
  2. Fetches each surviving URL directly via Wayback's "id_" shorthand,
     same technique as tools/03_wayback.py.
  3. Saves recovered bytes to data/images/orig/ext_<hash>.<ext> (same
     directory as recovered attachments, so the existing preview/img
     symlink covers both) and logs a row to data/recovered_external.csv
     (url, local_path, bytes).

Resumable: URLs already in data/recovered_external.csv are skipped.
Rate-limited like tools/03_wayback.py (~1 req/sec, be polite to
archive.org). ~11.6k URLs is a few hours — run in the background.

Usage:
    python3 tools/08_wayback_external_images.py [--limit N]
"""
import csv
import hashlib
import html
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.join(os.path.dirname(__file__), '..')
DB_PATH = os.path.join(ROOT, 'data', 'archive.db')
OUT_DIR = os.path.join(ROOT, 'data', 'images', 'orig')
RECOVERED_CSV = os.path.join(ROOT, 'data', 'recovered_external.csv')
LOG_PATH = os.path.join(ROOT, 'data', 'wayback_external_fetch.log')

REQUEST_DELAY = 1.0
MAX_RETRIES = 5
MIN_BYTES = 400  # skip likely 1x1 tracking pixels / "hotlinking disabled" placeholder graphics

IMG_RE = re.compile(r'\[img(?::\S+?)?\](.*?)\[/img(?::\S+?)?\]', re.I | re.S)

EXT_BY_MIME = {
    'image/jpeg': 'jpg', 'image/pjpeg': 'jpg', 'image/png': 'png',
    'image/x-png': 'png', 'image/gif': 'gif', 'image/bmp': 'bmp',
    'image/jp2': 'jp2', 'image/webp': 'webp',
}


def url_key(url):
    return hashlib.sha1(url.encode('utf-8', 'replace')).hexdigest()[:16]


def extract_external_urls(conn):
    urls = set()
    for (text,) in conn.execute('SELECT post_text FROM phpbb_posts'):
        if not text or '[img' not in text.lower():
            continue
        for raw in IMG_RE.findall(text):
            u = html.unescape(raw).strip()
            if not u or 'johnlsayers' in u.lower():
                continue
            if not u.lower().startswith(('http://', 'https://')):
                continue
            urls.add(u)
    return sorted(urls)


def load_already_done():
    done = set()
    if os.path.exists(RECOVERED_CSV):
        with open(RECOVERED_CSV, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                done.add(row['url'])
    return done


def fetch(session, url, log):
    wb_url = f'https://web.archive.org/web/2id_/{url}'
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(wb_url, timeout=30, headers={'User-Agent': 'johnlsayersarchive-recovery/1.0'})
        except Exception as e:
            log.write(f'EXC {url} attempt {attempt}: {e}\n')
            time.sleep(REQUEST_DELAY * attempt)
            continue
        if r.status_code == 200 and r.content:
            ctype = r.headers.get('Content-Type', '').split(';')[0].strip().lower()
            if not ctype.startswith('image/'):
                log.write(f'NON-IMAGE {url} content-type={ctype!r}, skipping\n')
                return None
            if len(r.content) < MIN_BYTES:
                log.write(f'TOO-SMALL {url} {len(r.content)} bytes, skipping\n')
                return None
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            log.write(f'{r.status_code} {url} attempt {attempt}, backing off\n')
            time.sleep(REQUEST_DELAY * (2 ** attempt))
            continue
        log.write(f'{r.status_code} {url} giving up\n')
        return None
    return None


def main():
    import requests

    limit = None
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])

    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    all_urls = extract_external_urls(conn)
    done = load_already_done()
    todo = [u for u in all_urls if u not in done]
    if limit:
        todo = todo[:limit]

    print(f'{len(all_urls)} distinct external image URLs, {len(done)} already recovered, {len(todo)} to fetch now.')

    session = requests.Session()
    ok = 0
    fail = 0
    write_header = not os.path.exists(RECOVERED_CSV)
    with open(RECOVERED_CSV, 'a', newline='', encoding='utf-8') as csvf, \
         open(LOG_PATH, 'a', encoding='utf-8') as log:
        w = csv.writer(csvf)
        if write_header:
            w.writerow(['url', 'local_path', 'bytes'])
        for i, url in enumerate(todo, 1):
            r = fetch(session, url, log)
            time.sleep(REQUEST_DELAY)
            if r is None:
                fail += 1
                continue
            ctype = r.headers.get('Content-Type', '').split(';')[0].strip()
            ext = EXT_BY_MIME.get(ctype, 'jpg')
            fname = f'ext_{url_key(url)}.{ext}'
            local_path = os.path.join(OUT_DIR, fname)
            with open(local_path, 'wb') as imgf:
                imgf.write(r.content)
            w.writerow([url, f'data/images/orig/{fname}', len(r.content)])
            csvf.flush()
            ok += 1
            if i % 100 == 0:
                print(f'  {i}/{len(todo)} processed ({ok} ok, {fail} failed)')

    print(f'\nDone. {ok} recovered this run, {fail} failed this run.')
    print(f'Total recovered so far: {len(done) + ok}')
    print(f'See {RECOVERED_CSV} and {LOG_PATH}')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Phase 3 — Image recovery from the Wayback Machine.

data/wayback_attachment_captures.txt lists johnlsayers.com attachment
download URLs (file.php?id=N&sid=...) that the CDX API confirmed were
captured with a 200 status and a non-HTML mimetype (built during planning,
see PLAN.md section 4). This script:

  1. Dedupes that list down to one URL per attach_id.
  2. Cross-references against data/archive.db to keep only attach_ids that
     belong to a post we actually kept (excluded-forum attachments were
     already dropped in Phase 1, so most ids should match).
  3. Fetches each one directly via Wayback's "id_" shorthand
     (https://web.archive.org/web/2id_/<url>), which resolves to the
     nearest real snapshot of that exact URL without a separate per-id CDX
     lookup — halving the request count vs. a CDX-then-fetch two-step.
  4. Saves the raw bytes to data/images/orig/<attach_id>.<ext> and logs a
     row to data/recovered.csv (attach_id, local_path, source_url, bytes).

Resumable: already-downloaded attach_ids (present in data/recovered.csv)
are skipped on a re-run. Rate-limited to be polite to archive.org, with
retry/backoff on 429/5xx.

Usage:
    python3 tools/03_wayback.py                 # attachments (default)
    python3 tools/03_wayback.py --limit 50       # smoke-test on first 50
"""
import csv
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

ROOT = os.path.join(os.path.dirname(__file__), '..')
DB_PATH = os.path.join(ROOT, 'data', 'archive.db')
CAPTURES_TXT = os.path.join(ROOT, 'data', 'wayback_attachment_captures.txt')
OUT_DIR = os.path.join(ROOT, 'data', 'images', 'orig')
RECOVERED_CSV = os.path.join(ROOT, 'data', 'recovered.csv')
LOG_PATH = os.path.join(ROOT, 'data', 'wayback_fetch.log')

REQUEST_DELAY = 1.0  # seconds between requests, be polite
MAX_RETRIES = 5

ID_RE = re.compile(r'[?&]id=(\d+)')

EXT_BY_MIME = {
    'image/jpeg': 'jpg', 'image/pjpeg': 'jpg', 'image/png': 'png',
    'image/x-png': 'png', 'image/gif': 'gif', 'image/bmp': 'bmp',
    'image/jp2': 'jp2', 'application/octet-stream': 'bin',
}


def load_candidates():
    """Returns dict attach_id -> original_url, deduped (first seen wins,
    preferring www. host since that's what the forum's own links used)."""
    by_id = {}
    with open(CAPTURES_TXT, encoding='utf-8') as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            url = parts[0]
            m = ID_RE.search(url)
            if not m:
                continue
            aid = int(m.group(1))
            if aid not in by_id or 'www.' in url:
                by_id[aid] = url
    return by_id


def load_kept_attachments(conn):
    """attach_id -> (real_filename, extension) for attachments Phase 1 kept."""
    out = {}
    for aid, fname, ext in conn.execute(
            'SELECT attach_id, real_filename, extension FROM phpbb_attachments'):
        out[aid] = (fname, ext)
    return out


def load_already_done():
    done = set()
    if os.path.exists(RECOVERED_CSV):
        with open(RECOVERED_CSV, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                try:
                    done.add(int(row['attach_id']))
                except (KeyError, ValueError):
                    pass
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

    candidates = load_candidates()
    kept = load_kept_attachments(conn)
    done = load_already_done()

    todo = [(aid, url) for aid, url in sorted(candidates.items())
            if aid in kept and aid not in done]
    if limit:
        todo = todo[:limit]

    print(f'{len(candidates)} candidate ids from Wayback capture list, '
          f'{len(kept)} attachments kept from Phase 1, '
          f'{len(done)} already recovered, {len(todo)} to fetch now.')

    session = requests.Session()
    ok = 0
    fail = 0
    write_header = not os.path.exists(RECOVERED_CSV)
    with open(RECOVERED_CSV, 'a', newline='', encoding='utf-8') as csvf, \
         open(LOG_PATH, 'a', encoding='utf-8') as log:
        w = csv.writer(csvf)
        if write_header:
            w.writerow(['attach_id', 'local_path', 'source_url', 'bytes'])
        for i, (aid, url) in enumerate(todo, 1):
            r = fetch(session, url, log)
            time.sleep(REQUEST_DELAY)
            if r is None:
                fail += 1
                continue
            ctype = r.headers.get('Content-Type', '').split(';')[0].strip()
            _, db_ext = kept.get(aid, (None, None))
            ext = (db_ext or '').lower().strip('.') or EXT_BY_MIME.get(ctype, 'bin')
            fname = f'{aid}.{ext}'
            local_path = os.path.join(OUT_DIR, fname)
            with open(local_path, 'wb') as imgf:
                imgf.write(r.content)
            rel_path = os.path.join('data', 'images', 'orig', fname)
            w.writerow([aid, rel_path, url, len(r.content)])
            csvf.flush()
            ok += 1
            if i % 100 == 0:
                print(f'  {i}/{len(todo)} processed ({ok} ok, {fail} failed)')

    print(f'\nDone. {ok} recovered this run, {fail} failed this run.')
    print(f'Total recovered so far: {len(done) + ok}')
    print(f'See {RECOVERED_CSV} and {LOG_PATH}')


if __name__ == '__main__':
    main()

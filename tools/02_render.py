#!/usr/bin/env python3
"""
Phase 2 — Render.

1. Runs the bbcode/PII renderer over a representative sample of threads
   (stickies, top-viewed, the hidden thread, and a random cross-section) and
   writes a bare, unstyled preview site to preview/ for the owner to review.
2. Writes data/pii_review.csv logging every PII match found IN THE SAMPLE
   (run with --full to scrub-scan and log the whole corpus without writing
   pages, for a complete PII audit ahead of the real Phase 6 build).

This is a checkpoint script, not the final site builder (that's Phase 6 /
tools/build.py, which will reuse tools/lib/bbcode.py and tools/lib/linkmap.py
against the full corpus plus curation.json and real templates).

Usage:
    python3 tools/02_render.py                 # sample preview (default)
    python3 tools/02_render.py --full-pii-scan  # scan all posts, CSV only
"""
import csv
import html
import os
import random
import sqlite3
import sys

RECOVERED_CSV_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'recovered.csv')
RECOVERED_EXTERNAL_CSV_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'recovered_external.csv')


def load_recovered_lookup():
    """attach_id -> web path under the preview's /img/ symlink (-> data/images/orig/)."""
    lookup = {}
    if not os.path.exists(RECOVERED_CSV_PATH):
        return lookup
    with open(RECOVERED_CSV_PATH, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            aid = int(row['attach_id'])
            fname = os.path.basename(row['local_path'])
            lookup[aid] = f'/img/{fname}'
    return lookup


def load_recovered_external_lookup():
    """Exact hotlinked [img] URL text -> web path under the preview's /img/ symlink."""
    lookup = {}
    if not os.path.exists(RECOVERED_EXTERNAL_CSV_PATH):
        return lookup
    with open(RECOVERED_EXTERNAL_CSV_PATH, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            fname = os.path.basename(row['local_path'])
            lookup[row['url']] = f'/img/{fname}'
    return lookup

sys.path.insert(0, os.path.dirname(__file__))
from lib import bbcode, linkmap  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), '..')
DB_PATH = os.path.join(ROOT, 'data', 'archive.db')
PREVIEW_DIR = os.path.join(ROOT, 'preview')
PII_CSV = os.path.join(ROOT, 'data', 'pii_review.csv')

PAGE_SIZE = linkmap.PAGE_SIZE
random.seed(20220320)  # deterministic sample, matches the dump date for no reason but reproducibility


def pick_sample_topic_ids(conn):
    ids = set()

    # all stickies/announcements
    ids.update(r[0] for r in conn.execute(
        "SELECT topic_id FROM phpbb_topics WHERE topic_type IN (1,2) ORDER BY topic_views DESC LIMIT 20"))

    # top-viewed threads overall (the big build diaries)
    ids.update(r[0] for r in conn.execute(
        "SELECT topic_id FROM phpbb_topics ORDER BY topic_views DESC LIMIT 20"))

    # the hidden forum's one thread
    ids.update(r[0] for r in conn.execute("SELECT topic_id FROM phpbb_topics WHERE forum_id=25"))

    # a random cross-section for coverage (short threads, list/code-heavy, etc.)
    all_ids = [r[0] for r in conn.execute("SELECT topic_id FROM phpbb_topics")]
    ids.update(random.sample(all_ids, min(15, len(all_ids))))

    return sorted(ids)


def load_attachments(conn):
    by_post = {}
    for r in conn.execute('SELECT * FROM phpbb_attachments'):
        by_post.setdefault(r['post_msg_id'], []).append(dict(r))
    return by_post


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


PAGE_CSS = """
body{font-family:Georgia,serif;max-width:800px;margin:2em auto;padding:0 1em;line-height:1.5;color:#222}
.post{border:1px solid #ccc;margin:1.5em 0;padding:1em;white-space:pre-wrap}
/* blockquotes should also preserve the quoted author's paragraph breaks —
   pre-wrap inherits from .post, which is correct, not overridden here.
   <pre> (code blocks) keeps the browser's own default white-space:pre,
   which wins over the inherited pre-wrap since it's set directly on the
   element, so code indentation is unaffected. */
.post-meta{color:#666;font-size:0.9em;margin-bottom:0.5em;border-bottom:1px solid #eee;padding-bottom:0.5em}
.bb-quote{border-left:3px solid #999;margin:0.8em 0;padding:0.2em 1em;background:#f7f7f7}
.bb-quote-author{font-weight:bold;font-size:0.85em;color:#555}
.bb-code{background:#f0f0f0;padding:0.8em;overflow-x:auto;font-size:0.9em}
.bb-attachment-missing,.bb-external-img{color:#a00;font-style:italic;border:1px dashed #ccc;padding:0.4em;margin:0.5em 0;font-size:0.9em}
.bb-attachment img{max-width:100%}
.bb-attachments-trailing{margin-top:1em;padding-top:0.5em;border-top:1px dashed #ccc}
.dead-link{color:#a00;text-decoration:line-through}
.smilie{color:#666}
nav{margin:1em 0}
a{color:#0645ad}
.pagelist a{margin-right:0.5em}
"""


def render_thread(conn, topic_id, atts_by_post, resolver, recovered_lookup, recovered_external_lookup,
                   review_rows, out_dir, users, posts_by_topic):
    topic = conn.execute(
        'SELECT topic_id, forum_id, topic_title, topic_views, topic_replies FROM phpbb_topics WHERE topic_id=?',
        (topic_id,)).fetchone()
    if not topic:
        return
    posts = posts_by_topic.get(topic_id, [])

    pages = [posts[i:i + PAGE_SIZE] for i in range(0, len(posts), PAGE_SIZE)] or [[]]
    total_pages = len(pages)

    def page_path(n):
        # must match tools/lib/linkmap.py LinkResolver._topic_path exactly
        return f'/t/{topic_id}/' if n <= 1 else f'/t/{topic_id}/page/{n}/'

    for page_num, page_posts in enumerate(pages, start=1):
        body = [f'<h1>{html.escape(topic["topic_title"])}</h1>',
                f'<p style="color:#666">{topic["topic_views"]} views &middot; {topic["topic_replies"]} replies '
                f'&middot; page {page_num} of {total_pages}</p>',
                '<nav><a href="/index.html">&larr; preview index</a></nav>']

        for p in page_posts:
            uname = p['post_username'] or users.get(p['poster_id'], 'Anonymous')
            post_html, used = bbcode.render_post(
                p['post_id'], p['post_text'], p['bbcode_uid'],
                atts_by_post.get(p['post_id'], []), resolver,
                recovered_lookup=recovered_lookup, review_rows=review_rows,
                recovered_external_lookup=recovered_external_lookup)
            subj = html.escape(p['post_subject']) if p['post_subject'] else ''
            body.append(
                f'<div class="post" id="p{p["post_id"]}">'
                f'<div class="post-meta"><strong>{html.escape(str(uname))}</strong> '
                f'&mdash; <a href="#p{p["post_id"]}">#{p["post_id"]}</a>'
                + (f' &mdash; {subj}' if subj else '') + '</div>'
                f'{post_html}</div>')

        if total_pages > 1:
            pl = ['<div class="pagelist">']
            for n in range(1, total_pages + 1):
                if n == page_num:
                    pl.append(f'<strong>{n}</strong>')
                else:
                    pl.append(f'<a href="{page_path(n)}">{n}</a>')
            pl.append('</div>')
            body.append(''.join(pl))

        page_html = (f'<!doctype html><html><head><meta charset="utf-8">'
                     f'<title>{html.escape(topic["topic_title"])}</title>'
                     f'<style>{PAGE_CSS}</style></head><body>' + ''.join(body) + '</body></html>')
        out_path = os.path.join(out_dir, 't', str(topic_id), 'index.html') if page_num == 1 else \
            os.path.join(out_dir, 't', str(topic_id), 'page', str(page_num), 'index.html')
        write(out_path, page_html)


def main():
    full_scan = '--full-pii-scan' in sys.argv

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    loc = linkmap.build_post_location_map(conn)
    moved = linkmap.build_moved_map(conn)
    tf = linkmap.build_topic_forum_map(conn)
    resolver = linkmap.LinkResolver(loc, moved, tf).resolve
    atts_by_post = load_attachments(conn)
    recovered_lookup = load_recovered_lookup()
    recovered_external_lookup = load_recovered_external_lookup()

    review_rows = []

    if full_scan:
        print('Full-corpus PII scan (no pages written)...')
        n = 0
        for p in conn.execute('SELECT post_id, post_text, bbcode_uid FROM phpbb_posts'):
            bbcode.render_post(p['post_id'], p['post_text'], p['bbcode_uid'],
                                atts_by_post.get(p['post_id'], []), resolver,
                                recovered_lookup=recovered_lookup, review_rows=review_rows,
                                recovered_external_lookup=recovered_external_lookup)
            n += 1
        print(f'Scanned {n} posts, {len(review_rows)} PII matches logged.')
    else:
        full_archive = '--full' in sys.argv
        topic_ids = ([r[0] for r in conn.execute('SELECT topic_id FROM phpbb_topics')]
                     if full_archive else pick_sample_topic_ids(conn))
        index_topic_ids = pick_sample_topic_ids(conn)  # always used for the index page links
        recovered_count = len(recovered_lookup)
        total_attachments = conn.execute('SELECT COUNT(*) FROM phpbb_attachments').fetchone()[0]

        print(f'Loading posts and users...')
        users = dict(conn.execute('SELECT user_id, username FROM phpbb_users'))
        posts_by_topic = {}
        for p in conn.execute(
                'SELECT post_id, topic_id, poster_id, post_username, post_subject, post_text, '
                'bbcode_uid, post_time FROM phpbb_posts ORDER BY topic_id, post_time, post_id'):
            posts_by_topic.setdefault(p['topic_id'], []).append(p)

        print(f'Rendering {len(topic_ids)} threads{" (full archive)" if full_archive else " (sample)"}...')
        for i, tid in enumerate(topic_ids, 1):
            render_thread(conn, tid, atts_by_post, resolver, recovered_lookup, recovered_external_lookup,
                           review_rows, PREVIEW_DIR, users, posts_by_topic)
            if full_archive and i % 1000 == 0:
                print(f'  {i}/{len(topic_ids)}...')

        # index page (always the curated sample list — a link into every
        # kept topic isn't a useful index, readers navigate via in-post links)
        rows = []
        for tid in index_topic_ids:
            t = conn.execute(
                'SELECT topic_title, topic_views, topic_replies, topic_type, forum_id FROM phpbb_topics '
                'WHERE topic_id=?', (tid,)).fetchone()
            if not t:
                continue
            tag = {1: '[sticky] ', 2: '[announcement] ', 0: ''}.get(t['topic_type'], '')
            rows.append(f'<li><a href="/t/{tid}/">{tag}{html.escape(t["topic_title"])}</a> '
                        f'&mdash; {t["topic_views"]} views, {t["topic_replies"]} replies '
                        f'(forum_id {t["forum_id"]}, topic_id {tid})</li>')
        coverage_note = (
            'Every kept topic is rendered, so links between threads resolve.'
            if full_archive else
            f'Only this sample of {len(rows)} threads is rendered, so most links between threads will '
            f'404 — run with --full for the complete, fully-linked archive.')
        index_html = (f'<!doctype html><html><head><meta charset="utf-8"><title>Preview index</title>'
                      f'<style>{PAGE_CSS}</style></head><body>'
                      f'<h1>johnlsayersarchive.com — Phase 2 render preview</h1>'
                      f'<p>Bare, unstyled preview of the BBCode-to-HTML renderer. {coverage_note} '
                      f'{recovered_count} of {total_attachments} attachments have been recovered from the '
                      f'Wayback Machine so far (Phase 3 is still running); everything else shows a '
                      f'"not preserved" placeholder — that is expected right now, not a bug. '
                      f'Design/CSS is intentionally absent; this is only to check the text conversion.</p>'
                      f'<ul>{"".join(rows)}</ul></body></html>')
        write(os.path.join(PREVIEW_DIR, 'index.html'), index_html)
        print(f'Wrote {len(topic_ids)} rendered threads to {PREVIEW_DIR}/ ({len(rows)} linked from the index page)')

    # PII review CSV (always written from whatever we scanned)
    os.makedirs(os.path.dirname(PII_CSV), exist_ok=True)
    with open(PII_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['post_id', 'category', 'matched_text'])
        w.writerows(review_rows)
    print(f'Wrote {len(review_rows)} PII review rows to {PII_CSV}')


if __name__ == '__main__':
    main()

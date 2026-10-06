#!/usr/bin/env python3
"""
Phase 6/7 — builds the whole static site into site/, then indexes it with
Pagefind.

Inputs: data/archive.db, data/curation.json, data/candidates.jsonl,
data/recovered.csv, data/recovered_external.csv, templates/, static/.
Same inputs give the same output: nothing depends on the build time.

Every page is written as <path>/index.html with root-relative links, so the
site works on any plain web server with no rewrite rules.

Usage:
    .venv/bin/python tools/build.py              # full build + search index
    .venv/bin/python tools/build.py --no-search  # skip Pagefind
    JLS_REMOVAL_CONTACT=someone@example.com .venv/bin/python tools/build.py

The removal-request contact defaults to removals@johnlsayersarchive.com;
JLS_REMOVAL_CONTACT overrides it.
"""
import csv
import datetime
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from lib import bbcode, linkmap, pii  # noqa: E402

import jinja2  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
SITE = os.path.join(ROOT, 'site')
TEMPLATES = os.path.join(ROOT, 'templates')
STATIC = os.path.join(ROOT, 'static')

BASE_URL = 'https://johnlsayersarchive.com'
REMOVAL_CONTACT = os.environ.get('JLS_REMOVAL_CONTACT', 'removals@johnlsayersarchive.com')
PAGEFIND_VERSION = '1.3.0'

PAGE_SIZE = linkmap.PAGE_SIZE
FORUM_PAGE_SIZE = 50
TOP_EXPERTS = 50
MAX_IMAGE_DIM = 1600
KEEP_ORIGINAL_MAX_BYTES = 400_000
FEATURED_TOPIC = 18654  # the hidden "John's small studio design" build

# forum_id -> (display name, category, description). Descriptions follow the
# forum's own wording, lightly cleaned up.
FORUMS = {
    17: ('Welcome', 'Welcome', 'How the forum worked, and what to read before asking for help.'),
    1: ('Studio Design', 'Studio design and construction',
        'Plans and layouts: room shapes, where things go, where to put your near-fields.'),
    2: ('Studio Construction', 'Studio design and construction',
        'How thick should my walls be, should I float my floors, and why is two-leaf '
        'mass-air-mass design important?'),
    3: ('Acoustics', 'Studio design and construction',
        'How to use REW, what a bass trap or a diffuser is, the speed of sound.'),
    12: ('Speaking of Speakers', 'Studio design and construction', 'Speakers and speaker design.'),
    16: ('Installation and Wiring', 'Studio design and construction',
         'Three-phase power, patchbays and every other wiring question.'),
    11: ('John Sayers Productions Designed Studios', 'Studios under construction',
         'Build threads for studios designed by John Sayers Productions.'),
    10: ('Other Studios', 'Studios under construction', 'Build threads for studios designed by others.'),
    25: ('John’s small studio design', None, ''),
}
FORUM_CATEGORIES = ['Welcome', 'Studio design and construction', 'Studios under construction']

TOPICS = [
    ('fundamentals', 'Fundamentals', 'Isolation versus treatment, room within a room, and mass-air-mass resonance.'),
    ('walls-floors-ceilings', 'Walls, floors and ceilings', 'Framing, drywall, decoupling, floating floors and hangers.'),
    ('doors-windows', 'Doors and windows', 'Sealing and isolating the openings in a wall.'),
    ('hvac', 'HVAC and ventilation', 'Air conditioning, fresh air and silencer boxes.'),
    ('treatment', 'Acoustic treatment', 'Panels, bass traps, diffusers and what they are made of.'),
    ('control-room-design', 'Control room design', 'Room geometry, the reflection-free zone and speaker placement.'),
    ('speakers', 'Speakers and soffits', 'Choosing monitors and flush-mounting them in soffits.'),
    ('electrical', 'Electrical and wiring', 'Grounding, power and running cables between rooms.'),
    ('codes-permits', 'Codes and permits', 'Building codes, planning permission and inspections.'),
    ('tools-measurement', 'Measurement and tools', 'Room EQ Wizard, SketchUp and acoustic calculators.'),
    ('materials-reference', 'Materials reference', 'Densities, absorption data and product comparisons.'),
    ('build-diaries', 'Build diaries', 'Studios built from start to finish, post by post.'),
]
TOPIC_NAMES = {slug: name for slug, name, _ in TOPICS}
DIARY_SUBTAGS = [('garage', 'Garages'), ('basement', 'Basements'), ('barn-outbuilding', 'Barns and outbuildings'),
                 ('room-in-house', 'Rooms in a house'), ('commercial', 'Commercial spaces'), ('other', 'Other builds')]

MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
          'October', 'November', 'December']
TAG_RE = re.compile(r'<[^>]+>')
WS_RE = re.compile(r'\s+')
QUOTE_OPEN = '<blockquote class="bb-quote"'
# post-body uses white-space: pre-wrap, so newlines the author typed around a
# quote or image would render as empty lines next to the block's own margin.
AFTER_BLOCK_RE = re.compile(r'(</(?:blockquote|div|figure|ul|ol|pre)>)\n{1,2}')
BEFORE_BLOCK_RE = re.compile(r'\n{2,}(?=<(?:blockquote|figure|ul|ol|pre|div class="bb-))')


def tidy_post_html(h):
    h = h.replace('\r\n', '\n').replace('\r', '\n')  # about 61k posts were typed with Windows line endings
    return BEFORE_BLOCK_RE.sub('\n', AFTER_BLOCK_RE.sub(r'\1', h)).strip()


# --- small helpers -----------------------------------------------------------

def utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


def fmt_date(ts):
    d = utc(ts)
    return f'{d.day} {MONTHS[d.month - 1]} {d.year}', d.strftime('%Y-%m-%d')


def count_label(n, singular, plural=None):
    return f'{n:,} {singular if n == 1 else (plural or singular + "s")}'


def year_span(first_ts, last_ts):
    a, b = utc(first_ts).year, utc(last_ts).year
    return str(a) if a == b else f'{a}–{b}'


def and_join(names):
    if len(names) <= 1:
        return ''.join(names)
    return ', '.join(names[:-1]) + ' and ' + names[-1]


def strip_quotes(h):
    """Drop quoted blocks (nested divs) so snippets show what the poster wrote."""
    out = []
    i = 0
    while True:
        j = h.find(QUOTE_OPEN, i)
        if j == -1:
            out.append(h[i:])
            return ''.join(out)
        out.append(h[i:j])
        depth = 0
        k = j
        while k < len(h):
            if h.startswith('<blockquote', k):
                depth += 1
                k += 11
            elif h.startswith('</blockquote>', k):
                depth -= 1
                k += 13
                if depth == 0:
                    break
            else:
                k += 1
        i = k


def plain_text(h):
    return WS_RE.sub(' ', html.unescape(TAG_RE.sub(' ', h))).strip()


def snippet(text, n=170):
    if len(text) <= n:
        return text
    return text[:n].rsplit(' ', 1)[0] + '…'


def make_pager(current, total, href):
    if total <= 1:
        return None
    nums = sorted({1, total, *range(max(1, current - 2), min(total, current + 2) + 1)})
    items, prev = [], 0
    for n in nums:
        if n - prev > 1:
            items.append({'gap': True, 'n': None, 'current': False, 'href': None})
        items.append({'gap': False, 'n': n, 'current': n == current, 'href': href(n)})
        prev = n
    return {'current': current, 'total': total, 'links': items,
            'prev': href(current - 1) if current > 1 else None,
            'next': href(current + 1) if current < total else None}


def thread_href(tid, page=1):
    return f'/t/{tid}/' if page <= 1 else f'/t/{tid}/page/{page}/'


class Writer:
    def __init__(self):
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(TEMPLATES),
            autoescape=jinja2.select_autoescape(['html']),
            keep_trailing_newline=True,
            undefined=jinja2.StrictUndefined,
        )
        self.env.globals.update(base_url=BASE_URL, removal_contact=REMOVAL_CONTACT,
                                section=None, title=None, description=None, noindex=False)
        self.count = 0

    def page(self, template, path, **ctx):
        out = self.env.get_template(template).render(path=path, **ctx)
        if path.endswith('/'):
            dest = os.path.join(SITE, path.strip('/'), 'index.html')
        else:
            dest = os.path.join(SITE, path.lstrip('/'))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(out)
        self.count += 1


# --- images and files ----------------------------------------------------------

def process_image(src, stem, out_dir):
    """Web copy of one recovered image. Returns the output filename or None if unreadable."""
    if not os.path.exists(src):
        # Rebuilding from the public repo, which carries site/img but not data/images/orig.
        for ext in ('jpg', 'png', 'gif'):
            if os.path.exists(os.path.join(out_dir, f'{stem}.{ext}')):
                return f'{stem}.{ext}'
        return None
    try:
        im = Image.open(src)
        fmt = im.format
        w, h = im.size
        im.load()
    except Exception:
        return None
    if fmt in ('PNG', 'GIF') and max(w, h) <= MAX_IMAGE_DIM and os.path.getsize(src) <= KEEP_ORIGINAL_MAX_BYTES:
        name = f'{stem}.{fmt.lower()}'
        dest = os.path.join(out_dir, name)
        if not os.path.exists(dest):
            shutil.copyfile(src, dest)
        return name
    name = f'{stem}.jpg'
    dest = os.path.join(out_dir, name)
    if os.path.exists(dest):
        return name
    try:
        if getattr(im, 'is_animated', False):
            im.seek(0)
        if im.mode in ('RGBA', 'LA', 'P', 'PA'):
            im = im.convert('RGBA')
            bg = Image.new('RGB', im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert('RGB')
        im.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)
        im.save(dest, 'JPEG', quality=82, optimize=True, progressive=True)
    except Exception:
        if os.path.exists(dest):
            os.remove(dest)
        return None
    return name


def prepare_assets():
    img_dir = os.path.join(SITE, 'img')
    files_dir = os.path.join(SITE, 'files')
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(files_dir, exist_ok=True)
    keep_img, keep_files = set(), set()
    att_lookup, ext_lookup = {}, {}
    unreadable = 0

    with open(os.path.join(DATA, 'recovered.csv'), encoding='utf-8') as f:
        for row in csv.DictReader(f):
            aid = int(row['attach_id'])
            src = os.path.join(ROOT, row['local_path'])
            ext = src.rsplit('.', 1)[-1].lower()
            if ext in ('jpg', 'jpeg', 'png', 'gif', 'bmp'):
                name = process_image(src, str(aid), img_dir)
                if name:
                    att_lookup[aid] = f'/img/{name}'
                    keep_img.add(name)
                else:
                    unreadable += 1
            elif ext in ('pdf', 'skp'):
                name = f'{aid}.{ext}'
                dest = os.path.join(files_dir, name)
                if not os.path.exists(dest):
                    if not os.path.exists(src):
                        continue
                    shutil.copyfile(src, dest)
                att_lookup[aid] = f'/files/{name}'
                keep_files.add(name)
            # zip/rar/xls uploads are not republished: unknown contents.

    ext_csv = os.path.join(DATA, 'recovered_external.csv')
    if os.path.exists(ext_csv):
        with open(ext_csv, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                src = os.path.join(ROOT, row['local_path'])
                stem = os.path.basename(src).rsplit('.', 1)[0]
                name = process_image(src, stem, img_dir)
                if name:
                    ext_lookup[row['url']] = f'/img/{name}'
                    keep_img.add(name)
                else:
                    unreadable += 1

    for d, keep in ((img_dir, keep_img), (files_dir, keep_files)):
        for name in os.listdir(d):
            if name not in keep:
                os.remove(os.path.join(d, name))
    return att_lookup, ext_lookup, unreadable


# --- the build -----------------------------------------------------------------

def clean_site():
    os.makedirs(SITE, exist_ok=True)
    # Generated output; keep it out of Dropbox sync like preview/.
    subprocess.run(['xattr', '-w', 'com.dropbox.ignored', '1', SITE], check=False)
    for name in os.listdir(SITE):
        if name in ('img', 'files'):
            continue  # image work is cached between builds; stale files pruned in prepare_assets
        p = os.path.join(SITE, name)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)


def main():
    run_search = '--no-search' not in sys.argv
    clean_site()
    w = Writer()

    print('Preparing images and recovered files...')
    att_lookup, ext_lookup, unreadable = prepare_assets()
    print(f'  {len(att_lookup)} attachments and {len(ext_lookup)} linked images ready '
          f'({unreadable} recovered files were unreadable and stay as placeholders).')

    conn = sqlite3.connect(os.path.join(DATA, 'archive.db'))
    conn.row_factory = sqlite3.Row

    print('Loading the archive...')
    topics = {r['topic_id']: dict(r) for r in conn.execute('SELECT * FROM phpbb_topics')}
    title_rows = []
    for t in topics.values():
        t['topic_title'] = pii.scrub_published(pii.scrub_text(t['topic_title'], None, title_rows))
    users = dict(conn.execute('SELECT user_id, username FROM phpbb_users'))
    posts_by_topic = defaultdict(list)
    for p in conn.execute('SELECT post_id, topic_id, poster_id, post_username, post_time, post_text, bbcode_uid '
                          'FROM phpbb_posts ORDER BY topic_id, post_time, post_id'):
        posts_by_topic[p['topic_id']].append(p)
    atts_by_post = defaultdict(list)
    for r in conn.execute('SELECT * FROM phpbb_attachments ORDER BY attach_id'):
        atts_by_post[r['post_msg_id']].append(dict(r))

    loc = linkmap.build_post_location_map(conn)
    moved = linkmap.build_moved_map(conn)
    topic_forum = linkmap.build_topic_forum_map(conn)
    resolver = linkmap.LinkResolver(loc, moved, topic_forum).resolve

    poster_counts = Counter()
    for tposts in posts_by_topic.values():
        for p in tposts:
            if p['poster_id'] != 1:
                poster_counts[p['poster_id']] += 1
    experts = [uid for uid, _ in sorted(poster_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_EXPERTS]]
    expert_set = set(experts)

    def display_name(poster_id, post_username):
        if poster_id == 1 or poster_id not in users:
            name = post_username or 'Guest'
        else:
            name = users[poster_id]
        if '@' in name:  # a few members used an email address as their name
            name = name.split('@')[0] or 'Member'
        return name

    expert_slug, used_slugs = {}, set()
    for uid in experts:
        s = linkmap.slugify(display_name(uid, None))
        if s in used_slugs:
            s = f'{s}-{uid}'
        used_slugs.add(s)
        expert_slug[uid] = s
    john_id = next((uid for uid, name in users.items() if name == 'John Sayers'), None)

    curation = json.load(open(os.path.join(DATA, 'curation.json'), encoding='utf-8'))
    approved = {t['topic_id']: t for t in curation['threads'] if t['status'] == 'approved'}
    scores = {}
    with open(os.path.join(DATA, 'candidates.jsonl'), encoding='utf-8') as f:
        for line in f:
            c = json.loads(line)
            scores[c['topic_id']] = c['score']

    # --- thread pages -----------------------------------------------------------
    print(f'Rendering {len(topics)} threads...')
    info = {}
    expert_topic_posts = defaultdict(Counter)  # uid -> topic_id -> posts
    pii_rows = []
    for i, tid in enumerate(sorted(topics), 1):
        t = topics[tid]
        tposts = posts_by_topic.get(tid, [])
        forum_id = t['forum_id']
        forum_name = FORUMS[forum_id][0]
        forum_slug = linkmap.FORUM_SLUGS[forum_id]
        first = tposts[0] if tposts else None
        starter_id = first['poster_id'] if first else t['topic_poster']
        starter = display_name(starter_id, first['post_username'] if first else None)
        first_ts = first['post_time'] if first else t['topic_time']
        last_ts = tposts[-1]['post_time'] if tposts else t['topic_time']
        n = len(tposts)
        ecount = Counter(p['poster_id'] for p in tposts if p['poster_id'] in expert_set)
        for uid, c in ecount.items():
            expert_topic_posts[uid][tid] = c
        cur = approved.get(tid)
        library = {'slug': cur['primary_topic'], 'name': TOPIC_NAMES[cur['primary_topic']]} if cur else None
        has_keyposts = n > 100 or bool(cur and cur['primary_topic'] == 'build-diaries')
        replies_label = count_label(max(n - 1, 0), 'reply', 'replies')
        span = year_span(first_ts, last_ts) if utc(first_ts).year != utc(last_ts).year else ''
        started, _ = fmt_date(first_ts)
        info[tid] = {
            'id': tid, 'title': t['topic_title'], 'forum_id': forum_id, 'forum_name': forum_name,
            'forum_slug': forum_slug, 'starter': starter, 'starter_id': starter_id, 'n_posts': n,
            'views': t['topic_views'], 'first_ts': first_ts, 'last_ts': last_ts, 'topic_type': t['topic_type'],
            'replies_label': replies_label, 'years': year_span(first_ts, last_ts), 'ecount': ecount,
        }

        pages = [tposts[k:k + PAGE_SIZE] for k in range(0, n, PAGE_SIZE)] or [[]]
        total_pages = len(pages)
        keyposts = []
        for page_num, page_posts in enumerate(pages, 1):
            rendered = []
            for j, p in enumerate(page_posts):
                number = (page_num - 1) * PAGE_SIZE + j + 1
                post_html, _ = bbcode.render_post(
                    p['post_id'], p['post_text'], p['bbcode_uid'], atts_by_post.get(p['post_id'], []), resolver,
                    recovered_lookup=att_lookup, review_rows=pii_rows, recovered_external_lookup=ext_lookup)
                post_html = pii.scrub_published(tidy_post_html(post_html))
                date, iso = fmt_date(p['post_time'])
                author = display_name(p['poster_id'], p['post_username'])
                author_href = f'/people/{expert_slug[p["poster_id"]]}/' if p['poster_id'] in expert_set else None
                rendered.append({'id': p['post_id'], 'n': number, 'author': author, 'author_href': author_href,
                                 'date': date, 'iso': iso, 'html': post_html})
                if has_keyposts and (p['poster_id'] == starter_id or p['poster_id'] in expert_set):
                    own = plain_text(strip_quotes(post_html))
                    threshold = 300 if p['poster_id'] == starter_id else 500
                    if len(own) >= threshold or '<figure' in post_html:
                        keyposts.append({'page': page_num, 'href': thread_href(tid, page_num) + f'#p{p["post_id"]}',
                                         'author': author, 'date': date, 'snippet': snippet(own)})
            path = thread_href(tid, page_num)
            w.page('thread.html', path,
                   title=t['topic_title'] if page_num == 1 else f'{t["topic_title"]} (page {page_num})',
                   description=f'Started by {starter} in {forum_name}, {utc(first_ts).year}. {replies_label}.',
                   section='forum', thread_title=t['topic_title'], topic_id=tid, forum_name=forum_name,
                   forum_slug=forum_slug, starter=starter, started=started, replies_label=replies_label,
                   span=span, library=library, has_keyposts=has_keyposts and bool(keyposts),
                   pages=make_pager(page_num, total_pages, lambda k: thread_href(tid, k)), posts=rendered)
        if has_keyposts and keyposts:
            groups = []
            for kp in keyposts:
                if not groups or groups[-1]['page'] != kp['page']:
                    groups.append({'page': kp['page'], 'posts': []})
                groups[-1]['posts'].append(kp)
            w.page('keyposts.html', f'/t/{tid}/key-posts/', title=f'Key posts: {t["topic_title"]}',
                   section='forum', thread_title=t['topic_title'], topic_id=tid, forum_name=forum_name,
                   forum_slug=forum_slug, starter=starter, count=len(keyposts), total=n, groups=groups)
        info[tid]['has_keyposts'] = bool(has_keyposts and keyposts)
        if i % 2000 == 0:
            print(f'  {i}/{len(topics)}')

    def row(tid, pinned=False, exclude_starter_from_experts=True):
        t = info[tid]
        names = [display_name(uid, None) for uid, _ in
                 sorted(t['ecount'].items(), key=lambda kv: (-kv[1], kv[0]))
                 if not (exclude_starter_from_experts and uid == t['starter_id'])][:3]
        return {'id': tid, 'title': t['title'], 'replies_label': t['replies_label'], 'years': t['years'],
                'starter': t['starter'], 'forum_name': t['forum_name'], 'experts': and_join(names),
                'pinned': pinned}

    # --- forums -----------------------------------------------------------------
    print('Writing forum listings, Library, people and site pages...')
    by_forum = defaultdict(list)
    for tid, t in info.items():
        by_forum[t['forum_id']].append(tid)
    forum_cards = []
    for fid, (name, cat, desc) in FORUMS.items():
        tids = sorted(by_forum[fid], key=lambda x: (info[x]['topic_type'] == 0, -info[x]['last_ts'], x))
        slug = linkmap.FORUM_SLUGS[fid]
        chunks = [tids[k:k + FORUM_PAGE_SIZE] for k in range(0, len(tids), FORUM_PAGE_SIZE)] or [[]]

        def fhref(k, slug=slug):
            return f'/forum/{slug}/' if k <= 1 else f'/forum/{slug}/page/{k}/'
        for k, chunk in enumerate(chunks, 1):
            w.page('forum.html', fhref(k), title=name if k == 1 else f'{name} (page {k})', section='forum',
                   description=desc or None, forum_name=name, forum_desc=desc,
                   pages=make_pager(k, len(chunks), fhref), topics=[row(x) for x in chunk])
        if cat:
            forum_cards.append({'slug': slug, 'name': name, 'desc': desc, 'cat': cat,
                                'count_label': count_label(len(tids), 'thread')})
    w.page('forum_index.html', '/forum/', title='Forums', section='forum',
           categories=[{'name': c, 'forums': [f for f in forum_cards if f['cat'] == c]} for c in FORUM_CATEGORIES])

    # --- library ----------------------------------------------------------------
    lib_topics = []
    for slug, name, desc in TOPICS:
        key = curation['topics'].get(slug, {}).get('key_threads', [])
        members = [tid for tid, c in approved.items() if c['primary_topic'] == slug and tid in info]
        pinned = [tid for tid in key if tid in members]
        rest = sorted((tid for tid in members if tid not in pinned), key=lambda x: (-scores.get(x, 0), x))
        ordered = pinned + rest
        groups, subtags = [], []
        if slug == 'build-diaries':
            if pinned:
                groups.append({'name': 'Start here', 'slug': 'start-here', 'threads': [row(x, True) for x in pinned]})
            for sub, sub_name in DIARY_SUBTAGS:
                tids = [x for x in rest if approved[x]['diary_subtag'] == sub]
                if tids:
                    groups.append({'name': sub_name, 'slug': sub, 'threads': [row(x) for x in tids]})
                    subtags.append({'slug': sub, 'name': sub_name, 'count': len(tids)})
        else:
            groups.append({'name': None, 'slug': None,
                           'threads': [row(x, x in pinned) for x in ordered]})
        intro = [p.strip() for p in curation['topics'].get(slug, {}).get('intro', '').split('\n\n') if p.strip()]
        w.page('library_topic.html', f'/library/{slug}/', title=name, section='library', description=desc,
               name=name, intro=intro, groups=groups, subtags=subtags)
        lib_topics.append({'slug': slug, 'name': name, 'desc': desc, 'count': len(ordered)})
    library_count = sum(t['count'] for t in lib_topics)
    w.page('library_index.html', '/library/', title='The Library', section='library',
           topics=lib_topics, total=f'{library_count:,}')

    # --- people -----------------------------------------------------------------
    total_posts = sum(t['n_posts'] for t in info.values())
    people = []
    for uid in experts:
        tp = expert_topic_posts[uid]
        times = [p['post_time'] for tid in tp for p in posts_by_topic[tid] if p['poster_id'] == uid]
        first_year, last_year = utc(min(times)).year, utc(max(times)).year
        name = display_name(uid, None)
        started = sorted((tid for tid, t in info.items() if t['starter_id'] == uid),
                         key=lambda x: (-info[x]['views'], x))[:25]
        active = sorted(tp, key=lambda x: (-tp[x], x))[:25]
        w.page('person.html', f'/people/{expert_slug[uid]}/', title=name, section='people',
               description=f'{name} on the johnlsayers.com recording studio design forum.',
               name=name, posts_label=count_label(poster_counts[uid], 'post'),
               threads_label=count_label(len(tp), 'thread'), first_year=first_year, last_year=last_year,
               is_john=uid == john_id, started=[row(x, exclude_starter_from_experts=False) for x in started],
               active=[row(x, exclude_starter_from_experts=False) for x in active])
        people.append({'slug': expert_slug[uid], 'name': name,
                       'posts_label': count_label(poster_counts[uid], 'post'),
                       'years': f'{first_year}–{last_year}' if first_year != last_year else str(first_year)})
    share = sum(poster_counts[u] for u in experts) / total_posts
    w.page('people_index.html', '/people/', title='People', section='people',
           people=people, share=f'{round(share * 100)}%')

    # --- home, about, search, old links, 404 -------------------------------------
    n_threads, n_posts = len(info), total_posts
    members = len(poster_counts)
    most_read = sorted(info, key=lambda x: (-info[x]['views'], x))[:12]
    feature = info[FEATURED_TOPIC]
    w.page('home.html', '/', threads=f'{n_threads:,}', posts=f'{n_posts:,}', topics=lib_topics,
           feature={'id': FEATURED_TOPIC, 'title': feature['title'], 'replies': max(feature['n_posts'] - 1, 0),
                    'helper': display_name(max((u for u in feature['ecount'] if u != feature['starter_id']),
                                               key=lambda u: (feature['ecount'][u], -u)), None)},
           most_read=[{'id': x, 'title': info[x]['title'], 'views_label': count_label(info[x]['views'], 'view'),
                       'replies_label': info[x]['replies_label']} for x in most_read],
           forums=[{'slug': f['slug'], 'name': f['name'], 'count_label': f['count_label']} for f in forum_cards])
    w.page('about.html', '/about/', title='About this archive', section='about', threads=f'{n_threads:,}',
           posts=f'{n_posts:,}', members=f'{members:,}', library_count=f'{library_count:,}',
           recovered_attachments=f'{len(att_lookup):,}', recovered_external=f'{len(ext_lookup):,}')
    w.page('search.html', '/search/', title='Search', noindex=True)
    w.page('oldlink.html', '/old-link/', title='Find an old link')
    w.page('404.html', '/404.html', title='Page not found', noindex=True)

    old_map = {
        'page_size': PAGE_SIZE,
        't': {str(tid): [p['post_id'] for p in posts_by_topic.get(tid, [])] for tid in sorted(info)},
        'moved': {str(k): v for k, v in sorted(moved.items())},
        'forums': {str(k): v for k, v in sorted(linkmap.FORUM_SLUGS.items())},
    }
    os.makedirs(os.path.join(SITE, 'old-link'), exist_ok=True)
    with open(os.path.join(SITE, 'old-link', 'map.json'), 'w', encoding='utf-8') as f:
        json.dump(old_map, f, separators=(',', ':'))

    # --- sitemap, robots, static -------------------------------------------------
    urls = ['/', '/library/', '/forum/', '/people/', '/about/', '/old-link/']
    urls += [f'/library/{s}/' for s, _, _ in TOPICS]
    urls += [f'/forum/{linkmap.FORUM_SLUGS[f]}/' for f in FORUMS]
    urls += [f'/people/{expert_slug[u]}/' for u in experts]
    entries = [(u, None) for u in urls]
    for tid in sorted(info):
        lastmod = utc(info[tid]['last_ts']).strftime('%Y-%m-%d')
        pages = max(1, -(-info[tid]['n_posts'] // PAGE_SIZE))
        entries += [(thread_href(tid, k), lastmod) for k in range(1, pages + 1)]
    chunk_size = 40000
    sitemap_files = []
    for k in range(0, len(entries), chunk_size):
        name = f'sitemap-{k // chunk_size + 1}.xml'
        sitemap_files.append(name)
        with open(os.path.join(SITE, name), 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
            for u, lastmod in entries[k:k + chunk_size]:
                f.write(f'<url><loc>{BASE_URL}{u}</loc>' + (f'<lastmod>{lastmod}</lastmod>' if lastmod else '')
                        + '</url>\n')
            f.write('</urlset>\n')
    with open(os.path.join(SITE, 'sitemap.xml'), 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for name in sitemap_files:
            f.write(f'<sitemap><loc>{BASE_URL}/{name}</loc></sitemap>\n')
        f.write('</sitemapindex>\n')
    with open(os.path.join(SITE, 'robots.txt'), 'w', encoding='utf-8') as f:
        f.write(f'User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n')
    shutil.copytree(STATIC, os.path.join(SITE, 'static'))

    print(f'Wrote {w.count:,} pages, {len(entries):,} sitemap URLs. '
          f'{len(pii_rows)} contact details replaced with [removed].')

    if run_search:
        print('Indexing for search with Pagefind...')
        subprocess.run(['npx', '-y', f'pagefind@{PAGEFIND_VERSION}', '--site', SITE], check=True)

    if REMOVAL_CONTACT == '{{REMOVAL_CONTACT}}':
        print('\nNOTE: no removal contact set (JLS_REMOVAL_CONTACT). The site is not ready to publish.')


if __name__ == '__main__':
    main()

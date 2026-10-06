#!/usr/bin/env python3
"""
Phase 4 — Ranking.

Scores every kept topic on a handful of signals (views, replies, sticky/
announcement status, participation by the top-50 posters, John Sayers'
participation, recovered-image count, and inbound internal links from
other posts), flags likely build diaries, and writes the top ~2,500 to
data/candidates.jsonl for Phase 5's topic-tagging pass.

Cheap and deterministic — safe to re-run any time (e.g. after Phase 3
finishes downloading more images) to refresh the recovered-image signal.

Usage: python3 tools/04_rank.py [--top N]
"""
import csv
import html
import json
import math
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import linkmap  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), '..')
DB_PATH = os.path.join(ROOT, 'data', 'archive.db')
RECOVERED_CSV = os.path.join(ROOT, 'data', 'recovered.csv')
OUT_PATH = os.path.join(ROOT, 'data', 'candidates.jsonl')

TOP_N_EXPERTS = 50
DEFAULT_TOP_N_CANDIDATES = 2500

DIARY_TITLE_RE = re.compile(
    r"\bbuild\b|\bdiary\b|\bconstruction\b|'s studio\b|'s garage\b|'s basement\b|build diary",
    re.IGNORECASE)


def load_recovered_counts():
    counts = {}
    if not os.path.exists(RECOVERED_CSV):
        return counts
    with open(RECOVERED_CSV, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            aid = int(row['attach_id'])
            counts[aid] = True
    return counts


def main():
    top_n = DEFAULT_TOP_N_CANDIDATES
    if '--top' in sys.argv:
        top_n = int(sys.argv[sys.argv.index('--top') + 1])

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # --- top-50 experts (post count within kept forums) --------------------
    expert_rows = conn.execute(
        'SELECT poster_id, COUNT(*) c FROM phpbb_posts GROUP BY poster_id ORDER BY c DESC LIMIT ?',
        (TOP_N_EXPERTS,)).fetchall()
    top_experts = {r['poster_id'] for r in expert_rows}
    users = dict(conn.execute('SELECT user_id, username FROM phpbb_users'))
    john_id = next((uid for uid, name in users.items() if name == 'John Sayers'), None)

    # --- recovered image counts per topic -----------------------------------
    recovered_attach_ids = load_recovered_counts()
    recovered_by_topic = {}
    for aid, topic_id in conn.execute('SELECT attach_id, topic_id FROM phpbb_attachments'):
        if aid in recovered_attach_ids:
            recovered_by_topic[topic_id] = recovered_by_topic.get(topic_id, 0) + 1

    # --- per-topic post stats (total, expert count, starter count, john) ---
    topic_poster_first = {}  # topic_id -> topic_poster (starter's user_id), from phpbb_topics
    for tid, poster in conn.execute('SELECT topic_id, topic_poster FROM phpbb_topics'):
        topic_poster_first[tid] = poster

    stats = {}  # topic_id -> dict(total, expert, starter, john)
    for tid, poster_id in conn.execute('SELECT topic_id, poster_id FROM phpbb_posts'):
        s = stats.setdefault(tid, {'total': 0, 'expert': 0, 'starter': 0, 'john': 0})
        s['total'] += 1
        if poster_id in top_experts:
            s['expert'] += 1
        if poster_id == topic_poster_first.get(tid):
            s['starter'] += 1
        if john_id is not None and poster_id == john_id:
            s['john'] += 1

    # --- inbound internal links: scan every post's text for johnlsayers.com
    #     viewtopic links, resolve to a target topic, tally distinct source
    #     topics per target (so one chatty multi-quoting thread doesn't
    #     inflate a target's count). ------------------------------------------
    loc = linkmap.build_post_location_map(conn)
    moved = linkmap.build_moved_map(conn)
    tf = linkmap.build_topic_forum_map(conn)
    resolver = linkmap.LinkResolver(loc, moved, tf)

    inbound_sources = {}  # target_topic_id -> set(source_topic_id)
    n = 0
    for post_id, topic_id, text in conn.execute('SELECT post_id, topic_id, post_text FROM phpbb_posts'):
        n += 1
        if not text or 'johnlsayers' not in text:
            continue
        decoded = html.unescape(text)
        for m in resolver.TOPIC_RE.finditer(decoded):
            q = resolver._query(m.group(1))
            target_tid = None
            if q.get('p', '').isdigit():
                pid = int(q['p'])
                if pid in loc:
                    target_tid = loc[pid][0]
            elif q.get('t', '').isdigit():
                target_tid = moved.get(int(q['t']), int(q['t']))
            if target_tid and target_tid in tf and target_tid != topic_id:
                inbound_sources.setdefault(target_tid, set()).add(topic_id)
    inbound_counts = {tid: len(srcs) for tid, srcs in inbound_sources.items()}

    # --- score every topic ---------------------------------------------------
    results = []
    for row in conn.execute(
            'SELECT topic_id, forum_id, topic_title, topic_views, topic_replies, topic_type '
            'FROM phpbb_topics'):
        tid = row['topic_id']
        s = stats.get(tid, {'total': 0, 'expert': 0, 'starter': 0, 'john': 0})
        total_posts = max(s['total'], 1)
        expert_ratio = s['expert'] / total_posts
        sticky_bonus = 3.0 if row['topic_type'] in (1, 2, 3) else 0.0
        john_bonus = 2.0 if s['john'] > 0 else 0.0
        recovered_n = recovered_by_topic.get(tid, 0)
        inbound_n = inbound_counts.get(tid, 0)

        signals = {
            'log_views': round(math.log1p(row['topic_views']), 3),
            'log_replies': round(math.log1p(row['topic_replies']), 3),
            'sticky_bonus': sticky_bonus,
            'expert_ratio': round(expert_ratio, 3),
            'john_bonus': john_bonus,
            'recovered_images': recovered_n,
            'inbound_links': inbound_n,
        }
        score = (
            1.0 * signals['log_views']
            + 1.5 * signals['log_replies']
            + signals['sticky_bonus']
            + 3.0 * signals['expert_ratio']
            + signals['john_bonus']
            + 0.3 * min(signals['recovered_images'], 10)
            + 0.8 * min(signals['inbound_links'], 10)
        )

        starter_share = s['starter'] / total_posts
        is_diary = bool(
            row['forum_id'] in (10, 11)
            or DIARY_TITLE_RE.search(row['topic_title'] or '')
            or (starter_share >= 0.30 and row['topic_replies'] > 100)
        )

        results.append({
            'topic_id': tid,
            'forum_id': row['forum_id'],
            'title': row['topic_title'],
            'views': row['topic_views'],
            'replies': row['topic_replies'],
            'topic_type': row['topic_type'],
            'score': round(score, 3),
            'signals': signals,
            'is_diary': is_diary,
        })

    results.sort(key=lambda r: r['score'], reverse=True)
    top = results[:top_n]

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        for r in top:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    diary_count = sum(1 for r in top if r['is_diary'])
    print(f'Ranked {len(results)} topics.')
    print(f'Top experts (user_ids): {len(top_experts)}; John Sayers user_id: {john_id}')
    print(f'Recovered images so far: {len(recovered_attach_ids)} (across {len(recovered_by_topic)} topics)')
    print(f'Inbound-link edges resolved: {sum(len(v) for v in inbound_sources.values())} '
          f'(to {len(inbound_counts)} distinct target topics)')
    print(f'Wrote top {len(top)} candidates to {OUT_PATH} ({diary_count} flagged as diaries)')
    print('\nTop 15 by score:')
    for r in top[:15]:
        print(f"  {r['score']:6.2f}  [{'DIARY' if r['is_diary'] else '     '}]  "
              f"{r['title'][:70]}")


if __name__ == '__main__':
    main()

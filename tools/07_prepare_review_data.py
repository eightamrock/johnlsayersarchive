#!/usr/bin/env python3
"""
Phase 5 (review, prep) — joins candidates.jsonl + tags.jsonl + topic_intros.json
+ archive.db thread metadata into a single JSON blob for the local review UI
(tools/review/index.html).

Usage: python3 tools/07_prepare_review_data.py
Writes tools/review/data.js (a `const REVIEW_DATA = {...}` script, so the
review page works from file:// without a server or fetch/CORS issues).
"""
import json
import os
import sqlite3
import sys
from importlib import import_module

sys.path.insert(0, os.path.dirname(__file__))
prep = import_module('05_prepare_batches')

ROOT = os.path.join(os.path.dirname(__file__), '..')
DB_PATH = os.path.join(ROOT, 'data', 'archive.db')
CANDIDATES_PATH = os.path.join(ROOT, 'data', 'candidates.jsonl')
TAGS_PATH = os.path.join(ROOT, 'data', 'tags.jsonl')
INTROS_PATH = os.path.join(ROOT, 'data', 'topic_intros.json')
OUT_PATH = os.path.join(ROOT, 'tools', 'review', 'data.js')

FORUM_NAMES = {
    1: 'Studio Design', 2: 'Studio Construction', 3: 'Acoustics',
    10: 'Other Studios', 11: 'JSP-designed Studios', 12: 'Speaking of Speakers',
    16: 'Installation & Wiring', 17: 'Welcome', 25: 'Hidden',
}


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    candidates = {}
    with open(CANDIDATES_PATH, encoding='utf-8') as f:
        for line in f:
            row = json.loads(line)
            candidates[row['topic_id']] = row

    tags = {}
    with open(TAGS_PATH, encoding='utf-8') as f:
        for line in f:
            row = json.loads(line)
            tags[row['topic_id']] = row

    intros = {}
    if os.path.exists(INTROS_PATH):
        with open(INTROS_PATH, encoding='utf-8') as f:
            intros = json.load(f)

    threads = []
    for tid, tag in tags.items():
        c = candidates.get(tid)
        if not c:
            continue
        threads.append({
            'id': tid,
            'title': c['title'],
            'forum': FORUM_NAMES.get(c['forum_id'], str(c['forum_id'])),
            'views': c['views'],
            'replies': c['replies'],
            'score': c['score'],
            'primary_topic': tag['primary_topic'],
            'secondary_topics': tag['secondary_topics'],
            'diary_subtag': tag['diary_subtag'],
            'is_reference_quality': tag['is_reference_quality'],
            'confidence': tag['confidence'],
        })

    threads.sort(key=lambda t: t['score'], reverse=True)

    taxonomy = [{'slug': slug, 'desc': desc} for slug, desc in prep.TAXONOMY]

    data = {
        'taxonomy': taxonomy,
        'diary_subtags': prep.DIARY_SUBTAGS,
        'intros': intros,
        'threads': threads,
        'generated_count': len(threads),
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        f.write('const REVIEW_DATA = ')
        json.dump(data, f, ensure_ascii=False)
        f.write(';\n')

    print(f'Wrote {len(threads)} threads, {len(intros)}/{len(taxonomy)} intros to {OUT_PATH}')
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f'data.js size: {size_kb:.0f} KB')


if __name__ == '__main__':
    main()

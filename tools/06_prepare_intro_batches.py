#!/usr/bin/env python3
"""
Phase 5 (intros, prep) — builds one prompt-ready text file per taxonomy
topic, asking a subagent to draft a 120-200 word AI-labelled intro plus
3-5 key threads, using only the tagged threads' own titles/content.

Usage: python3 tools/06_prepare_intro_batches.py
Writes data/intro_batches/<slug>.txt
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import bbcode, linkmap  # noqa: E402
import sqlite3
from importlib import import_module

prep = import_module('05_prepare_batches')  # TAXONOMY, html_to_text

ROOT = os.path.join(os.path.dirname(__file__), '..')
DB_PATH = os.path.join(ROOT, 'data', 'archive.db')
CANDIDATES_PATH = os.path.join(ROOT, 'data', 'candidates.jsonl')
TAGS_PATH = os.path.join(ROOT, 'data', 'tags.jsonl')
OUT_DIR = os.path.join(ROOT, 'data', 'intro_batches')

TOP_N_PER_TOPIC = 15  # threads fed as context for the intro draft


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

    loc = linkmap.build_post_location_map(conn)
    moved = linkmap.build_moved_map(conn)
    tf = linkmap.build_topic_forum_map(conn)
    resolver = linkmap.LinkResolver(loc, moved, tf).resolve

    by_topic = {slug: [] for slug, _ in prep.TAXONOMY}
    for tid, tag in tags.items():
        by_topic.setdefault(tag['primary_topic'], []).append(tid)

    os.makedirs(OUT_DIR, exist_ok=True)
    taxonomy_desc = {slug: desc for slug, desc in prep.TAXONOMY}

    written = 0
    for slug, topic_ids in by_topic.items():
        ranked = sorted(topic_ids, key=lambda t: candidates.get(t, {}).get('score', 0), reverse=True)
        top = ranked[:TOP_N_PER_TOPIC]
        if not top:
            continue

        threads_block = []
        for tid in top:
            c = candidates.get(tid, {})
            row = conn.execute(
                'SELECT post_id, post_text, bbcode_uid FROM phpbb_posts '
                'WHERE topic_id=? ORDER BY post_time, post_id LIMIT 1', (tid,)).fetchone()
            excerpt = ''
            if row:
                html, _ = bbcode.render_post(row['post_id'], row['post_text'], row['bbcode_uid'], [], resolver)
                excerpt = prep.html_to_text(html, 400)
            threads_block.append(
                f'- topic_id={tid} | "{c.get("title", "")}" | views={c.get("views", 0)} '
                f'replies={c.get("replies", 0)}\n  {excerpt}')

        prompt = f"""You are drafting a short introduction for one topic page in a curated "Library" section of a recording-studio-design forum archive. The intro must be clearly labelled as AI-drafted (that label is added by the site template, don't include it yourself), 120-200 words, written in a neutral, informative, encyclopedia-style tone (not marketing copy), summarizing what a reader will find under this topic and why it matters, based ONLY on the thread titles/excerpts given below — do not invent facts not supported by them.

Topic: {slug} — {taxonomy_desc.get(slug, '')}

Candidate threads (most highly ranked first):
{chr(10).join(threads_block)}

Respond with ONLY a single JSON object, no prose, no markdown fences:
{{"topic": "{slug}", "intro": "<120-200 word intro>", "key_threads": [<3 to 5 topic_id ints from the list above, the best entry points for a newcomer>]}}
"""
        out_path = os.path.join(OUT_DIR, f'{slug}.txt')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(prompt)
        written += 1

    print(f'Wrote {written} intro batch files to {OUT_DIR}/')


if __name__ == '__main__':
    main()

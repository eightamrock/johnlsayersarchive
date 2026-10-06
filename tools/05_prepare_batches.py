#!/usr/bin/env python3
"""
Phase 5 (prep) — builds one prompt-ready text file per batch of ~50
candidate threads for topic-tagging subagents.

For each candidate topic (data/candidates.jsonl, from Phase 4) this pulls:
  - the title
  - the first post, rendered to HTML then stripped to plain text, trimmed
    to ~1500 chars
  - up to 3 replies from the top-50 posters ("experts"), same treatment,
    trimmed to ~500 chars each

...and writes data/tag_batches/batch_NNN.txt: a ready-to-paste prompt
asking for a strict JSON array back, one object per thread, using the
fixed taxonomy from PLAN.md section "Phase 5".

Usage: python3 tools/05_prepare_batches.py [--batch-size 50]
"""
import html as html_mod
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import bbcode, linkmap  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), '..')
DB_PATH = os.path.join(ROOT, 'data', 'archive.db')
CANDIDATES_PATH = os.path.join(ROOT, 'data', 'candidates.jsonl')
BATCH_DIR = os.path.join(ROOT, 'data', 'tag_batches')
TAGS_PATH = os.path.join(ROOT, 'data', 'tags.jsonl')

BATCH_SIZE = 50
TOP_N_EXPERTS = 50

TAXONOMY = [
    ('fundamentals', 'Fundamentals — isolation vs. treatment, room-in-room, mass-air-mass resonance'),
    ('walls-floors-ceilings', 'Walls, floors and ceilings — framing, drywall, decoupling, floating floors, hangers'),
    ('doors-windows', 'Doors and windows'),
    ('hvac', 'HVAC, ventilation and silencer boxes'),
    ('treatment', 'Acoustic treatment — panels, bass traps, diffusers, materials'),
    ('control-room-design', 'Control room design — geometry, RFZ, reflection control'),
    ('speakers', 'Speakers and soffit / flush mounting'),
    ('electrical', 'Electrical, grounding and wiring'),
    ('codes-permits', 'Codes, permits and planning'),
    ('tools-measurement', 'Measurement and tools — REW, SketchUp'),
    ('materials-reference', 'Materials data and reference'),
    ('build-diaries', 'Build diaries (use diary_subtag for the room type)'),
]
DIARY_SUBTAGS = ['garage', 'basement', 'barn-outbuilding', 'commercial', 'room-in-house', 'other']

TAG_STRIP_RE = re.compile(r'<[^>]+>')


def html_to_text(html_str, max_len):
    text = TAG_STRIP_RE.sub(' ', html_str or '')
    text = html_mod.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(' ', 1)[0] + '…'
    return text


def already_tagged():
    done = set()
    if os.path.exists(TAGS_PATH):
        with open(TAGS_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(json.loads(line)['topic_id'])
                except (json.JSONDecodeError, KeyError):
                    pass
    return done


def main():
    batch_size = BATCH_SIZE
    if '--batch-size' in sys.argv:
        batch_size = int(sys.argv[sys.argv.index('--batch-size') + 1])

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    top_experts = {r[0] for r in conn.execute(
        'SELECT poster_id, COUNT(*) c FROM phpbb_posts GROUP BY poster_id ORDER BY c DESC LIMIT ?',
        (TOP_N_EXPERTS,))}

    loc = linkmap.build_post_location_map(conn)
    moved = linkmap.build_moved_map(conn)
    tf = linkmap.build_topic_forum_map(conn)
    resolver = linkmap.LinkResolver(loc, moved, tf).resolve

    candidates = []
    with open(CANDIDATES_PATH, encoding='utf-8') as f:
        for line in f:
            candidates.append(json.loads(line))

    done = already_tagged()
    todo = [c for c in candidates if c['topic_id'] not in done]
    print(f'{len(candidates)} candidates, {len(done)} already tagged, {len(todo)} to prepare.')

    os.makedirs(BATCH_DIR, exist_ok=True)
    taxonomy_block = '\n'.join(f'- {slug}: {desc}' for slug, desc in TAXONOMY)

    batch_num = 0
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        batch_num += 1
        threads_block = []
        for c in batch:
            tid = c['topic_id']
            posts = list(conn.execute(
                'SELECT post_id, poster_id, post_text, bbcode_uid FROM phpbb_posts '
                'WHERE topic_id=? ORDER BY post_time, post_id', (tid,)))
            if not posts:
                continue
            first = posts[0]
            first_html, _ = bbcode.render_post(first['post_id'], first['post_text'], first['bbcode_uid'],
                                                [], resolver)
            first_text = html_to_text(first_html, 1500)

            expert_excerpts = []
            for p in posts[1:]:
                if p['poster_id'] in top_experts:
                    ph, _ = bbcode.render_post(p['post_id'], p['post_text'], p['bbcode_uid'], [], resolver)
                    pt = html_to_text(ph, 500)
                    if pt:
                        expert_excerpts.append(pt)
                if len(expert_excerpts) >= 3:
                    break

            block = [f'### topic_id={tid}', f'Title: {c["title"]}',
                     f'Forum: {c["forum_id"]}  Views: {c["views"]}  Replies: {c["replies"]}  '
                     f'Diary-flagged: {c["is_diary"]}',
                     f'First post:\n{first_text}']
            for j, ex in enumerate(expert_excerpts, 1):
                block.append(f'Expert reply {j}:\n{ex}')
            threads_block.append('\n'.join(block))

        prompt = f"""You are classifying threads from a recording-studio-design forum archive into a fixed taxonomy, for a curated "Library" section. Read each thread's title, first post and any expert replies given, then classify it.

TAXONOMY (use these exact slugs):
{taxonomy_block}

diary_subtag values (only when primary_topic is build-diaries, else null): {', '.join(DIARY_SUBTAGS)}

For EACH thread below, output one JSON object:
{{"topic_id": <int>, "primary_topic": "<slug>", "secondary_topics": ["<slug>", ...] (0-2 items, can be empty), "diary_subtag": "<slug>"|null, "is_reference_quality": <bool, true if this reads well as a standalone reference for someone researching the topic rather than just general chat>, "confidence": <float 0-1>}}

Respond with ONLY a JSON array of exactly {len(threads_block)} objects, one per thread below, in the SAME ORDER as listed, no prose, no markdown code fences, no commentary before or after.

THREADS:

{chr(10).join('---' + chr(10) + t for t in threads_block)}
"""
        out_path = os.path.join(BATCH_DIR, f'batch_{batch_num:03d}.txt')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(prompt)

    print(f'Wrote {batch_num} batch files to {BATCH_DIR}/')


if __name__ == '__main__':
    main()

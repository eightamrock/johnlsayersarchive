#!/usr/bin/env python3
"""
Phase 5 (no-review path) — writes data/curation.json straight from the AI
tags, in the same format tools/review/index.html exports, for when the
owner chooses not to review by hand.

Rule: every tagged candidate is approved except threads the tagger itself
marked as both not reference-quality and low-confidence (forum rules,
welcome posts, off-topic chatter). Build diaries are always kept. The AI's
3-5 key threads per topic are pinned. Intros are the AI drafts, unedited
(the site labels them as AI-drafted).

Refuses to overwrite an existing curation.json unless --force is given, so
a hand-made export from the review UI is never clobbered.

Usage: python3 tools/09_auto_curate.py [--force]
"""
import datetime
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), '..')
CANDIDATES = os.path.join(ROOT, 'data', 'candidates.jsonl')
TAGS = os.path.join(ROOT, 'data', 'tags.jsonl')
INTROS = os.path.join(ROOT, 'data', 'topic_intros.json')
OUT = os.path.join(ROOT, 'data', 'curation.json')

LOW_CONFIDENCE = 0.5


def main():
    if os.path.exists(OUT) and '--force' not in sys.argv:
        sys.exit(f'{OUT} already exists (maybe a hand-reviewed export). Re-run with --force to replace it.')

    cand_ids = {json.loads(l)['topic_id'] for l in open(CANDIDATES, encoding='utf-8')}
    tags = [json.loads(l) for l in open(TAGS, encoding='utf-8')]
    tags = [t for t in tags if t['topic_id'] in cand_ids]
    intros = json.load(open(INTROS, encoding='utf-8'))

    pinned = {tid for v in intros.values() for tid in v['key_threads']}

    threads = []
    dropped = 0
    for t in sorted(tags, key=lambda t: t['topic_id']):
        keep = (t['primary_topic'] == 'build-diaries'
                or t['is_reference_quality']
                or t['confidence'] >= LOW_CONFIDENCE
                or t['topic_id'] in pinned)
        dropped += not keep
        threads.append({
            'topic_id': t['topic_id'],
            'primary_topic': t['primary_topic'],
            'secondary_topics': t['secondary_topics'],
            'diary_subtag': (t['diary_subtag'] or 'other') if t['primary_topic'] == 'build-diaries' else None,
            'status': 'approved' if keep else 'dropped',
            'pinned': t['topic_id'] in pinned,
        })

    out = {
        'generated_at': datetime.date.today().isoformat(),
        'generated_by': 'tools/09_auto_curate.py (no manual review)',
        'topics': {slug: {'intro': v['intro'], 'key_threads': v['key_threads']} for slug, v in intros.items()},
        'threads': threads,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'Wrote {OUT}: {len(threads) - dropped} approved, {dropped} dropped, {len(pinned)} pinned.')


if __name__ == '__main__':
    main()

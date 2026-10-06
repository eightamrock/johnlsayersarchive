#!/usr/bin/env python3
"""
Phase 5 (intros, ingest) — validates a subagent's JSON object for one
topic intro and appends/updates it in data/topic_intros.json.

Usage:
    python3 tools/06_ingest_intros.py <<'JSON'
    {"topic": "hvac", "intro": "...", "key_threads": [123, 456]}
    JSON
"""
import json
import os
import sys
from importlib import import_module

sys.path.insert(0, os.path.dirname(__file__))
prep = import_module('05_prepare_batches')

ROOT = os.path.join(os.path.dirname(__file__), '..')
OUT_PATH = os.path.join(ROOT, 'data', 'topic_intros.json')

VALID_TOPICS = {slug for slug, _ in prep.TAXONOMY}


def main():
    raw = sys.stdin.read()
    try:
        row = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f'JSON parse failed: {e}\nFirst 300 chars: {raw[:300]!r}')

    errs = []
    if row.get('topic') not in VALID_TOPICS:
        errs.append(f'bad topic: {row.get("topic")!r}')
    if not isinstance(row.get('intro'), str) or not (50 < len(row.get('intro', '')) < 3000):
        errs.append('intro missing or wrong length')
    if not isinstance(row.get('key_threads'), list) or not all(isinstance(t, int) for t in row.get('key_threads', [])):
        errs.append('key_threads not a list of ints')
    if errs:
        sys.exit(f'REJECTED: {errs} -- {str(row)[:300]}')

    data = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding='utf-8') as f:
            data = json.load(f)

    data[row['topic']] = {'intro': row['intro'].strip(), 'key_threads': row['key_threads']}

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"OK: {row['topic']} ({len(row['intro'].split())} words, {len(row['key_threads'])} key threads). "
          f"{len(data)}/{len(VALID_TOPICS)} topics done.")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Phase 5 (ingest) — validates a subagent's JSON array output for one batch
and appends the valid rows to data/tags.jsonl.

Usage:
    python3 tools/05_ingest_tags.py batch_003 <<'JSON'
    [{"topic_id": 839, "primary_topic": "walls-floors-ceilings", ...}, ...]
    JSON

Reads the JSON from stdin so the caller doesn't need to worry about shell
quoting. Validates every object against the fixed taxonomy and required
keys; rejects (and reports) any that don't match rather than silently
dropping or guessing. On success, appends only the valid rows and reports
which topic_ids (if any) were expected but missing from the response.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from importlib import import_module

prep = import_module('05_prepare_batches')

ROOT = os.path.join(os.path.dirname(__file__), '..')
TAGS_PATH = os.path.join(ROOT, 'data', 'tags.jsonl')
BATCH_DIR = os.path.join(ROOT, 'data', 'tag_batches')

VALID_TOPICS = {slug for slug, _ in prep.TAXONOMY}
VALID_DIARY = set(prep.DIARY_SUBTAGS)
REQUIRED_KEYS = {'topic_id', 'primary_topic', 'secondary_topics', 'diary_subtag',
                  'is_reference_quality', 'confidence'}


def validate_row(row):
    errs = []
    if not REQUIRED_KEYS.issubset(row.keys()):
        errs.append(f'missing keys: {REQUIRED_KEYS - row.keys()}')
        return errs
    if not isinstance(row['topic_id'], int):
        errs.append('topic_id not int')
    if row['primary_topic'] not in VALID_TOPICS:
        errs.append(f'bad primary_topic: {row["primary_topic"]!r}')
    if not isinstance(row['secondary_topics'], list) or any(
            t not in VALID_TOPICS for t in row['secondary_topics']):
        errs.append(f'bad secondary_topics: {row["secondary_topics"]!r}')
    if row['diary_subtag'] is not None and row['diary_subtag'] not in VALID_DIARY:
        errs.append(f'bad diary_subtag: {row["diary_subtag"]!r}')
    if not isinstance(row['is_reference_quality'], bool):
        errs.append('is_reference_quality not bool')
    if not isinstance(row['confidence'], (int, float)) or not (0 <= row['confidence'] <= 1):
        errs.append(f'bad confidence: {row["confidence"]!r}')
    return errs


def expected_topic_ids(batch_name):
    path = os.path.join(BATCH_DIR, f'{batch_name}.txt')
    if not os.path.exists(path):
        return None
    ids = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('### topic_id='):
                ids.append(int(line.strip().split('=', 1)[1]))
    return ids


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: python3 tools/05_ingest_tags.py <batch_name> < response.json')
    batch_name = sys.argv[1]
    raw = sys.stdin.read()

    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f'JSON parse failed: {e}\nFirst 300 chars: {raw[:300]!r}')

    if not isinstance(rows, list):
        sys.exit(f'Expected a JSON array, got {type(rows).__name__}')

    good, bad = [], []
    seen_ids = set()
    for row in rows:
        errs = validate_row(row) if isinstance(row, dict) else ['not an object']
        if errs:
            bad.append((row, errs))
        else:
            good.append(row)
            seen_ids.add(row['topic_id'])

    with open(TAGS_PATH, 'a', encoding='utf-8') as f:
        for row in good:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    print(f'{batch_name}: {len(good)} valid rows appended, {len(bad)} rejected.')
    for row, errs in bad[:10]:
        print(f'  REJECTED: {errs} -- {str(row)[:200]}')

    expected = expected_topic_ids(batch_name)
    if expected is not None:
        missing = set(expected) - seen_ids
        if missing:
            print(f'  MISSING from response entirely: {sorted(missing)}')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Phase 1 — Extract.

Streams the raw phpBB SQL dump and writes ONLY whitelisted columns for
whitelisted forums into data/archive.db. Never touches PII columns
(email, ip, password, signature, private messages, sessions, etc).

Usage:
    python3 tools/01_extract.py [path/to/jls_archive_good.sql.gz] [data/archive.db]
"""
import html
import re
import sqlite3
import sys
import os

DEFAULT_SRC = os.path.join(os.path.dirname(__file__), '..', '..', 'jls_archive_good.sql.gz')
DEFAULT_DST = os.path.join(os.path.dirname(__file__), '..', 'data', 'archive.db')

# --- Forum whitelist -------------------------------------------------------
# Real forums (forum_type=1) we keep content from.
KEEP_FORUM_IDS = {1, 2, 3, 10, 11, 12, 16, 17, 25}
# Studio Design, Studio Construction, Acoustics, Other Studios,
# John Sayers Productions Designed Studios, Speaking of Speakers,
# Installation and Wiring, Welcome, Hidden (John's small studio design thread)
#
# Excluded: 4 (Wombat Hole), 15 (Engine Room), 6 (Building Products), 23 (Foro Español)
# All forum rows (including categories and excluded forums) are kept in the
# `forums` table for hierarchy/reference, but topics/posts/attachments are
# only extracted for KEEP_FORUM_IDS.

# --- Column whitelists per table -------------------------------------------
COLUMNS = {
    'phpbb_forums':   ['forum_id', 'parent_id', 'left_id', 'right_id', 'forum_name',
                        'forum_desc', 'forum_type'],
    'phpbb_topics':   ['topic_id', 'forum_id', 'topic_title', 'topic_poster', 'topic_time',
                        'topic_views', 'topic_replies', 'topic_status', 'topic_type',
                        'topic_first_post_id', 'topic_moved_id', 'topic_last_post_time'],
    'phpbb_posts':    ['post_id', 'topic_id', 'forum_id', 'poster_id', 'post_time',
                        'post_username', 'post_subject', 'post_text', 'bbcode_uid',
                        'enable_smilies', 'enable_magic_url', 'post_attachment',
                        'post_edit_time', 'post_edit_count'],
    'phpbb_users':    ['user_id', 'username'],
    'phpbb_attachments': ['attach_id', 'post_msg_id', 'topic_id', 'real_filename',
                           'physical_filename', 'extension', 'mimetype', 'filesize',
                           'filetime', 'attach_comment', 'thumbnail'],
}

# phpBB stores these plain-text (non-BBCode) fields already HTML-entity-
# encoded at write time (its normal storage convention for subject/title/
# username fields, unlike post_text which keeps raw bbcode_uid-tagged
# markup). Unescape once here so every downstream consumer gets plain text.
UNESCAPE_COLS = {
    'phpbb_topics': {'topic_title'},
    'phpbb_posts': {'post_subject', 'post_username'},
    'phpbb_users': {'username'},
    'phpbb_attachments': {'real_filename'},
}


def unescape_fully(v, _max_passes=5):
    # A handful of rows (non-ASCII usernames/filenames) are double-encoded
    # in the source dump, e.g. "&amp;#1514;" -> "&#1514;" after one pass.
    # html.unescape() doesn't re-scan its own output, so loop to a fixed
    # point instead of assuming a single pass is enough.
    for _ in range(_max_passes):
        nxt = html.unescape(v)
        if nxt == v:
            return v
        v = nxt
    return v


def row_values(r, col_idx, wanted, table):
    unescape = UNESCAPE_COLS.get(table, ())
    out = []
    for c in wanted:
        v = r[col_idx[c]]
        if c in unescape and isinstance(v, str):
            v = unescape_fully(v)
        out.append(v)
    return out


# Columns that must NEVER appear anywhere in archive.db.
FORBIDDEN_PATTERNS = [
    re.compile(p, re.I) for p in
    [r'email', r'.*_ip$', r'^user_ip$', r'password', r'passchg', r'sig', r'privmsg',
     r'session', r'salt', r'actkey', r'newpasswd', r'ip$', r'birthday', r'occ$',
     r'interests', r'icq', r'aim', r'yim', r'msnm', r'jabber']
]

TABLES_TO_READ = set(COLUMNS.keys())


def parse_values(s, i):
    """Parse a MySQL multi-row VALUES (...),(...),(...) clause starting at index i."""
    rows = []
    n = len(s)
    while i < n:
        while i < n and s[i] in ' ,\n\r':
            i += 1
        if i >= n or s[i] == ';':
            break
        assert s[i] == '(', s[max(0, i - 20):i + 20]
        i += 1
        row = []
        while True:
            while s[i] == ' ':
                i += 1
            c = s[i]
            if c == "'":
                i += 1
                buf = []
                while True:
                    c = s[i]
                    if c == '\\':
                        nx = s[i + 1]
                        buf.append({'n': '\n', 'r': '\r', 't': '\t', '0': '\0', 'Z': '\x1a'}.get(nx, nx))
                        i += 2
                    elif c == "'":
                        if s[i + 1] == "'":
                            buf.append("'")
                            i += 2
                        else:
                            i += 1
                            break
                    else:
                        j = i
                        while s[j] not in "\\'":
                            j += 1
                        buf.append(s[i:j])
                        i = j
                row.append(''.join(buf))
            else:
                j = i
                while s[j] not in ',)':
                    j += 1
                tok = s[i:j].strip()
                i = j
                row.append(None if tok == 'NULL' else (int(tok) if re.fullmatch(r'-?\d+', tok) else tok))
            while s[i] == ' ':
                i += 1
            if s[i] == ',':
                i += 1
                continue
            if s[i] == ')':
                i += 1
                break
        rows.append(row)
    return rows


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    dst = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DST
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)

    if not os.path.exists(src):
        sys.exit(f"Source dump not found: {src}")

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)

    db = sqlite3.connect(dst)
    created = set()
    counts = {t: 0 for t in TABLES_TO_READ}
    skipped_forum_filter = {t: 0 for t in ('phpbb_topics', 'phpbb_posts')}

    # First pass needs the kept-topic-id set to filter attachments/posts by
    # forum membership consistently. phpbb_posts and phpbb_topics both carry
    # forum_id directly, so we can filter those inline. Attachments only
    # carry topic_id, so we buffer attachment rows and filter them in a
    # second pass against the topics we actually kept.
    pending_attachments = []  # raw (cols, row) awaiting topic filter
    kept_topic_ids = set()
    moved_map = {}  # old topic_id -> topic_moved_id, for topics excluded because status=2

    ins_re = re.compile(r'INSERT INTO (\w+) \(([^)]*)\) VALUES ')

    print(f"Reading {src} ...")
    with open(src, encoding='utf-8', errors='replace') as f:
        for line in f:
            if not line.startswith('INSERT INTO'):
                continue
            m = ins_re.match(line)
            if not m:
                continue
            table = m.group(1)
            if table not in TABLES_TO_READ:
                continue
            all_cols = [c.strip() for c in m.group(2).split(',')]
            col_idx = {c: i for i, c in enumerate(all_cols)}
            wanted = COLUMNS[table]
            missing = [c for c in wanted if c not in col_idx]
            if missing:
                sys.exit(f"Column(s) {missing} not found in dump's {table} INSERT — dump schema changed?")

            if table not in created:
                db.execute(f'DROP TABLE IF EXISTS {table}')
                db.execute(f'CREATE TABLE {table} ({",".join(wanted)})')
                created.add(table)

            rows = parse_values(line, m.end())

            if table == 'phpbb_forums':
                out_rows = [row_values(r, col_idx, wanted, table) for r in rows]
                db.executemany(f'INSERT INTO {table} VALUES ({",".join("?" * len(wanted))})', out_rows)
                counts[table] += len(out_rows)

            elif table == 'phpbb_topics':
                fid_i = col_idx['forum_id']
                status_i = col_idx['topic_status']
                movedid_i = col_idx['topic_moved_id']
                tid_i = col_idx['topic_id']
                out_rows = []
                for r in rows:
                    if r[fid_i] not in KEEP_FORUM_IDS:
                        skipped_forum_filter[table] += 1
                        continue
                    if r[status_i] == 2:  # moved shadow topic -> record redirect, no content
                        moved_map[r[tid_i]] = r[movedid_i]
                        continue
                    kept_topic_ids.add(r[tid_i])
                    out_rows.append(row_values(r, col_idx, wanted, table))
                if out_rows:
                    db.executemany(f'INSERT INTO {table} VALUES ({",".join("?" * len(wanted))})', out_rows)
                counts[table] += len(out_rows)

            elif table == 'phpbb_posts':
                fid_i = col_idx['forum_id']
                out_rows = [row_values(r, col_idx, wanted, table) for r in rows if r[fid_i] in KEEP_FORUM_IDS]
                skipped_forum_filter[table] += len(rows) - len(out_rows)
                if out_rows:
                    db.executemany(f'INSERT INTO {table} VALUES ({",".join("?" * len(wanted))})', out_rows)
                counts[table] += len(out_rows)

            elif table == 'phpbb_users':
                out_rows = [row_values(r, col_idx, wanted, table) for r in rows]
                db.executemany(f'INSERT INTO {table} VALUES ({",".join("?" * len(wanted))})', out_rows)
                counts[table] += len(out_rows)

            elif table == 'phpbb_attachments':
                inmsg_i = col_idx.get('in_message')
                orphan_i = col_idx.get('is_orphan')
                for r in rows:
                    if inmsg_i is not None and r[inmsg_i] == 1:
                        continue
                    if orphan_i is not None and r[orphan_i] == 1:
                        continue
                    pending_attachments.append(row_values(r, col_idx, wanted, table))

    # Second pass: filter buffered attachments against kept_topic_ids.
    if 'phpbb_attachments' not in created:
        db.execute('DROP TABLE IF EXISTS phpbb_attachments')
        db.execute(f'CREATE TABLE phpbb_attachments ({",".join(COLUMNS["phpbb_attachments"])})')
        created.add('phpbb_attachments')
    tid_col = COLUMNS['phpbb_attachments'].index('topic_id')
    kept_att = [r for r in pending_attachments if r[tid_col] in kept_topic_ids]
    if kept_att:
        db.executemany(
            f'INSERT INTO phpbb_attachments VALUES ({",".join("?" * len(COLUMNS["phpbb_attachments"]))})',
            kept_att)
    counts['phpbb_attachments'] = len(kept_att)

    # Moved-topic redirect map (old_topic_id -> destination_topic_id), kept
    # for Phase 2 link rewriting only. No PII.
    db.execute('DROP TABLE IF EXISTS moved_topics')
    db.execute('CREATE TABLE moved_topics (old_topic_id INTEGER PRIMARY KEY, new_topic_id INTEGER)')
    db.executemany('INSERT INTO moved_topics VALUES (?, ?)', list(moved_map.items()))

    db.commit()

    # --- Privacy assertion --------------------------------------------------
    violations = []
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for row in db.execute(f"PRAGMA table_info({table})"):
            colname = row[1]
            for pat in FORBIDDEN_PATTERNS:
                if pat.search(colname):
                    violations.append(f'{table}.{colname}')
    if violations:
        db.close()
        os.remove(dst)
        sys.exit("PRIVACY VIOLATION — forbidden column(s) present, archive.db deleted:\n  "
                  + "\n  ".join(violations))

    print("\nExtraction complete.")
    for t in sorted(counts):
        print(f'  {t:22s} {counts[t]}')
    print(f'  moved_topics (redirects) {len(moved_map)}')
    print(f'\nSkipped (excluded forums): topics={skipped_forum_filter["phpbb_topics"]}, '
          f'posts={skipped_forum_filter["phpbb_posts"]}')
    print(f'\nWrote {dst} ({os.path.getsize(dst)/1e6:.1f} MB)')
    print('Privacy check passed: no forbidden PII columns present.')


if __name__ == '__main__':
    main()

import re, sqlite3, sys

SRC, DST = sys.argv[1], sys.argv[2]
KEEP = {'phpbb_forums','phpbb_topics','phpbb_posts','phpbb_users','phpbb_attachments',
        'phpbb_config','phpbb_groups','phpbb_user_group','phpbb_ranks','phpbb_bbcodes','phpbb_poll_options'}

def parse_values(s, i):
    rows = []
    n = len(s)
    while i < n:
        while i < n and s[i] in ' ,\n\r': i += 1
        if i >= n or s[i] == ';': break
        assert s[i] == '(', s[i-20:i+20]
        i += 1
        row = []
        while True:
            while s[i] == ' ': i += 1
            c = s[i]
            if c == "'":
                i += 1; buf = []
                while True:
                    c = s[i]
                    if c == '\\':
                        nx = s[i+1]
                        buf.append({'n':'\n','r':'\r','t':'\t','0':'\0','Z':'\x1a'}.get(nx, nx)); i += 2
                    elif c == "'":
                        if s[i+1] == "'": buf.append("'"); i += 2
                        else: i += 1; break
                    else:
                        j = i
                        while s[j] not in "\\'": j += 1
                        buf.append(s[i:j]); i = j
                row.append(''.join(buf))
            else:
                j = i
                while s[j] not in ',)': j += 1
                tok = s[i:j].strip(); i = j
                row.append(None if tok == 'NULL' else (int(tok) if re.fullmatch(r'-?\d+', tok) else tok))
            while s[i] == ' ': i += 1
            if s[i] == ',': i += 1; continue
            if s[i] == ')': i += 1; break
        rows.append(row)
    return rows

db = sqlite3.connect(DST)
created = set()
ins = re.compile(r'INSERT INTO (\w+) \(([^)]*)\) VALUES ')
with open(SRC, encoding='utf-8', errors='replace') as f:
    for line in f:
        if not line.startswith('INSERT INTO'): continue
        m = ins.match(line)
        t = m.group(1)
        if t not in KEEP: continue
        cols = [c.strip() for c in m.group(2).split(',')]
        if t not in created:
            db.execute(f'DROP TABLE IF EXISTS {t}')
            db.execute(f'CREATE TABLE {t} ({",".join(cols)})')
            created.add(t)
        rows = parse_values(line, m.end())
        db.executemany(f'INSERT INTO {t} VALUES ({",".join("?"*len(cols))})', rows)
db.commit()
for t in sorted(created):
    print(t, db.execute(f'select count(*) from {t}').fetchone()[0])

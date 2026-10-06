#!/usr/bin/env python3
"""Unit tests for tools/lib/pii.py. Run: python3 tools/tests/test_pii.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib import pii  # noqa: E402

PASS = 0
FAIL = 0


def check(name, condition, extra=''):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f'FAIL: {name} {extra}')


def scrub(text):
    rows = []
    return pii.scrub_text(text, 1, rows), rows


t, rows = scrub('Email me at bob.smith@example.co.uk please')
check('email scrubbed', 'bob.smith@example.co.uk' not in t, t)
check('email category logged', rows and rows[0][1] == 'email', rows)

t, rows = scrub('call me at 555-123-4567 or (555) 123-4567 or 555.123.4567')
check('all NANP phone formats scrubbed', '555' not in t.replace('[removed]', ''), t)

t, rows = scrub('reach me at +44 20 7946 0958 thanks')
check('international phone scrubbed', '7946' not in t, t)

t, rows = scrub('I live at 123 Main Street, come by')
check('street address scrubbed', '123 Main Street' not in t, t)

t, rows = scrub('meet at 42 Wallaby Way Sydney')
check('street address (Way) scrubbed', '42 Wallaby Way' not in t, t)

t, rows = scrub('bob at example dot com should get you there')
check('obfuscated email scrubbed', 'example' not in t or '[removed]' in t, t)

# False-positive guards: measurements, hz, stc, dimensions, years must survive untouched
for safe in [
    'The studs are 2x4x8 construction lumber',
    'Use 1/2" drywall on both sides',
    'Target STC 52 for this wall',
    'Notch filter at 125Hz fixes the boom',
    'Built the room back in 2007',
    'Room is 12x14x8 feet',
    'Panel size 4x8 sheet',
]:
    t, rows = scrub(safe)
    check(f'no false positive: {safe!r}', t == safe, t)

# --- scrub_published: last pass over finished HTML and titles ---------------
for raw, want in [
    ('someone@example.net ... probs with ur email', '[removed] ... probs with ur email'),
    ('Image not preserved: LaudSep@8.jpg', 'Image not preserved: LaudSep@8.jpg'),
    ('http://www.example.com/looking-up-@-cavity-wall---.jpg', 'http://www.example.com/looking-up-@-cavity-wall---.jpg'),
    ('<div class="bb-quote-author">someone@gmail.com wrote:</div>', '<div class="bb-quote-author">[removed] wrote:</div>'),
    ('viewtopic.php?f=1&amp;t=2837&amp;sid=0123456789abcdef0123456789abcdef"', 'viewtopic.php?f=1&amp;t=2837"'),
    ('a.htm?sid=fedcba9876543210fedcba9876543210', 'a.htm'),
    ('x.php?sid=abcdef0123456789&amp;t=4', 'x.php?t=4'),
    ('(Add) 104.10.1.1 Action', '(Add) 104.10.1.1 Action'),
    ('Sec. 8.2.2.3 (Room', 'Sec. 8.2.2.3 (Room'),
    ('Firefox/25.0 44 203.0.113.45 Thanks', 'Firefox/25.0 44 [removed] Thanks'),
    ('cip=198.51.100.27&amp;act=', 'cip=[removed]&amp;act='),
    ('href="http://66.201.119.52/pictures/a.jpg"', 'href="http://66.201.119.52/pictures/a.jpg"'),
]:
    got = pii.scrub_published(raw)
    check(f'scrub_published {raw[:40]!r}', got == want, repr(got))

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)

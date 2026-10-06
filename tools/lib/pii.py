"""
PII scrubber for post bodies.

Operates on already-extracted, already-decoded plain text (run this AFTER
BBCode/entity decoding, before final HTML tag insertion, on the human-
readable text portions only — never inside URLs used as href targets).

Removes (replaces with "[removed]"):
  - email addresses, including simple "name at domain dot com" obfuscation
  - phone numbers (NANP-style with separators, and leading-"+" international)
  - street addresses (number + street-type word)

Every match is also logged to a review CSV (post_id, category, snippet, rule)
so the owner can audit for false positives. Scrubbing is non-destructive:
the original text lives untouched in archive.db, so any false positive can
be fixed by adjusting a rule and re-rendering — nothing here mutates source
data.

Usernames and real names are intentionally NOT touched.
"""
import re

REMOVED = '[removed]'

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')

# "name at domain dot com" / "name (at) domain (dot) com" obfuscation
OBFUSCATED_EMAIL_RE = re.compile(
    r'\b[\w.+-]+\s*[\(\[]?\s*at\s*[\)\]]?\s+[\w-]+(?:\s*[\(\[]?\s*dot\s*[\)\]]?\s+[\w-]+)+\b',
    re.IGNORECASE)

# NANP-style phone: (555) 555-1234 / 555-555-1234 / 555.555.1234 / 555 555 1234
# Requires a separator between each digit group so it doesn't match bare
# 10-digit numbers (which risk colliding with part numbers) or dimension
# strings like 2x4, 1/2", 125Hz, STC-52, or bare years like 2007.
PHONE_NANP_RE = re.compile(
    r'(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)')

# International, leading +, at least 7 digits total, grouped with separators.
PHONE_INTL_RE = re.compile(
    r'(?<!\d)\+\d{1,3}(?:[-.\s]?\(?\d{2,4}\)?){2,5}(?!\d)')

STREET_TYPES = (
    r'street|st|avenue|ave|road|rd|lane|ln|boulevard|blvd|drive|dr|court|ct|'
    r'way|circle|cir|place|pl|terrace|ter|trail|highway|hwy|parkway|pkwy|'
    r'crescent|cres'
)
# This forum's own vocabulary makes a bare "number + street-type word" a bad
# heuristic on its own: "2 way" / "3 way" (crossovers), "5 minute drive",
# "into place", "down the road" etc. are all far more common here than real
# addresses. Real street names are near-always a capitalized proper noun, so
# require the word immediately before the type keyword to start with a
# capital letter (allowing up to 2 more, arbitrary, words before that) —
# this keeps "123 Main Street" / "42 Wallaby Way" / "750 Audio Drive" while
# rejecting the audio-domain false positives above.
#
# Two more narrowings, needed against real false positives found in this
# corpus: gaps use [ \t] (not \s) so a match can't stretch across a
# paragraph break and glue an unrelated number to unrelated capitalized
# text below it; middle words exclude '.' so "3 screws. This way" doesn't
# treat "This" (capitalized only because it starts a new sentence) as a
# street name.
STREET_ADDRESS_RE = re.compile(
    r"\b\d{1,6}(?:[ \t]+[A-Za-z0-9'-]+){0,2}[ \t]+[A-Z][A-Za-z'-]*[ \t]+(?i:" + STREET_TYPES + r')\b\.?')


def scrub_text(text, post_id, review_rows):
    """
    Replace PII in `text` with '[removed]'. Appends
    (post_id, category, matched_snippet) tuples to `review_rows` for every
    match (all matches are logged, regardless of confidence, so the owner
    can audit).

    Returns the scrubbed text.
    """
    if not text:
        return text

    def _sub(pattern, category, s):
        out = []
        last = 0
        for m in pattern.finditer(s):
            snippet = m.group(0)
            review_rows.append((post_id, category, snippet))
            out.append(s[last:m.start()])
            out.append(REMOVED)
            last = m.end()
        out.append(s[last:])
        return ''.join(out)

    text = _sub(EMAIL_RE, 'email', text)
    text = _sub(OBFUSCATED_EMAIL_RE, 'email_obfuscated', text)
    text = _sub(PHONE_NANP_RE, 'phone_nanp', text)
    text = _sub(PHONE_INTL_RE, 'phone_intl', text)
    text = _sub(STREET_ADDRESS_RE, 'street_address', text)
    return text


# --- Final pass over published HTML ------------------------------------------
# scrub_text() only sees the plain-text runs of a post. Contact details can
# also hide in places it never sees: quote author names, link targets,
# attachment filenames and thread titles. scrub_published() runs over the
# finished HTML (or a title) as a last line of defence, and tools/qa.py checks
# the built site with these same patterns.

# Stricter than EMAIL_RE so filenames like "LaudSep@8.jpg" and URL fragments
# like "looking-up-@-cavity" are not mistaken for addresses.
STRICT_EMAIL_RE = re.compile(
    r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9](?:[A-Za-z0-9._%+-]*[A-Za-z0-9])?'
    r'@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*'
    r'\.(?!(?:jpe?g|png|gif|bmp|tiff?|pdf|skp|zip|rar|docx?|xlsx?|html?|php|asp)\b)[A-Za-z]{2,}\b')

# phpBB-style session ids in links (to this forum and to others).
SID_RE = re.compile(r'(\?|&amp;|&|;)sid=[0-9A-Fa-f]{8,}(&amp;|&)?')

IPV4_RE = re.compile(
    r'(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])')


def is_personal_ipv4(text, m):
    """
    True for an IPv4 address that could identify a person. Two shapes are
    left alone: the host part of a URL (a web server someone linked to) and
    dotted section numbers such as "104.10.1.1" or "8.2.2.3" from building
    codes and ITU standards, which have at most one component of 20 or more.
    """
    if text[max(0, m.start() - 3):m.start()] == '://':
        return False
    octets = [int(o) for o in m.group(0).split('.')]
    return sum(o >= 20 for o in octets) >= 2


def _strip_sid(m):
    lead, trail = m.group(1), m.group(2)
    if lead == '?':
        return '?' if trail else ''
    return trail or ''


def scrub_published(h):
    h = STRICT_EMAIL_RE.sub(REMOVED, h)
    h = SID_RE.sub(_strip_sid, h)
    h = IPV4_RE.sub(lambda m: REMOVED if is_personal_ipv4(h, m) else m.group(0), h)
    return h

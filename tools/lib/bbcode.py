"""
Converts a phpBB 3.0 stored post body (pre-processed BBCode with a per-post
uid, mixed with some already-rendered raw HTML fragments for smilies and
auto-linked URLs) into sanitized, self-contained HTML.

Pipeline (see PLAN.md section 3 for the format notes this is based on):
  1. Pull out the raw, already-rendered HTML fragments phpBB stored directly
     (smilies, magic/local/www auto-links, mailto links) into placeholder
     tokens — these are literal HTML in the source, NOT entity-escaped, and
     must never be run through html.unescape/html.escape.
  2. html.unescape() everything that's left (plain user text + bbcode tag
     syntax + quote author names + url targets are all htmlspecialchars-
     escaped in storage).
  3. Single left-to-right scan converting each recognized [tag:uid] to
     trusted HTML, and html.escape()-ing every plain-text run in between.
  4. Re-insert the placeholders from step 1 with their final HTML.
  5. Run the PII scrubber over the plain-text runs only (done inside step 3,
     before a run is escaped/emitted) — never over URLs or filenames.

A fresh tag regex is compiled per post using that post's own literal
bbcode_uid (not a generic pattern) so tag boundaries are unambiguous.
"""
import html
import re

from . import pii

PLACEHOLDER_OPEN = ''
PLACEHOLDER_CLOSE = ''
PLACEHOLDER_RE = re.compile(PLACEHOLDER_OPEN + r'(\d+)' + PLACEHOLDER_CLOSE)

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'bmp'}

# --- Step 1: raw pre-rendered HTML fragments --------------------------------

SMILIE_RE = re.compile(
    r'<!--\s*s(?P<code1>\S+?)\s*-->\s*<img[^>]*alt="(?P<alt>[^"]*)"[^>]*/?>\s*<!--\s*s(?P<code2>\S+?)\s*-->')

# magic url (m), local link (l), www link (w) — all rendered by phpBB as
# <a class="postlink" href="...">text</a> wrapped in matching HTML comments.
AUTOLINK_RE = re.compile(
    r'<!--\s*(?P<kind>[mlw])\s*-->\s*<a\b[^>]*\bhref="(?P<href>[^"]*)"[^>]*>(?P<text>.*?)</a>\s*<!--\s*\1\s*-->',
    re.DOTALL)

EMAIL_LINK_RE = re.compile(
    r'<!--\s*e\s*-->\s*<a\b[^>]*\bhref="mailto:[^"]*"[^>]*>.*?</a>\s*<!--\s*e\s*-->', re.DOTALL)


def _extract_raw_fragments(text, resolve_link, review_rows, post_id):
    placeholders = []

    def store(html_out):
        placeholders.append(html_out)
        return f'{PLACEHOLDER_OPEN}{len(placeholders) - 1}{PLACEHOLDER_CLOSE}'

    def sub_smilie(m):
        alt = html.unescape(m.group('alt'))
        return store(f'<span class="smilie" title="{html.escape(alt)}">{html.escape(alt)}</span>')

    def sub_autolink(m):
        href = html.unescape(m.group('href'))
        text = html.unescape(re.sub(r'<[^>]+>', '', m.group('text')))
        resolved, is_internal, note = resolve_link(href)
        if resolved is None:
            # internal link we can't resolve (points at excluded content)
            return store(f'<span class="dead-link" title="{html.escape(note or "")}">'
                         f'{html.escape(text)}</span>')
        rel = '' if is_internal else ' rel="nofollow noopener" target="_blank"'
        return store(f'<a href="{html.escape(resolved)}"{rel}>{html.escape(text)}</a>')

    def sub_email(m):
        review_rows.append((post_id, 'email_autolink', m.group(0)[:120]))
        return store('[removed]')

    text = SMILIE_RE.sub(sub_smilie, text)
    text = AUTOLINK_RE.sub(sub_autolink, text)
    text = EMAIL_LINK_RE.sub(sub_email, text)
    return text, placeholders


def _reinsert(text, placeholders):
    return PLACEHOLDER_RE.sub(lambda m: placeholders[int(m.group(1))], text)


# --- Step 3: bbcode tag scanning --------------------------------------------

_LIST_TYPE_ATTR = {'1': '1', 'a': 'a', 'A': 'A', 'i': 'i', 'I': 'I'}
_SAFE_COLOR_RE = re.compile(r'^#?[0-9a-fA-F]{3}$|^#?[0-9a-fA-F]{6}$|^[a-zA-Z]{2,20}$')


def _build_tag_regex(uid):
    u = re.escape(uid)
    parts = [
        ('quote_named', rf'\[quote="(?P<quote_author>.*?)":{u}\]'),
        ('quote_open', rf'\[quote:{u}\]'),
        ('quote_close', rf'\[/quote:{u}\]'),
        ('b_open', rf'\[b:{u}\]'), ('b_close', rf'\[/b:{u}\]'),
        ('i_open', rf'\[i:{u}\]'), ('i_close', rf'\[/i:{u}\]'),
        ('u_open', rf'\[u:{u}\]'), ('u_close', rf'\[/u:{u}\]'),
        ('color_open', rf'\[color=(?P<color_val>[^\]:]+):{u}\]'),
        ('color_close', rf'\[/color:{u}\]'),
        ('size_open', rf'\[size=(?P<size_val>\d+):{u}\]'),
        ('size_close', rf'\[/size:{u}\]'),
        ('code_open', rf'\[code:{u}\]'), ('code_close', rf'\[/code:{u}\]'),
        # list_type is usually one of 1/a/A/i/I, but a handful of real posts
        # have malformed values like [list=4:uid] — accept anything up to the
        # uid and degrade gracefully (plain <ol>) for unrecognized values.
        ('list_typed', rf'\[list=(?P<list_type>[^\]:]+):{u}\]'),
        ('list_open', rf'\[list:{u}\]'),
        ('list_close_o', rf'\[/list:o:{u}\]'),
        ('list_close_u', rf'\[/list:u:{u}\]'),
        ('li_open', rf'\[\*:{u}\]'),
        ('li_close_m', rf'\[/\*:m:{u}\]'),
        # a handful of real posts have [/*:uid] without the usual :m: infix
        ('li_close_plain', rf'\[/\*:{u}\]'),
        ('attachment', rf'\[attachment=(?P<att_index>\d+):{u}\](?P<att_body>.*?)\[/attachment:{u}\]'),
        ('img', rf'\[img:{u}\](?P<img_target>.*?)\[/img:{u}\]'),
        # A small number of real posts (~0.15%) use bare [img]...[/img] with
        # no :uid suffix at all (an older/different embed style), or have a
        # single orphaned [img] or [/img] left over from an edit. Handle the
        # paired form first so it wins over the two lone-tag fallbacks below.
        ('img_bare', r'\[img\](?P<img_bare_target>.*?)\[/img\]'),
        ('img_bare_open_only', r'\[img\]'),
        ('img_bare_close_only', r'\[/img\]'),
        ('url_named', rf'\[url=(?P<url_target>[^\]]*?):{u}\](?P<url_label>.*?)\[/url:{u}\]'),
        ('url_bare', rf'\[url:{u}\](?P<url_bare_target>.*?)\[/url:{u}\]'),
        ('email_tag', rf'\[email:{u}\](?P<email_addr>.*?)\[/email:{u}\]'),
    ]
    pattern = '|'.join(f'(?P<{name}>{pat})' for name, pat in parts)
    return re.compile(pattern, re.DOTALL)


_STRIP_INNER_TAG_RE_CACHE = {}


def _strip_inner_tags(text, uid):
    rx = _STRIP_INNER_TAG_RE_CACHE.get(uid)
    if rx is None:
        u = re.escape(uid)
        rx = re.compile(rf'\[/?[a-zA-Z*]+(?:=[^\]]*)?:(?:o:|u:|m:)?{u}\]')
        _STRIP_INNER_TAG_RE_CACHE[uid] = rx
    return rx.sub('', text)


def render_post(post_id, raw_text, uid, attachments, resolve_link,
                 recovered_lookup=None, review_rows=None, recovered_external_lookup=None):
    """
    post_id: int
    raw_text: the stored post_text (bbcode + mixed raw html + entities)
    uid: the post's bbcode_uid
    attachments: list of dicts for this post's rows from phpbb_attachments
                 (attach_id, real_filename, extension, filesize, ...)
    resolve_link: callable(url) -> (href_or_None, is_internal, note)
    recovered_lookup: dict attach_id -> local relative image path, or None
    review_rows: list to append (post_id, category, snippet) PII log entries
    recovered_external_lookup: dict of the exact URL text inside a hotlinked
        [img]URL[/img] tag -> local relative image path, or None. Covers
        externally-hosted images (Photobucket, ImageShack, etc) separately
        recovered via tools/08_wayback_external_images.py.

    Returns: (html_string, used_attach_ids: set)
    """
    if recovered_lookup is None:
        recovered_lookup = {}
    if recovered_external_lookup is None:
        recovered_external_lookup = {}
    if review_rows is None:
        review_rows = []
    if not raw_text:
        return '', set()

    by_filename = {}
    for a in attachments:
        by_filename.setdefault(a['real_filename'], []).append(a)

    used_attach_ids = set()

    def render_attachment_card(att):
        fname = att['real_filename']
        ext = (att.get('extension') or '').lower()
        local = recovered_lookup.get(att['attach_id'])
        if local and ext not in IMAGE_EXTS:
            return (f'<div class="bb-file">Recovered file: <a href="{html.escape(local)}" download>'
                     f'{html.escape(fname)}</a></div>')
        if local:
            return (f'<figure class="bb-attachment"><img src="{html.escape(local)}" '
                     f'loading="lazy" alt="{html.escape(fname)}">'
                     f'<figcaption>{html.escape(fname)}</figcaption></figure>')
        kind = 'Image' if ext in IMAGE_EXTS else 'File'
        return (f'<div class="bb-attachment-missing">{kind} not preserved: '
                 f'{html.escape(fname)}</div>')

    IA_RE = re.compile(r'<!--\s*ia\d+\s*-->(.*?)<!--\s*ia\d+\s*-->', re.DOTALL)

    def handle_attachment(m):
        body = m.group('att_body')
        ia = IA_RE.search(body)
        fname = html.unescape(ia.group(1)).strip() if ia else None
        att = None
        if fname and fname in by_filename:
            candidates = [a for a in by_filename[fname] if a['attach_id'] not in used_attach_ids]
            if candidates:
                att = candidates[0]
        if att is None:
            idx = int(m.group('att_index'))
            remaining = [a for a in attachments if a['attach_id'] not in used_attach_ids]
            if 0 <= idx < len(remaining):
                att = remaining[idx]
        if att is None:
            label = fname or 'attachment'
            return f'<div class="bb-attachment-missing">Not preserved: {html.escape(label)}</div>'
        used_attach_ids.add(att['attach_id'])
        return render_attachment_card(att)

    def handle_img(m, group='img_target'):
        target = html.unescape(m.group(group)).strip()
        local = recovered_external_lookup.get(target)
        if local:
            return (f'<figure class="bb-attachment"><img src="{html.escape(local)}" '
                     f'loading="lazy" alt="">'
                     f'<figcaption>Recovered from web.archive.org &mdash; originally hosted at '
                     f'{html.escape(target)}</figcaption></figure>')
        resolved, is_internal, note = resolve_link(target)
        href = resolved if resolved else target
        return (f'<div class="bb-external-img">External image, not preserved &mdash; original: '
                 f'<a href="{html.escape(href)}" rel="nofollow noopener" target="_blank">'
                 f'{html.escape(target)}</a></div>')

    def handle_url(target_raw, label_raw, uid_local):
        target = html.unescape(target_raw).strip()
        label = _strip_inner_tags(label_raw, uid_local)
        label = html.unescape(label).strip() or target
        resolved, is_internal, note = resolve_link(target)
        if resolved is None:
            return f'<span class="dead-link" title="{html.escape(note or "")}">{html.escape(label)}</span>'
        rel = '' if is_internal else ' rel="nofollow noopener" target="_blank"'
        return f'<a href="{html.escape(resolved)}"{rel}>{html.escape(label)}</a>'

    def convert(text):
        rx = _build_tag_regex(uid)
        out = []
        last = 0
        for m in rx.finditer(text):
            plain = text[m.start():m.start()] if False else None  # unused
            if m.start() > last:
                run = text[last:m.start()]
                run = pii.scrub_text(run, post_id, review_rows)
                out.append(html.escape(run))
            kind = m.lastgroup
            if kind == 'quote_named':
                # rare: quote author itself contains an embedded [url]...[/url]
                # (multi-quote across threads); strip tag syntax, keep the text
                author = html.unescape(_strip_inner_tags(m.group('quote_author'), uid)).strip()
                out.append(f'<blockquote class="bb-quote"><div class="bb-quote-author">'
                            f'{html.escape(author)} wrote:</div><div class="bb-quote-body">')
            elif kind == 'quote_open':
                out.append('<blockquote class="bb-quote"><div class="bb-quote-body">')
            elif kind == 'quote_close':
                out.append('</div></blockquote>')
            elif kind == 'b_open':
                out.append('<strong>')
            elif kind == 'b_close':
                out.append('</strong>')
            elif kind == 'i_open':
                out.append('<em>')
            elif kind == 'i_close':
                out.append('</em>')
            elif kind == 'u_open':
                out.append('<span class="bb-u">')
            elif kind == 'u_close':
                out.append('</span>')
            elif kind == 'color_open':
                val = m.group('color_val').strip()
                if _SAFE_COLOR_RE.match(val):
                    out.append(f'<span style="color:{html.escape(val)}">')
                else:
                    out.append('<span>')
            elif kind == 'color_close':
                out.append('</span>')
            elif kind == 'size_open':
                pct = max(25, min(200, int(m.group('size_val'))))
                out.append(f'<span style="font-size:{pct}%">')
            elif kind == 'size_close':
                out.append('</span>')
            elif kind == 'code_open':
                out.append('<pre class="bb-code"><code>')
            elif kind == 'code_close':
                out.append('</code></pre>')
            elif kind == 'list_typed':
                t = _LIST_TYPE_ATTR.get(m.group('list_type'))
                if t:
                    out.append(f'<ol class="bb-list" type="{t}">')
                else:
                    out.append('<ol class="bb-list">')
            elif kind == 'list_open':
                out.append('<ul class="bb-list">')
            elif kind == 'list_close_o':
                out.append('</ol>')
            elif kind == 'list_close_u':
                out.append('</ul>')
            elif kind == 'li_open':
                out.append('<li>')
            elif kind in ('li_close_m', 'li_close_plain'):
                out.append('</li>')
            elif kind == 'attachment':
                out.append(handle_attachment(m))
            elif kind == 'img':
                out.append(handle_img(m))
            elif kind == 'img_bare':
                out.append(handle_img(m, group='img_bare_target'))
            elif kind in ('img_bare_open_only', 'img_bare_close_only'):
                pass  # orphaned leftover tag with no target url — drop silently
            elif kind == 'url_named':
                out.append(handle_url(m.group('url_target'), m.group('url_label'), uid))
            elif kind == 'url_bare':
                out.append(handle_url(m.group('url_bare_target'), m.group('url_bare_target'), uid))
            elif kind == 'email_tag':
                review_rows.append((post_id, 'email_tag', m.group('email_addr')[:120]))
                out.append('[removed]')
            else:
                out.append(html.escape(m.group(0)))
            last = m.end()
        if last < len(text):
            run = text[last:]
            run = pii.scrub_text(run, post_id, review_rows)
            out.append(html.escape(run))
        return ''.join(out)

    step1, placeholders = _extract_raw_fragments(raw_text, resolve_link, review_rows, post_id)
    step2 = html.unescape(step1)
    step3 = convert(step2)
    final = _reinsert(step3, placeholders)

    # trailing block for attachments never referenced inline
    leftovers = [a for a in attachments if a['attach_id'] not in used_attach_ids]
    if leftovers:
        cards = ''.join(render_attachment_card(a) for a in leftovers)
        final += f'<div class="bb-attachments-trailing">{cards}</div>'
        used_attach_ids.update(a['attach_id'] for a in leftovers)

    return final, used_attach_ids

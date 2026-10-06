"""
Resolves old johnlsayers.com forum URLs to new archive paths, and builds the
post_id -> (topic_id, page) location map used both by the renderer (to turn
[url]...[/url] links into internal links) and by the page-builder (to know
which page of a topic a given post lands on).

New URL scheme (see PLAN.md section 5):
    /t/<topic_id>/                  first page of a thread
    /t/<topic_id>/page/<n>/         page n (n >= 2)
    /forum/<forum-slug>/            forum listing
    /people/<username-slug>/        expert page

PAGE_SIZE must stay in sync with whatever tools/build.py (Phase 6) actually
paginates with — it is defined once, here, and imported everywhere.
"""
import re
import unicodedata

PAGE_SIZE = 25

FORUM_SLUGS = {
    1: 'studio-design',
    2: 'studio-construction',
    3: 'acoustics',
    10: 'other-studios',
    11: 'jsp-designed-studios',
    12: 'speaking-of-speakers',
    16: 'installation-and-wiring',
    17: 'welcome',
    25: 'hidden',
}


def slugify(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r"[']+", '', s)
    s = re.sub(r'[^a-zA-Z0-9]+', '-', s).strip('-').lower()
    return s or 'x'


def build_post_location_map(conn):
    """
    Returns dict: post_id -> (topic_id, page_number, forum_id)
    Pages are 1-indexed. Posts are ordered by post_time then post_id within
    a topic, matching original phpBB display order.
    """
    loc = {}
    cur = conn.execute(
        'SELECT post_id, topic_id, forum_id FROM phpbb_posts ORDER BY topic_id, post_time, post_id')
    counters = {}
    for post_id, topic_id, forum_id in cur:
        n = counters.get(topic_id, 0)
        page = n // PAGE_SIZE + 1
        loc[post_id] = (topic_id, page, forum_id)
        counters[topic_id] = n + 1
    return loc


def build_moved_map(conn):
    return dict(conn.execute('SELECT old_topic_id, new_topic_id FROM moved_topics'))


def build_topic_forum_map(conn):
    return dict(conn.execute('SELECT topic_id, forum_id FROM phpbb_topics'))


class LinkResolver:
    """
    Callable: resolver(url) -> (href, is_internal, note)
      href      - the URL to use (rewritten for internal links, unchanged for external)
      is_internal - True if this pointed at johnlsayers.com content
      note      - None, or a short string to render alongside a dead/unresolvable internal link
    """
    # Matches johnlsayers.com viewtopic/viewforum links, with or without
    # www., with or without the /phpBB2/ path segment, http or https.
    _HOST_RE = r'(?:https?:)?//(?:www\.)?johnlsayers\.com(?:/phpBB2)?'
    TOPIC_RE = re.compile(_HOST_RE + r'/viewtopic\.php\?([^\s"\']*)', re.I)
    FORUM_RE = re.compile(_HOST_RE + r'/viewforum\.php\?([^\s"\']*)', re.I)

    def __init__(self, post_location, moved_map, topic_forum_map):
        self.post_location = post_location
        self.moved_map = moved_map
        self.topic_forum_map = topic_forum_map

    def _query(self, qs):
        out = {}
        for part in qs.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                out[k] = v
        return out

    def _topic_path(self, topic_id, page=1):
        if page <= 1:
            return f'/t/{topic_id}/'
        return f'/t/{topic_id}/page/{page}/'

    def resolve(self, url):
        m = self.TOPIC_RE.search(url)
        if m:
            q = self._query(m.group(1))
            topic_id = int(q['t']) if q.get('t', '').isdigit() else None
            post_id = int(q['p']) if q.get('p', '').isdigit() else None

            if post_id is not None and post_id in self.post_location:
                t, page, forum_id = self.post_location[post_id]
                return (self._topic_path(t, page) + f'#p{post_id}', True, None)

            if topic_id is not None:
                topic_id = self.moved_map.get(topic_id, topic_id)
                if topic_id in self.topic_forum_map:
                    return (self._topic_path(topic_id), True, None)

            return (None, True, 'link to a thread not included in this archive')

        m = self.FORUM_RE.search(url)
        if m:
            q = self._query(m.group(1))
            forum_id = int(q['f']) if q.get('f', '').isdigit() else None
            slug = FORUM_SLUGS.get(forum_id)
            if slug:
                return (f'/forum/{slug}/', True, None)
            return (None, True, 'link to a forum not included in this archive')

        return (url, False, None)

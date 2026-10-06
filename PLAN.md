# johnlsayersarchive.com — Build Plan

A static, read-only tribute archive of the **johnlsayers.com Recording Studio Design forum** (phpBB 3.0.x, Jan 2003 – Mar 2022). John Sayers has died; the family let hosting lapse; a moderator saved a database dump before the site went dark. The owner of this project has permission to republish. The original domain is now held by a domain scalper, so **no redirects from old URLs are possible**. The new home is **johnlsayersarchive.com**.

This file is the source of truth. All decisions below were made with the owner and are **settled — do not re-ask them.** Only the items under "Open items" are undecided.

---

## 1. Settled decisions

| Area | Decision |
|---|---|
| Scope | **Full archive** of every kept thread **+ a curated "Library"** layer, organized by topic, that links into the archive |
| Framing | Tribute to the forum's content. **Do NOT restore John's main (non-forum) website.** |
| Forums kept | Studio Design (1), Studio Construction (2), Acoustics (3), Speaking of Speakers (12), Installation and Wiring (16), Other Studios (10), John Sayers Productions Designed Studios (11), Welcome (17), **Hidden (25) — include its single thread** ("New studio/house build including John's small studio design") |
| Forums excluded | The Wombat Hole (4), The Engine Room (15), Building Products (6), Foro Español (23) |
| People | Keep **usernames** and post dates. Show nothing else about users. **Expert pages for the top 50 posters** (by post count within kept forums). Facts only (post count, active years, notable threads), **no AI-written bios**. |
| Privacy | Never extract: private messages, emails, IPs, passwords, sessions, profile fields, signatures, avatars. **Scrub from post bodies:** emails, phone numbers, street addresses → `[removed]`. Uncertain street-address matches go on a review list for the owner. **Do not** scrub real names or usernames. |
| Moderator credit | The moderator who saved the dump is **not named**. |
| Curation | Claude Code subagents (no API key available) tag the **top ~2,500 ranked threads** with topics; the owner approves them in a local review UI. Threads not tagged stay in the full archive and in search. |
| AI text on site | **Only short topic-page intros**, clearly labelled as AI-drafted, reviewed by the owner. No per-thread summaries. |
| Build diaries | Full thread, paginated, plus a **"key posts" jump list** (the thread starter's posts + posts by top-50 experts). |
| Lost attachments | Visible placeholder showing the original filename, e.g. *Image not preserved: Costanera-centre.jpg*. |
| Search | **Pagefind** (static index; the only JavaScript on the site). Site must be fully usable without JS. |
| Old links | Rewrite internal links to new URLs at build time. Keep old topic IDs in URLs (`/t/4310/`). Add an `/old-link/` page: paste an old johnlsayers.com URL and it jumps to the right thread. |
| Hosting | Owner's own server, type unknown → output **plain `folder/index.html` files**, no server rewrites required. |
| Backup | **Public GitHub repo** containing the build scripts and the generated site. |
| Member outreach | None. |
| Build dir | `wwwroot/johnlsayersarchive/` (this folder) |

## 2. Open items (ask the owner when you reach them; don't block earlier phases)

- **Contact email for removal requests** on the About page. Use a placeholder `{{REMOVAL_CONTACT}}` until provided; **do not publish** without it.
- Design direction — present options during Phase 6.
- Final topic list — the owner edits the draft taxonomy during Phase 5 review.

---

## 3. Source data facts (verified)

- **Use `../jls_archive_good.sql.gz`** (455 MB). Despite the `.gz` name it is **plain UTF-8 SQL text**, not gzip. Read it directly; `gzcat` fails.
- **Ignore `../jls_forum_backup.sql.gz`**: it is truncated and ends in a phpBB HTML error page (no topics/users tables).
- Format: "phpBB Backup Script" dump, dated 2022-03-20. MySQL-style `INSERT INTO table (cols) VALUES (...),(...);` with **many rows per line**. Strings use backslash escapes and `''`.
- No MySQL is installed. Python 3.9 and sqlite3 are available. `tools/load_dump_reference.py` is a working, tested parser (≈8 s) that loads selected tables into SQLite — reuse or extend it.
- Volume (entire board): 18 forums, 16,556 topics, 145,306 posts, 147 MB of post text, 31,889 users (8,603 who posted), 62,668 attachment records.
- Posts per year peak in 2006 (16.9k) and fall to about 2k/year by 2021.
- Top posters: Soundman2020 11,938 · knightfly 6,976 · John Sayers (user_id 2) 5,462 · gullfo 5,330 · sharward 4,281 · xSpace 3,823 · AVare 2,336 · Ro 2,073 · Aaronw 1,771 · kendale 1,667 · Gregwor · giles117 · rod gervais · Ethan Winer · …
- `topic_type`: 0 normal, 1 sticky (62), 2 announcement (61), 3 global (1). Stickies and announcements are strong Library signals (e.g. "The 25 minute, $25 acoustic panel", "REFERENCE AREA – Useful threads/links", "Temporary FAQ", "Sample floor plans…").
- `topic_status = 2` rows with `topic_moved_id` are "moved" shadow topics → skip them, but map their IDs to the target topic for link rewriting.
- `poster_id = 1` is Anonymous/guest; use `post_username` as the display name.
- All posts have `post_approved = 1`.
- The `phpbb_bbcodes` table is empty, so there are **no custom BBCodes**.

### Useful columns
- **posts:** post_id, topic_id, forum_id, poster_id, post_time (unix), post_username, post_subject, post_text, bbcode_uid, enable_smilies, enable_magic_url, post_attachment, post_edit_time, post_edit_count
- **topics:** topic_id, forum_id, topic_title, topic_poster, topic_time, topic_views, topic_replies, topic_status, topic_type, topic_first_post_id, topic_moved_id, topic_last_post_time
- **attachments:** attach_id, post_msg_id (= post_id), topic_id, in_message (1 = PM → skip), is_orphan (skip), real_filename, physical_filename, extension, mimetype, filesize, filetime, attach_comment, thumbnail
- **users:** user_id, username, user_type (use only `username`; never export anything else)
- **forums:** forum_id, parent_id, left_id, forum_name, forum_desc, forum_type (0 = category, 1 = forum)

### Stored post format (phpBB 3.0)
Post text is **pre-processed BBCode**, not raw user input:
- Tags carry the post's `bbcode_uid`: `[b:3j042wfc]…[/b:3j042wfc]`. List items look like `[*:uid]…[/*:m:uid]`, and list ends look like `[/list:u:uid]` or `[/list:o:uid]`.
- Tag frequency: quote 158k, b 40k, attachment 38k, img 24k, url 21k, i 18k, u 8.5k, color 6.5k, size 2.3k, list/*, code 136, email 2.
- Quotes: `[quote="Name":uid]…[/quote:uid]`, often nested.
- `[size=117:uid]` is a percentage. `[color=#hex:uid]`.
- In URL arguments, special characters are entity-encoded: `http&#58;//www&#46;example&#46;com`. The whole text is already HTML-escaped (`&quot;`, `&amp;`, `&lt;`).
- Inline HTML already present in the text (from phpBB's own processing) is marked with comments:
  - `<!-- s:) --><img src="{SMILIES_PATH}/icon_smile.gif" …><!-- s:) -->` = smilie (128k). Render as the text code (e.g. `:)`) or a tiny inline image.
  - `<!-- m --><a class="postlink" href="…">…</a><!-- m -->` = auto-linked URL (31k); `<!-- l -->` = local link (5.3k); `<!-- w -->` = www link; `<!-- e -->` = email (**scrub**).
  - `<!-- ia0 -->filename.jpg<!-- ia0 -->` inside `[attachment=N:uid]…[/attachment:uid]` = inline attachment. N is the **index into that post's attachments ordered by attach_id DESC** (phpBB 3.0 convention). Verify this ordering on a few posts before relying on it.
- Posts with attachments that aren't placed inline: show them in an "Attachments" block at the end of the post.

---

## 4. Attachments and image recovery

- The actual attachment files (8.25 GB) are **lost**. Only metadata exists.
- By type: jpg 50.7k, png 4.8k, gif 2.5k, SketchUp .skp 2.0k, jpeg 1.0k, pdf 925, zip 273.
- **Wayback Machine has 4,213 of them** (about 7%) as real image files. Recovery rate is 25% for sticky/announcement threads, 13% for threads with more than 100k views, and 6% for everything else.
- `data/wayback_attachment_captures.txt` lists the captured URLs (`original statuscode mimetype`, collapsed by URL). IDs appear as `file.php?id=NNNN`, sometimes with `&sid=` and `&t=1` (thumbnail) or `&mode=view`. Prefer the full-size version over `t=1`.
- The list has no timestamps. For each ID, fetch the latest 200 capture from the CDX API:
  `https://web.archive.org/cdx/search/cdx?url=www.johnlsayers.com/phpBB2/download/file.php?id=NNNN*&filter=statuscode:200&filter=!mimetype:text/html&fl=timestamp,original&limit=-1`
  Then download raw bytes with the `id_` flag: `https://web.archive.org/web/<timestamp>id_/<original>`. Check both the `www.` and bare hosts.
- **Be polite:** at most ~1 request per second, retry with backoff on 429/5xx, and make the job resumable (skip files already downloaded). Run it in the background.
- Also check Wayback for external `[img]` embeds: roughly 12k [img] tags, including ~680 posts using Photobucket and ~360 using ImageShack, which are mostly dead. Check them the same way. This is lower priority; do it after the attachments.
- Keep originals in `data/images/orig/` (not committed if huge). Generate web versions (max 1600px, JPEG q≈82) in `site/img/`. Pillow is fine; `sips` also exists on macOS.

---

## 5. Target site structure

Plain static files; every page is `path/index.html`.

| URL | Contents |
|---|---|
| `/` | Tribute intro to the forum (not John's business site), how to use the archive, Library entry points, "most-read threads", forum stats |
| `/library/` | Topic index |
| `/library/<topic-slug>/` | Labelled AI intro + curated, ranked thread list with one-line metadata (replies, years, key experts involved) |
| `/forum/<forum-slug>/` and `/forum/<forum-slug>/page/N/` | Full archive listing, paginated (50 topics/page, sorted by last post, like the original) |
| `/t/<topic_id>/` and `/t/<topic_id>/page/N/` | Thread, 25 posts/page, anchors `#p<post_id>`, breadcrumbs, prev/next page. Build diaries (and any thread over ~100 posts) get the **key-posts jump list**. |
| `/people/` and `/people/<username-slug>/` | Top-50 expert pages |
| `/old-link/` | Paste-a-URL resolver: handles `viewtopic.php?t=`, `?p=`, `?f=&t=`, `viewforum.php?f=`, with or without `/phpBB2/`. Uses a compact JSON map `post_id → [topic_id, page]` (≈145k entries), loaded only on this page. |
| `/about/` | Forum history (2003–2022) and the fact that the archive is a read-only preservation. Mention that "a forum moderator" saved the database (no name). Explain what's missing (images) and why. Removal-request contact. |
| `/search/` | Pagefind UI |

**Link rewriting (inside posts):** Rewrite any `johnlsayers.com/(phpBB2/)?viewtopic.php?…t=N` → `/t/N/`. Rewrite `…?p=N` → `/t/<topic>/page/<k>/#pN`. Rewrite `viewforum.php?f=N` → `/forum/<slug>/`. Links pointing into excluded forums or at missing posts become plain text with a small note "(link to a thread not included in this archive)". Leave external links as they are, with `rel="nofollow noopener"`.

---

## 6. Phases

Each phase ends with a checkpoint. Show the owner the result before moving on when it says **[owner check]**.

### Phase 1 — Extract (`tools/01_extract.py`)
- Parse the dump → `data/archive.db` (SQLite). Include **only** the kept forums and the whitelisted columns above. Users table: `user_id, username` only.
- Exclude moved-shadow topics (keep a moved-ID map), in_message attachments, and orphan attachments.
- Print counts (expected ≈15.4k topics / ≈140k posts; confirm the exact numbers).
- **Privacy assertion:** fail the build if any column named like `*email*`, `*ip*`, `*password*`, `*sig*` or `*pm*` exists in `archive.db`.

### Phase 2 — Render (`tools/02_render.py`, a library the build uses)
- `bbcode_to_html(text, uid, attachments_for_post, link_resolver) -> html`. Write unit tests with real sample posts: nested quotes, lists, url with `&#58;`, inline attachment, smilies, magic URLs, color/size, code.
- **Sanitize the output.** Only allow a small set of tags/attributes; strip `style` except safe `color` / `font-size`.
- **PII scrubber**, run on the final text:
  - Emails (including `name at domain dot com` obfuscations) → `[removed]`
  - Phone numbers (international and North American formats; avoid false positives on measurements like `2x4`, `1/2"`, `125Hz`, dimensions, `STC 52`, years, and part numbers)
  - Street addresses (number + street-type word such as St/Street/Ave/Rd/Road/Lane/Blvd, etc.)
  - Write every uncertain match to `data/pii_review.csv` (post_id, snippet, rule) for the owner.
- **[owner check]** Build a bare, unstyled local site for ~50 sampled threads (include the biggest diaries and the stickies) and show it to the owner.

### Phase 3 — Image recovery (`tools/03_wayback.py`, background)
- As in section 4. Start it early, since it takes hours. Output `data/recovered.csv` (attach_id, local_path, source_url, timestamp).

### Phase 4 — Ranking (`tools/04_rank.py`)
- Compute a per-topic score from these signals:
  - log(views)
  - log(replies)
  - sticky/announcement bonus
  - share of posts by top-50 experts
  - presence of John Sayers
  - count of recovered images
  - number of other posts linking to the topic (inbound internal links are a strong "reference" signal)

  Store the score, and the per-signal breakdown so the owner can see why a thread ranked where it did.
- Mark **build diaries**: forum 10/11, or titles matching build/diary/construction/"'s studio", or threads where the starter's posts make up ≥30% of all posts and there are more than 100 replies.
- Output the top ~2,500 to `data/candidates.jsonl`.

### Phase 5 — Topic tagging and owner review
- **Draft taxonomy** (the owner may edit):
  1. Fundamentals: isolation vs. treatment, room-in-room, mass-air-mass
  2. Walls, floors and ceilings: framing, drywall, decoupling, floating floors, hangers
  3. Doors and windows
  4. HVAC, ventilation and silencer boxes
  5. Acoustic treatment: panels, bass traps, diffusers, materials
  6. Control room design: geometry, RFZ, reflection control
  7. Speakers and soffit / flush mounting
  8. Electrical, grounding and wiring
  9. Codes, permits and planning
  10. Measurement and tools: REW, SketchUp
  11. Materials data and reference
  12. Build diaries, with subtags garage / basement / barn-outbuilding / commercial / room-in-house / other
- **Tagging:** use Claude Code subagents in batches of ~50 threads. For each thread, give the agent the title, the first post (trimmed to ~1.5k chars) and up to 3 expert replies (trimmed). Ask for strict JSON: `{topic_id, primary_topic, secondary_topics[], diary_subtag|null, is_reference_quality: bool, confidence}`. Append the results to `data/tags.jsonl`, resumable. **Validate the JSON**; re-run any failures.
- **Topic intros:** one subagent per topic drafts a 120–200 word intro using only the tagged threads' content, and links 3–5 key threads. The intro carries a visible label: "Introduction drafted with AI assistance from forum posts."
- **Review UI** (`tools/review/`): a local static HTML page, opened from the filesystem or `python3 -m http.server`. For each topic it shows ranked candidates with approve / move-to-topic / drop / pin-to-top controls, plus editable intros. It exports `data/curation.json`, which the owner saves into `data/`. The build uses only `curation.json`. **[owner check]**

### Phase 6 — Design and full build (`tools/build.py`)
- Load the `frontend-design` skill. Offer the owner 2–3 design directions (typography-led, readable, archival; light and dark modes), then implement the chosen one.
- Templates: Jinja2 (`pip install jinja2` into a venv at `.venv/`). One CSS file, no framework, and system fonts or a self-hosted webfont (no Google Fonts calls, for longevity).
- Generate the whole site into `site/`. Deterministic output, so re-running produces identical files.
- Include a `sitemap.xml`, a `robots.txt` that allows everything, and `<link rel="canonical">`. Every thread page gets a small "Originally posted at johnlsayers.com, topic N" note.
- Performance target: a full build in minutes, not hours.

### Phase 7 — Search
- Run Pagefind over `site/` (`npx pagefind --site site`, or download the standalone binary into `tools/bin/`). Index thread titles and post bodies. Mark Library pages with higher weight, and exclude navigation chrome using `data-pagefind-ignore`.

### Phase 8 — QA
- Check links across the whole site (no broken internal links).
- **Privacy scan** of `site/`: grep for email patterns, IPv4 addresses, `sid=`, and phone patterns. Any hit fails the build.
- Spot-check rendering on the 20 most-viewed threads and all stickies.
- Check that pages work at phone width and with JS disabled.
- Report the final counts: threads, posts, recovered images, placeholders, and PII removals.

### Phase 9 — Publish
- Start a git repo in this folder. Write a `.gitignore` covering `.venv/`, `data/images/orig/` (if too large) and the raw dumps.
  - **Never commit the SQL dumps**; they contain emails, IPs and private messages.
  - `archive.db` holds only whitelisted fields and may be committed if it's under ~100 MB. Otherwise publish it as a release asset.
- Create a public GitHub repo (**ask the owner before creating or pushing anything**). Add a README explaining the project and how to rebuild.
- Deploy: give the owner the `site/` folder to upload (rsync/SFTP) to their host. **Do not deploy until `{{REMOVAL_CONTACT}}` is filled in.**

---

## 7. Proposed project layout

```
johnlsayersarchive/
  PLAN.md            ← this file
  CLAUDE.md
  tools/             ← numbered pipeline scripts, build.py, review/ UI
  templates/         ← Jinja templates
  static/            ← css, fonts, favicon
  data/              ← archive.db, candidates, tags, curation.json, recovered.csv, images
  site/              ← generated output (deploy this)
```

## 8. Guardrails
- The raw dumps in `../` are the only copy of this forum. **Treat them as read-only; never modify or move them.**
- Never output user emails, IPs, private messages, or passwords anywhere, including logs and debugging prints.
- Don't publish or push anything without the owner's explicit OK.
- Keep AI-written text to the labelled topic intros.

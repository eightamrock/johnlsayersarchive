# johnlsayersarchive.com

A read-only archive of the Recording Studio Design Forum that ran at johnlsayers.com from 2003 to 2022. After John Sayers died the site went offline; a forum moderator had kept a copy of the database, and this archive is built from it, with permission.

The finished website is in `site/`. It is plain HTML, one `index.html` per folder, and runs on any web server with no configuration. Search uses [Pagefind](https://pagefind.app) and runs in the browser.

## What is in this repository

- `site/`: the generated website, ready to upload.
- `tools/`: the numbered pipeline that turned the forum database into the site.
- `templates/`, `static/`: page templates, the stylesheet and self-hosted fonts.
- `data/`: curation and image-recovery records (`curation.json`, `tags.jsonl`, `recovered.csv` and so on).

The forum database itself is not here. Post text in it still contains the email addresses, phone numbers and street addresses that the build removes from the published pages, so it stays private.

## Rebuilding

You need the original SQL dump (`jls_archive_good.sql.gz`, kept privately in the folder above this one), Python 3.9+ and Node.

```sh
python3 -m venv .venv && .venv/bin/pip install jinja2 pillow requests
python3 tools/01_extract.py                  # dump -> data/archive.db (whitelisted columns only)
python3 tools/04_rank.py                     # score threads -> data/candidates.jsonl
.venv/bin/python tools/build.py             # site/ + search index
python3 tools/qa.py                          # link check, privacy scan, counts
```

Image recovery from the Wayback Machine (`tools/03_wayback.py`, `tools/08_wayback_external_images.py`) takes hours and only needs to run once; its results are recorded in `data/recovered*.csv`. Topic tagging and the Library introductions were drafted with AI assistance (`tools/05_*`, `tools/06_*`) and can be reviewed in `tools/review/index.html`, which exports `data/curation.json`.

`PLAN.md` records every decision behind the archive.

## Removal requests

If you wrote a post here and want it removed, see the contact on the site's About page.

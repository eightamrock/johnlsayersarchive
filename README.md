# johnlsayersarchive.com

A read-only archive of the Recording Studio Design Forum that ran at johnlsayers.com from 2003 to 2022. After John Sayers died the site went offline; a forum moderator had kept a copy of the database, and this archive is built from it, with permission.

The finished website is in `site/`. It is plain HTML, one `index.html` per folder, and runs on any web server with no configuration. Search uses [Pagefind](https://pagefind.app) and runs in the browser.

## What is in this repository

- `site/`: the generated website, ready to upload.
- `tools/`: the numbered pipeline that turned the forum database into the site.
- `templates/`, `static/`: page templates, the stylesheet and self-hosted fonts.
- `data/`: curation and image-recovery records (`curation.json`, `tags.jsonl`, `recovered.csv` and so on).

The forum database itself is not here. Post text in it still contains the email addresses, phone numbers and street addresses that the build removes from the published pages, so it stays private.

## Publishing

The site is hosted on GitHub Pages. `.github/workflows/pages.yml` uploads whatever is committed in `site/` whenever it changes on `main`, so publishing is: build, run QA, commit, push.

## Removal requests

If you wrote a post here and want it removed, see the contact on the site's About page.

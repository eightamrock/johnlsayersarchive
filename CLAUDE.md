# johnlsayersarchive

Static tribute archive of the johnlsayers.com phpBB studio-design forum.

**Read `PLAN.md` before doing anything.** It holds every settled decision, the verified facts about the source data, and the phased build steps. Don't re-ask settled decisions. Work through the phases in order, and stop at each **[owner check]**.

- Source dump: `../jls_archive_good.sql.gz`. It is plain SQL, not gzip. Read-only; never modify it.
- `tools/load_dump_reference.py`: a tested dump → SQLite parser to reuse.
- Never output or commit emails, IPs, private messages, or passwords.

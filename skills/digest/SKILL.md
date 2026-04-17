# Digest Module

Synthesize today's information additions into a daily brief the user
actually reads. Digest is the user-facing surface of the
`radar → ingest → digest` loop. It draws on [[ingest]]'s structured
concept extracts (claims, persona_relevance) to explain *why* a new
concept matters, not just *that* it was added.

This is the only module that produces user-facing narrative. Radar is
silent (just drops files), ingest is structural (just builds wiki
pages). The narrative live here.

## Trigger
Runs as the third stage of the daily chain: `radar → ingest → digest`.
Also runnable manually via `./run-module.sh digest` for ad-hoc
re-synthesis (e.g., after a manual ingest of user-curated books).

## Inputs
- `skills/ingest/state_db.py merged-on <date>` — find books merged today
- `extracts/ingest/state/{book-slug}.yaml` — per-book metadata
- `extracts/ingest/{book-slug}.yaml` — structured concepts per book
- `wiki/topics/*.md` — for the synthesized "Core Idea" text on each new page
- `extracts/radar/YYYY-MM-DD.md` — today's radar audit log (for the rejected-items section)
- `persona/character_sheet.md` — voice and framing for the brief

## Process

### 1. Determine what's new today
Compute today's date as `YYYY-MM-DD`.

Query the ingest state index for books merged today:

```bash
TODAY=$(date -u +%F)
python skills/ingest/state_db.py merged-on "$TODAY"
```

Stdout is JSON: `[{slug, title, domain, concept_count}, ...]`. For
each returned slug:
- `Read` `extracts/ingest/state/{slug}.yaml` for metadata and any
  recorded wiki pages.
- `Read` `extracts/ingest/{slug}.yaml` for the concept body.

Pool every concept across all of today's books into a single working
set, keyed by wiki page slug. If the same concept slug appears in
multiple books (an enrichment merge), keep both perspectives.

If the `merged-on` list is empty, write a one-line "nothing new today"
digest (see step 4) and exit cleanly. The point is to make it visible
that the system *ran*, not that it's broken.

### 2. Rank concepts
For each new concept in the working set, score importance:

- **+3** if the concept's `persona_relevance` text explicitly names a
  known interest, expertise area, or bias from `character_sheet.md`
  (read the character sheet first to know what those are).
- **+2** if it has ≥3 substantive `claims`.
- **+1** if it landed in `wiki_pages_created` (truly new) rather than
  `wiki_pages_updated` (a new perspective on a known idea).
- **+1** if its `domain_tags` overlap with the user's primary domains
  (trading, agent, ml, formal-methods).

The top 3–5 by score become **Top picks**. Everything else goes in
**Also added** (if `created`) or **Existing pages enriched** (if `updated`).

If fewer than 3 concepts qualify, that's fine — promote what you have.
Don't pad to hit a number.

### 3. Write the digest
Output to `wiki/digests/YYYY-MM-DD.md`:

```markdown
# Digest — 2026-04-09

#digest

**Today:** 4 articles read, 7 new concept pages, 2 existing pages enriched.

## Top picks

### [[concept-page-slug|Concept Name]]
*From "Source Title" — Author*

Two-to-three sentence framing in the user's voice. Pull from the
concept's `persona_relevance` field but rewrite it as direct address
("This challenges your assumption that…", "Worth reading because…")
instead of as a metadata blurb. End with a wikilink to the page.

→ [[concept-page-slug]]

### [[next-pick]]
...

## Also added
- [[concept-slug-1]] — one-line description from the concept's `summary` #domain-tag
- [[concept-slug-2]] — one-line description #domain-tag
...

## Existing pages enriched
- [[existing-page-slug]] — new perspective from "Source Title"
...

## Filtered out
Radar saw N items below the relevance threshold. See
[[../../extracts/radar/2026-04-09|today's radar audit log]] for titles
and links if you want to override and pull any of them in manually.
```

For the **nothing-new-today** case, write a much shorter file:

```markdown
# Digest — 2026-04-09

#digest

Nothing new today. Radar scanned N sources and either found no items
above the relevance threshold or all candidates were already in the
corpus. See [[../../extracts/radar/2026-04-09|today's radar audit log]]
for what was filtered out.
```

### 4. Tone and voice
The digest is written *to* the user, not *about* the user. Read
`persona/character_sheet.md` first and match its register: terse,
direct, no hedging, no marketing tone. The user already trusts that
the concepts on the wiki are real — digest's job is to surface
*priority* and *connection*, not to re-justify the wiki's existence.

Avoid:
- "Today we saw…" (corporate plural)
- "I think you might find this interesting" (hedging)
- Long preambles before the actual content
- Restating the concept's title as if it's a discovery

Prefer:
- "This sharpens the [[mean-reversion]] argument you had with X"
- "Reads as a counter to your current position on Y"
- "Skip if you've already seen Z"

### 5. Log
Append one row to `wiki/log.md`:

```
| 2026-04-09 07:30 | digest | synthesize | Daily brief: 4 top picks, 12 also-added, 2 enriched |
```

For nothing-new days:

```
| 2026-04-09 07:30 | digest | synthesize | Nothing new today |
```

## Output
- `wiki/digests/YYYY-MM-DD.md` — the daily brief (Syncthing'd to user
  machine, viewable in Obsidian)
- One row in `wiki/log.md`

## What digest does NOT do
- Re-read source articles. That's [[ingest]]'s job. Digest only
  synthesizes from ingest's structured extracts and the wiki pages
  ingest already built.
- Make persistent wiki edits beyond the digest file and the log row.
- Email, notify, or push. For v0.1 the digest just lands in
  `wiki/digests/` and you read it in Obsidian via the bidirectional
  Syncthing sync.
- Re-rank or re-filter what ingest already merged. If a concept made
  it into the wiki, digest's job is to *frame* it, not to second-guess
  whether it should be there.

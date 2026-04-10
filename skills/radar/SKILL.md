# Radar Module

Scan monitored sources, triage by relevance, and drop articles worth
reading into the corpus. Radar does **not** read article bodies for
content, write summaries, or touch the wiki. It only decides what
should enter `sources/corpus/reading/radar/`. The actual reading
happens in [[ingest]] on its next run, which detects radar's drops
via hash diff and processes them through the same pipeline as
user-curated books.

## Trigger
Daily cron at 7am.

## Inputs
- `skills/radar/sources.yaml` — source list with URLs/queries and categories
- `extracts/radar/state.yaml` — URLs already triaged (de-dup)
- `persona/character_sheet.md` — interest profile for relevance gating

## Process

### 1. Pre-flight
- Read `persona/character_sheet.md` to load current interests, expertise,
  and biases.
- Read `extracts/radar/state.yaml` into a set of seen URLs.

### 2. Scan sources
For each source in `skills/radar/sources.yaml`:
- If `method: web_fetch` — fetch the listing page (WebFetch) and extract
  item URLs from the rendered markdown.
- If `method: web_search` — run the query (WebSearch) and collect result URLs.
- If `method: script_search` — run the search via Bash using the Python
  scripts symlinked at `scripts/pubmed-reader/`. Build and execute:

      python3 -c "
      import sys, json
      sys.path.insert(0, 'scripts/pubmed-reader')
      from {source.script} import {source.function}
      result = {source.function}('{source.query}', **{source.params as kwargs})
      print(json.dumps(result, default=str))
      "

  Parse the JSON output. Each article in `result['articles']` has:
  - `title` — always present
  - `abstract` — present for arXiv (via API), empty for bioRxiv
    (website scrape returns titles only)
  - arXiv articles: canonical URL is `https://arxiv.org/abs/{arxiv_id}`
    (use the `arxiv_id` field, strip any version suffix for de-dup)
  - bioRxiv articles: use the `url` field directly

  **bioRxiv abstract enrichment**: bioRxiv search results lack abstracts.
  After collecting search hits from a bioRxiv source, fetch abstracts in
  a single batch call before scoring relevance:

      python3 -c "
      import sys, json
      sys.path.insert(0, 'scripts/pubmed-reader')
      from fetch_biorxiv import batch_fetch_biorxiv
      result = batch_fetch_biorxiv([{list of DOIs from search results}])
      print(json.dumps(result, default=str))
      "

  The returned `result['articles']` dict is keyed by DOI; each entry has
  `title`, `abstract`, `authors`, `posted_date`, and `category`. Use
  `title` + `abstract` together for relevance scoring.

  **Rate limiting**: arXiv bans aggressively (>1 req / 3 sec). The scripts
  handle internal throttling, but you MUST process all `script_search`
  sources **sequentially** — never fire multiple Bash calls in parallel.
  If you get an HTTP 429 or timeout, skip that source and record
  `action: fetch_failed` for it in the audit log; do not retry.

- **URL normalization** — before de-dup, canonicalize URLs so the same
  paper doesn't sneak through under two identities:
  - HuggingFace paper URLs (`https://huggingface.co/papers/{id}`) →
    `https://arxiv.org/abs/{id}`. The HF page is a community wrapper
    around the arXiv paper; use the arXiv canonical form everywhere
    (de-dup key, state.yaml, corpus frontmatter `source_url`).
  - arXiv version suffixes (`v1`, `v2`, …) — strip for de-dup, preserve
    in fetch URL (existing behavior).
- Drop any item whose normalized URL is already in the seen set (structural de-dup).
- For items from **HuggingFace Daily Papers** where no abstract is
  available from the listing page: fetch the individual HF paper page
  (`https://huggingface.co/papers/{id}`) to retrieve the abstract
  before scoring. This is a lightweight fetch (one page per candidate),
  not the full-text fetch in step 3.
- Score remaining items by relevance to the persona profile (1–5) using
  the title and any available abstract text.

Items at relevance ≥3 proceed to step 3. Items below 3 are recorded as
seen with `action: filtered_out` and skipped.

### 3. Fetch full content
For each item that passed the relevance gate:

- **arXiv pages** (`https://arxiv.org/abs/{id}` or `.../abs/{id}v{n}`) —
  rewrite `/abs/` to `/html/` and fetch that. The HTML render contains
  the full paper text, not just the abstract. Preserve the version
  suffix if present; otherwise omit it (arXiv resolves `/html/{id}` to
  the latest version). If the HTML render is missing or returns 404,
  fall back to the abstract page and add `note: html_missing` to the
  state entry so the audit log surfaces it (action stays `dropped`).
- **bioRxiv pages** (`https://www.biorxiv.org/content/{doi}`) — fetch
  the full text via the script rather than WebFetch (which would only
  return the landing page chrome):

      python3 -c "
      import sys, json
      sys.path.insert(0, 'scripts/pubmed-reader')
      from fetch_biorxiv_fulltext import get_biorxiv_fulltext
      result = get_biorxiv_fulltext('{doi}')
      print(json.dumps(result, default=str))
      "

  The result contains `title`, `abstract`, and section text (`sections`
  list). Concatenate sections into markdown for the corpus drop. If the
  full text is unavailable (JATS missing), fall back to the abstract
  from the enrichment step and add `note: fulltext_missing` to the
  state entry.
- **Other URLs** — fetch directly with WebFetch. WebFetch returns
  markdown.

If a fetch fails or the body is suspiciously short (< 500 chars),
record `action: fetch_failed` and skip the drop. **Do not write a
stub file** — an empty drop would just give ingest garbage to chew on.

### 4. Drop into corpus
Write the fetched markdown to:

    sources/corpus/reading/radar/{category}/{slug}.md

Where:
- `{category}` is the source's `category` field from `sources.yaml`
  (`trading`, `llm`, `bioinformatics`, `tech`, `math`, …). The
  `radar/` prefix keeps radar's drops in their own namespace inside
  the corpus, so they never collide with user-curated subdirs.
- `{slug}` is a lowercased-kebab-case form of the title, prefixed with
  the arXiv id when applicable (e.g. `2604.07236-self-revising-agent.md`).
  Truncate to ~80 chars to keep paths sane.

Prepend YAML frontmatter so [[ingest]] (and you, when browsing) know
the provenance:

    ---
    source_url: https://arxiv.org/abs/2604.07236
    fetched_at: 2026-04-09
    radar_category: llm
    radar_relevance: 4
    title: "How Much LLM Does a Self-Revising Agent Actually Need?"
    ---

The body is the markdown returned by WebFetch, unmodified.

**Never overwrite an existing corpus file.** If the target path already
exists (user-curated, or a prior radar run wrote it), skip the drop and
record `action: already_in_corpus`. The corpus is sacred — radar adds,
never edits.

### 5. Update state and audit log

Append every URL processed in this run to `extracts/radar/state.yaml`
under `seen:`. This must happen for *every* item, not just the ones
that got dropped, so the de-dup gate works tomorrow:

    - url: https://arxiv.org/abs/2604.07236
      source: arXiv AI/LLM
      title: "How Much LLM Does a Self-Revising Agent Actually Need?"
      first_seen: 2026-04-09
      relevance: 4
      action: dropped         # | filtered_out | fetch_failed | already_in_corpus
      corpus_path: sources/corpus/reading/radar/llm/2604.07236-self-revising-agent.md  # only if action == dropped
      note: html_missing      # optional, e.g. for arxiv items where the HTML render was unavailable

Write a thin audit log to `extracts/radar/YYYY-MM-DD.md` — counts and
links only, **no summaries** (summaries are ingest's job, not radar's):

    # Radar Run — 2026-04-09

    Sources scanned: 7
    Items seen: 32
    Items dropped: 18
    Filtered out (relevance < 3): 11
    Fetch failed: 3

    ## Dropped
    - llm/2604.07236-self-revising-agent.md ⭐ 4 ← https://arxiv.org/abs/2604.07236
    - ...

    ## Filtered out
    - ⭐ 2 https://arxiv.org/abs/2604.06608 — SoK of RWA Tokenization
    - ...

    ## Fetch failed
    - https://example.com/broken-link
    - ...

Append one summary row to `wiki/log.md`:

    | 2026-04-09 07:00 | radar | scan | Dropped 18 items into corpus, filtered 11, 3 failed |

## Output
- New files under `sources/corpus/reading/radar/{category}/`
  (consumed by ingest on its next run)
- `extracts/radar/state.yaml` — URL de-dup state (git-tracked, autocommitted)
- `extracts/radar/YYYY-MM-DD.md` — thin audit log (gitignored)
- One row in `wiki/log.md`

## What radar does NOT do
- Read article bodies for content beyond the relevance score
- Write summaries, digests, or wiki pages
- Modify or delete any file in `sources/`
- Run ingest itself — that's a separate manual step

If you ever feel tempted to summarize an article inside radar, stop:
that's a sign the work belongs in [[ingest]].

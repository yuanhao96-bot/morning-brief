# Ingest Module

Read documents from `sources/corpus/reading/`, extract concepts, and
merge them into the wiki knowledge base. Inputs can be books the user
dropped in via Syncthing or articles the [[radar]] module fetched into
`sources/corpus/reading/radar/{category}/`. Both go through the same
hash-based pipeline — ingest doesn't care where a file came from.

## Trigger

Manual — run after syncing new books into `sources/corpus/reading/`.

## Inputs

- `sources/corpus/reading/` — books as PDF, txt, or md files
- `persona/character_sheet.md` — user persona for relevance tagging
- `extracts/ingest/state/{slug}.yaml` — per-book metadata (git-tracked,
  written exclusively by `state_db.py`)
- `extracts/ingest/{slug}.yaml` — per-book concept extracts with their
  `source_files:` block (git-tracked)
- `extracts/ingest/.cache/state.db` — derived SQLite index, gitignored
- `wiki/topics/` — existing wiki pages (for deduplication)

**State-writer contract.** `state_db.py` is the **sole authoritative
writer** of `extracts/ingest/state/{slug}.yaml` and of the
`source_files:` block inside `extracts/ingest/{slug}.yaml`. This skill
writes the concept body of the extract YAML (title, concepts, claims)
directly with `Write`, then shells out to `state_db.py` to advance
status and persist source attribution. Never hand-edit a state shard.

## Process

### Phase 0 — MANDATORY full scan

**Do not skip or shortcut this section.** Every ingest run must scan
the entire corpus, not just radar drops from the current day.

1. Read `persona/character_sheet.md` to load the user's interests,
   expertise, values, and biases.

2. **Full corpus scan (required before any extraction):**
   Glob `sources/corpus/reading/**/*.{pdf,txt,md}` to list every
   file in the corpus across ALL subdirectories — `radar/`, `trading/`,
   `philosophy/`, `economics/`, `science/`, and any other directories
   the user may have created. Print the complete directory listing.
   This is the checkpoint: if you haven't printed the listing, you
   haven't done the scan.

3. **Hash every corpus file and write the manifest to `/tmp/corpus.json`:**
   ```bash
   python -c "
   import hashlib, json, sys
   from pathlib import Path
   corpus = []
   for p in Path('sources/corpus/reading').rglob('*'):
       if p.suffix.lower() in {'.pdf','.txt','.md'}:
           corpus.append({'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
   json.dump(corpus, sys.stdout)
   " > /tmp/corpus.json
   ```

4. **Diff against the index:**
   ```bash
   python skills/ingest/state_db.py diff < /tmp/corpus.json
   ```

   Stdout is a JSON list of corpus paths that are either unprocessed
   (absent from the `files` table) or changed (sha256 differs from
   what the index has recorded). No LLM-driven set-math — the DB
   answers the question exactly.

5. **Print the scan report before proceeding:**
   ```
   Pre-flight scan:
     Directories found: <list all subdirs of sources/corpus/reading/>
     Total files: N
     Unprocessed/changed from diff: X (list paths)
   ```
   This report is the gate to Phase 1. Do not proceed to extraction
   until it is printed.

6. **Register each new book.** Group the returned paths by proposed
   book (by directory, by radar-category aggregation, or 1:1 for a
   standalone file) and for each new book slug, register it:
   ```bash
   python skills/ingest/state_db.py upsert-book \
     --slug "$SLUG" --title "..." --domain "$DOMAIN" \
     --source-path "..." --status pending
   ```
   This writes `extracts/ingest/state/{slug}.yaml` and the `books`
   row in one atomic call.

### Phase 1 — Extract

For each book with status `pending` (as reported by `state_db.py diff`
and now registered via `upsert-book`):

1. **Parse the book:**
   - PDF: Use the `markitdown` skill to convert to markdown text.
   - txt/md: Read directly.
   - If the file has YAML frontmatter (radar drops do), extract
     `source_url` and carry it through to the extract file and
     wiki pages.

2. **Chunk large books:**
   Books over ~50 pages (or ~25,000 words) should be chunked by
   chapter or major section. Split on `# ` headings in markdown,
   or on clear chapter boundaries in converted PDFs.
   For chunk-at-a-time progress, call `upsert-book --status extracting`
   between chunks; the per-slug lock in `state_db.py` serializes
   concurrent writes.

3. **Extract concepts from each chunk:**
   For each chunk, identify:
   - **Concepts**: Core ideas, theories, frameworks, mental models.
     Each concept gets:
     - `id`: lowercase-kebab-case identifier
     - `name`: Human-readable concept name
     - `summary`: 2-4 sentence description of the concept
     - `claims`: Specific assertions with page/section references
     - `related_concepts`: IDs of other concepts in this book
     - `domain_tags`: Category tags (e.g., trading, risk-management)
     - `persona_relevance`: How this relates to the user's known
       interests, expertise, or biases from character_sheet.md

   Focus on **core themes**, not passing mentions. A concept should
   represent a substantive idea the author develops, not a single
   offhand reference.

4. **Write the extract file body** (concepts only — **not** the
   `source_files:` block; that block is owned by `state_db.py` and
   gets written by `complete-extraction` in the next step):
   Save to `extracts/ingest/{book-slug}.yaml` where `book-slug`
   is the filename without extension, lowercased, spaces replaced
   with hyphens.

   Format:
   ```yaml
   book:
     title: "Book Title"
     author: "Author Name"
     domain: trading  # or science, philosophy
     source_path: sources/corpus/reading/trading/book-file.pdf
     source_url: https://arxiv.org/abs/2604.07236  # from corpus frontmatter, if present
     slug: book-slug

   concepts:
     - id: concept-slug
       name: "Concept Name"
       summary: >
         2-4 sentence description of the concept.
       claims:
         - text: "Specific claim from the book"
           location: "Chapter N, p.XX"
       related_concepts: [other-concept-id]
       domain_tags: [domain, subtopic]
       persona_relevance: >
         Brief note on how this connects to the user's
         known interests or challenges known biases.
   ```

5. **Advance status to `extracted` atomically.**
   Build the source_files JSON (one entry per corpus file that fed
   this book) and pipe it to `complete-extraction`:
   ```bash
   SOURCE_FILES_JSON=$(python -c "
   import hashlib, json, sys
   from pathlib import Path
   files = [
     # list every corpus path that fed this book
     'sources/corpus/reading/trading/book.pdf',
   ]
   out = []
   for rel in files:
       data = Path(rel).read_bytes()
       out.append({
           'path': rel,
           'sha256': hashlib.sha256(data).hexdigest(),
           'size': len(data),
       })
   json.dump(out, sys.stdout)
   ")
   echo "$SOURCE_FILES_JSON" | python skills/ingest/state_db.py \
     complete-extraction --slug "$SLUG" \
     --extracted-at "$(date -u +%FT%TZ)"
   ```

   `complete-extraction` bundles every write Phase 1 needs into one
   subprocess: it writes `source_files:` into the concept extract
   YAML, then advances the state shard's `status` to `extracted`
   (writing `extracted_at` at the same time), then updates the DB.
   A process crash at any point leaves a consistent pre-advance state
   — the next ingest run's fingerprint check triggers rebuild, which
   succeeds because the book is still `extracting` (source_files
   tolerated) or cleanly `extracted` (source_files present).

6. **Log to wiki/log.md:**
   Append a row:
   ```
   | 2026-04-08 10:00 | ingest | extract | Extracted 12 concepts from "Book Title" |
   ```

7. Repeat for each pending book.

After all extractions complete, proceed to Phase 2.

### Phase 2 — Merge

Merge extracted concepts into wiki pages. Default: incremental
merge (only process newly extracted books). Use `--full` flag
to re-merge all extracts.

For each book with status `extracted` (or all books if `--full`):

1. **Load the extract file** from `extracts/ingest/{book-slug}.yaml`.

2. **For each concept in the extract, shortlist candidate existing
   pages via [[query]]:**

   Build a query string by concatenating the concept's `name` and
   `summary`, then call:

   ```bash
   python -m skills.query.query "{name}. {summary}" --k 10 --format json
   ```

   Parse the JSON. Read the markdown at each `path` in the results
   (up to 10 pages) to have their full content available for step 3.
   An empty `results` list means no existing page lexically matches —
   step 3 will treat the concept as new.

   This replaces scanning the entire `wiki/topics/` directory per
   run. BM25 shortlisting stays fast regardless of wiki size and
   keeps the classifier's attention on genuinely relevant candidates.
   If query misses a true duplicate because of vocabulary mismatch,
   step 3 creates a near-duplicate page — recoverable later, cheaper
   than rereading the full corpus every run. For concepts with
   unusual terminology, re-run query with alternative phrasings
   before committing to "new".

3. **For each concept in the extract, classify it against its
   shortlist:**

   - **New concept** — no shortlist page covers this idea
     (including the empty-shortlist case).
     Action: Create `wiki/topics/{concept-id}.md`.

   - **Enrich existing** — a shortlist page covers the same core
     idea (even if named differently).
     Action: Add a new subsection under `## Perspectives` in the
     existing page, citing this book. Update `## Related` links
     if needed.

   - **Related but distinct** — a shortlist page covers a related
     but clearly different idea.
     Action: Create a new page. Add `[[wikilinks]]` to and from
     the related page.

4. **Wiki page format for new concepts:**

   ```markdown
   #domain-tag #subtopic-tag

   # Concept Name

   One-line description of the concept.

   ## Core Idea

   2-4 paragraph explanation synthesized from the book's treatment
   of this concept. Written in the user's voice (informed by
   persona/character_sheet.md), not as generic book notes.

   ## Perspectives

   ### Book Title — Author Name
   Key claims and arguments from this book, with page references.
   (Ch. N, p.XX)

   For radar-sourced articles with a `source_url` in their corpus
   frontmatter, include the link in the heading:

   ### Paper Title ([source](https://arxiv.org/abs/2604.07236))
   Key claims and arguments from this paper.

   ## Persona Connection

   How this concept relates to the user's known interests,
   challenges existing assumptions, or connects to their work.

   ## Related

   - [[related-concept-1]]
   - [[related-concept-2]]
   ```

5. **Conflict handling:**
   When two books contradict each other on the same concept page,
   keep both perspectives under their respective `### Book Title`
   headings. Add a `## Tensions` section between `## Perspectives`
   and `## Persona Connection`:

   ```markdown
   ## Tensions

   Book A argues X while Book B argues Y. The disagreement stems
   from [brief analysis of why they differ].
   ```

6. **Consolidation heuristic:**
   If a concept appears in only one book and is a minor mention
   (not a core theme — fewer than 2 claims, summary under 1
   sentence), fold it as a subsection into a broader related topic
   page rather than creating a standalone page. Add a heading like
   `### Minor: Concept Name` under the related topic's
   `## Core Idea` section.

7. **Advance status to `merged` atomically.**
   After writing all wiki pages for this book, invoke:
   ```bash
   python skills/ingest/state_db.py complete-merge \
     --slug "$SLUG" --merged-at "$(date -u +%FT%TZ)" \
     --concept-count "$N"
   ```

   `complete-merge` sets `status=merged`, `merged_at`, and
   `concept_count` in the state shard and DB in one subprocess.
   The command enforces state-machine preconditions: it requires
   the prior status to be `extracted` and the extract YAML to carry
   a `source_files:` block — illegal transitions exit non-zero
   before touching anything.

   Recording the wiki pages touched as slug lists (for digest's
   consumption) is out of scope for the current CLI — track in a
   follow-up `wiki-pages` subcommand if the need grows. Do **not**
   hand-edit `extracts/ingest/state/{slug}.yaml`.

8. **Update wiki/index.md:**
   Under `## Topics`, add entries for each new concept page:
   ```markdown
   - [[concept-name]] — one-line description #domain-tag
   ```
   Under `## Books`, add an entry for the ingested book:
   ```markdown
   - **Book Title** by Author — N concepts extracted #domain
   ```
   Keep entries sorted alphabetically within each section.

9. **Log to wiki/log.md:**
   Append a row:
   ```
   | 2026-04-08 10:15 | ingest | merge | Merged "Book Title": 3 new pages, 2 updated |
   ```

   The downstream [[digest]] module runs immediately after ingest and
   queries `state_db.py merged-on $(date -u +%F)` to find today's
   merges. Make sure `complete-merge` has been called for every
   finished book before ingest exits — otherwise digest sees an
   empty list and writes a "nothing new today" brief by mistake.

## Output

- `extracts/ingest/{book-slug}.yaml` — per-book structured extracts
  (body written by this skill; `source_files:` written by CLI)
- `extracts/ingest/state/{book-slug}.yaml` — per-book state shard
  (written exclusively by `state_db.py`)
- `extracts/ingest/.cache/state.db` — derived SQLite index (updated
  by every CLI invocation; gitignored)
- `wiki/topics/*.md` — new or updated concept pages
- `wiki/index.md` — updated content catalog
- `wiki/log.md` — activity log entries

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
- `extracts/ingest/state.yaml` — processing state manifest
- `extracts/ingest/` — existing extract files (for incremental merge)
- `wiki/topics/` — existing wiki pages (for deduplication)

## Process

### Pre-flight — MANDATORY full scan

**Do not skip or shortcut this section.** Every ingest run must scan
the entire corpus, not just radar drops from the current day.

1. Read `persona/character_sheet.md` to load the user's interests,
   expertise, values, and biases.
2. Read `extracts/ingest/state.yaml` to load current processing state.
3. **Full corpus scan (required before any extraction):**
   Glob `sources/corpus/reading/**/*.{pdf,txt,md}` to list every
   file in the corpus across ALL subdirectories — including any
   directories the user may have created beyond the defaults.
   Print the complete directory listing. This is the checkpoint:
   if you haven't printed the listing, you haven't done the scan.
4. **Diff the file listing against `state.yaml` coverage.**
   Build the set of all corpus file paths from the glob. Build the
   set of all covered file paths from the union of every entry's
   `files_included` list in `state.yaml`. Any corpus file not in
   the covered set is unprocessed. For each unprocessed file (or
   file with a changed hash), compute its sha256:
   ```bash
   shasum -a 256 <file_path>
   ```
5. **Print the scan report before proceeding:**
   ```
   Pre-flight scan:
     Directories found: <list all subdirs of sources/corpus/reading/>
     Total files: N
     Covered by existing entries: C
     New/unprocessed: X (list paths)
     Changed: Y (list paths)
   ```
   This report is the gate to Phase 1. Do not proceed to extraction
   until it is printed.
6. Add new/changed entries to `state.yaml` with status `pending`.
   Update hashes for changed files.

### Phase 1 — Extract

For each book with status `pending` in `state.yaml`:

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
   Update `state.yaml` status to `extracting` with per-chunk
   tracking:
   ```yaml
   - path: sources/corpus/reading/trading/big-book.pdf
     sha256: ...
     status: extracting
     chunks:
       - range: "chapters 1-5"
         status: extracted
       - range: "chapters 6-10"
         status: pending
   ```

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

4. **Write the extract file:**
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

5. **Update state.yaml:**
   Set the book's status to `extracted` and record. The
   `files_included` field is **mandatory** — it lists every
   corpus file that was read during this extraction. The
   pre-flight scan uses this to detect unprocessed files.
   ```yaml
   - path: sources/corpus/reading/trading/book.pdf
     sha256: abc123...
     status: extracted
     extracted_at: 2026-04-08T10:00:00Z
     concepts_file: extracts/ingest/book.yaml
     concept_count: 12
     files_included:
       - sources/corpus/reading/trading/book.pdf
   ```
   For batch entries (e.g. radar drops grouped by theme), list
   every individual file in the batch:
   ```yaml
   - path: sources/corpus/reading/radar/llm
     sha256: aggregate-6-radar-drops-claude-code
     status: extracted
     files: 6
     files_included:
       - sources/corpus/reading/radar/llm/article-one.md
       - sources/corpus/reading/radar/llm/article-two.md
       - sources/corpus/reading/radar/llm/article-three.md
       - sources/corpus/reading/radar/llm/article-four.md
       - sources/corpus/reading/radar/llm/article-five.md
       - sources/corpus/reading/radar/llm/article-six.md
   ```

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

2. **Load existing wiki topics:**
   Read all files in `wiki/topics/` to build a list of existing
   concept pages (name + content summary).

3. **For each concept in the extract, classify it:**

   - **New concept** — no existing wiki page covers this idea.
     Action: Create `wiki/topics/{concept-id}.md`.

   - **Enrich existing** — an existing wiki page covers the same
     core idea (even if named differently).
     Action: Add a new subsection under `## Perspectives` in the
     existing page, citing this book. Update `## Related` links
     if needed.

   - **Related but distinct** — an existing page covers a related
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

7. **Update state.yaml:**
   Set each processed book's status to `merged`. Record the wiki pages
   touched as **lists of slugs**, not counts — the [[digest]] module
   reads these to know what to feature in the daily brief. Preserve
   the `files_included` list from the extract phase.
   ```yaml
   - path: sources/corpus/reading/trading/book.pdf
     sha256: abc123...
     status: merged
     extracted_at: 2026-04-08T10:00:00Z
     merged_at: 2026-04-08T10:15:00Z
     concepts_file: extracts/ingest/book.yaml
     concept_count: 12
     files_included:
       - sources/corpus/reading/trading/book.pdf
     wiki_pages_created:
       - mean-reversion
       - kelly-criterion
       - drawdown-control
     wiki_pages_updated:
       - position-sizing
       - risk-of-ruin
   ```

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
   will read the per-book entries in `state.yaml` whose `merged_at`
   field matches today's date. Make sure those entries are written
   before ingest exits — otherwise digest will see no work and write
   a "nothing new today" brief by mistake.

## Output

- `extracts/ingest/{book-slug}.yaml` — per-book structured extracts
- `extracts/ingest/state.yaml` — updated processing manifest
- `wiki/topics/*.md` — new or updated concept pages
- `wiki/index.md` — updated content catalog
- `wiki/log.md` — activity log entries

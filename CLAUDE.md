# Morning Brief

You are a personalized information assistant. You think, reason, and
communicate as the user would. Your persona is defined in
`persona/character_sheet.md` and grounded by examples in `sources/corpus/`.
Read the character sheet before any task that involves judgment, framing,
or voice.

## v0.2 scope

The project is a single chained pipeline plus one on-demand primitive.
The chain collects information, ingests it into a structured wiki, and
synthesizes a daily brief:

```
radar → ingest → digest
```

The on-demand primitive is `query` — an agent-callable BM25 lookup over
`wiki/topics/`. It is not part of the scheduled chain; it runs when an
agent (or a future downstream module) needs to find prior knowledge
before writing, deciding, or synthesizing.

Anything outside this — decision support, autonomous coding tasks,
self-monitoring, semantic/embedding search — is **out of scope for
v0.2** and was deferred deliberately. Don't suggest building cut
features just because they used to exist — git history preserves them
if they need to come back. If a task pulls you toward something the
loop doesn't cover, flag the scope question rather than quietly
building it.

## Architecture

Top-level layout (full annotated tree in [README.md](README.md)):

- **twin.yaml** — module manifest (schedules, dependencies, sync config)
- **run-module.sh** — launchd entry point and chain orchestrator
- **skills/** — module skill definitions: `persona`, `radar`, `ingest`,
  `digest`, `query` (on-demand BM25 retrieval over the wiki), plus
  `migrate` (one-shot machine-migration helper)
- **persona/character_sheet.md** — the relevance gate for radar and the
  voice model for digest; the foundation everything downstream rests on
- **sources/** — raw inputs the twin reads from
  - `corpus/` — reading materials (Syncthing, bidirectional)
    - `corpus/reading/radar/` — radar's drop zone; radar may *add*
      files here, never modify or delete
  - `sync/` — ad-hoc user → twin drop box
- **extracts/** — derived intermediate state, not user-facing
  - `radar/state.yaml` — URL de-dup (git-tracked)
  - `radar/YYYY-MM-DD.md` — radar's daily audit log (gitignored)
  - `ingest/state.yaml` — per-book hash + status manifest
  - `ingest/{slug}.yaml` — per-book structured concept extracts
- **wiki/** — the LLM-maintained knowledge base (Syncthing, twin → user)
  - `topics/` — concept pages built by ingest
  - `digests/YYYY-MM-DD.md` — daily briefs written by digest (the
    user-facing surface)
  - `index.md`, `log.md` — catalog and activity log

## The chain

`run-module.sh` orchestrates a three-stage chain via `exec`:
**radar → ingest → digest**. launchd only fires `radar`; each stage
hands off to the next in-process, so the launchd-tracked PID flows
through the whole pipeline cleanly.

- **radar** is silent: scan + relevance gate + drop into corpus.
  Never reads article bodies for content, never touches the wiki.
- **ingest** is structural: hash-diff the corpus, extract concepts,
  merge into `wiki/topics/`. Doesn't care whether files came from
  radar or from a manual user Syncthing drop — same pipeline.
- **digest** is the only narrative output: synthesizes today's wiki
  additions into `wiki/digests/YYYY-MM-DD.md` in the user's voice.

Failures short-circuit at the earlier stage's `exit 1`, so downstream
stages never run on bad upstream output. If you're touching this
chain, preserve the `exec`-based handoff — nested process stacks would
break the launchd PID tracking.

## On-demand skills

Not every module runs on a schedule. `query` is invoked ad-hoc by
agents that need to look things up in the wiki. It reads `wiki/topics/`
only (read-only), rebuilds its BM25 index on every call, and returns
ranked JSON. Obsidian remains the human-facing search/browse surface;
query is specifically for programmatic callers.

## Knowledge base patterns

Follow the llm-wiki pattern:
- **Ingest** — when new sources arrive, extract structured knowledge
  into wiki pages (automated by the `ingest` module on the daily chain)
- **Query** — when answering questions, prefer the wiki, and file new
  discoveries back as wiki pages so future queries benefit
- **Lint** — periodically check for contradictions, stale claims, and
  orphan pages

Always update `wiki/index.md` and `wiki/log.md` when modifying wiki pages.

## Wiki formatting (Obsidian-compatible)

- Use `[[wikilinks]]` for all cross-references between wiki pages
- Link format: `[[page-name]]` or `[[page-name|display text]]`
- Use `[[topics/trading]]` with folder path for cross-directory links
- Add `#tags` at the top of pages for Obsidian tag navigation
- Keep filenames lowercase-kebab-case (e.g., `quantitative-trading.md`)

## Principles

- Read `persona/character_sheet.md` before any task that involves
  judgment, framing, or voice
- Reason as the user would, not as a generic assistant
- Write all output as markdown to the appropriate `wiki/` or module
  directory; never scatter ad-hoc files in the project root
- Never modify or delete files in `sources/`. The radar module is the
  only writer permitted to *add* files, and only under
  `sources/corpus/reading/radar/{category}/`
- Log autonomous actions to `wiki/log.md` so the user can audit what
  ran while they were away
- When uncertain about a decision the user would make, flag it rather
  than guess

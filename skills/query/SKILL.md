# Query Module

Retrieval primitive over the wiki. Given a natural-language query,
returns up to `k` ranked wiki topic pages by BM25 lexical relevance.
Read-only. Agent-facing — human browsing uses Obsidian directly.

## Trigger

On-demand. Invoked by the agent (or downstream module) when looking up
prior knowledge in the wiki before writing, deciding, or synthesizing.
Not part of the scheduled radar → ingest → digest chain.

## Inputs

- `query` (positional string) — free-form query; works best with
  concrete topical keywords (e.g. `"kelly criterion"`, `"agent
  autonomy"`). Stopwords are dropped automatically.
- `--k` (int, default 10) — maximum number of results.
- `--topics-dir` (path, default `wiki/topics`) — directory to index.
- `--format` (`json` | `text`, default `json`) — output format.

## Process

1. Glob `wiki/topics/*.md` and build an in-memory BM25 index.
2. Tokenize the query (lowercase, word-split, drop stopwords, drop
   tokens < 2 chars; no stemming).
3. Score every page, return top `k` with positive scores.
4. For each hit, generate a ±200-char snippet centered on the earliest
   matching query token.
5. Emit JSON (default) or plain text to stdout.

No state is persisted. The index is rebuilt on every call — sub-100ms
at current corpus sizes — which means manual Obsidian edits to wiki
pages are reflected immediately, with no reindex step.

## Invocation

```bash
python -m skills.query.query "query text" --k 10
```

From an agent session, run via Bash and parse the JSON output.

## Output schema (JSON)

```json
{
  "query": "kelly criterion",
  "results": [
    {
      "slug": "kelly-criterion",
      "path": "wiki/topics/kelly-criterion.md",
      "score": 12.4821,
      "snippet": "…optimal bet sizing for compounding edge…"
    }
  ]
}
```

Empty `results` indicates no page scored above zero. Agents should
treat that as "try different keywords" rather than "no information
exists" — lexical search is brittle to vocabulary mismatch.

## Agentic search pattern

Because v0.2 is lexical-only, recall depends on the agent picking
keywords the wiki actually uses. When a query returns empty or
low-quality results, iterate:

1. Try synonyms (`"optimal betting"` → `"bet sizing"` → `"position sizing"`).
2. Try expanding acronyms (`"LLM"` → `"large language model"`).
3. Try narrower and broader terms.
4. Cross-check with Obsidian's tag/search when debugging.

If iteration repeatedly fails to surface pages you know exist, that
is the signal to add semantic search (deferred from v0.2).

## Dependencies

Install with `pip install -r skills/query/requirements.txt`. Runtime
requires `rank_bm25`; `pytest` is dev-only.

## Out of scope (v0.2)

- Embedding / semantic search (add when lexical recall demonstrably fails)
- Persistent index (ephemeral rebuild is fast enough at current scale)
- Wiki writes or logging of queries (strictly read-only)
- Graph traversal over [[wikilinks]]
- Section-level retrieval (pages are the unit; agent reads full page on hit)
- BM25+dense hybrid ranking, LLM re-ranking

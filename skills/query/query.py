"""Query skill: BM25 retrieval primitive over wiki/topics/."""

from __future__ import annotations

import argparse
import json as _json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing",
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him",
    "his", "she", "her", "it", "its", "they", "them", "their",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "and", "but", "or", "if", "because", "as", "until", "while",
    "of", "at", "by", "for", "with", "about", "against", "between",
    "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "in", "out", "on", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "can", "will", "just", "should", "now",
})

_TOKEN_RE = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    """Lowercase, word-split, drop stopwords and <2-char tokens."""
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if len(t) >= 2 and t not in _STOPWORDS]


def iter_wiki_docs(topics_dir: Path) -> Iterator[tuple[str, str]]:
    """Yield (slug, raw_markdown) for every .md file under topics_dir."""
    if not topics_dir.is_dir():
        return
    for path in sorted(topics_dir.glob("*.md")):
        yield path.stem, path.read_text(encoding="utf-8")


@dataclass
class Index:
    """Ephemeral BM25 index over wiki topic pages."""

    bm25: BM25Okapi | None
    docs: list[tuple[str, str]]  # (slug, raw_markdown), ordered to match bm25 doc ids


def build_index(topics_dir: Path) -> Index:
    """Load all pages under topics_dir and build an in-memory BM25 index."""
    docs = list(iter_wiki_docs(topics_dir))
    if not docs:
        return Index(bm25=None, docs=[])
    tokenized = [tokenize(text) for _, text in docs]
    return Index(bm25=BM25Okapi(tokenized), docs=docs)


def search(index: Index, query: str, k: int = 10) -> list[dict[str, Any]]:
    """Return up to k ranked results with non-zero scores."""
    if index.bm25 is None:
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    scores = index.bm25.get_scores(query_tokens)
    ranked = sorted(
        ((i, float(s)) for i, s in enumerate(scores) if s > 0),
        key=lambda x: -x[1],
    )[:k]
    return [
        {"slug": index.docs[i][0], "score": score, "text": index.docs[i][1]}
        for i, score in ranked
    ]


def make_snippet(
    text: str, query_tokens: list[str], window: int = 200
) -> str:
    """Return a window of text centered on the earliest matching query token."""
    low = text.lower()
    positions = [low.find(tok) for tok in query_tokens]
    positions = [p for p in positions if p >= 0]
    if not positions:
        body = text[:window].strip().replace("\n", " ")
        return " ".join(body.split())
    center = min(positions)
    half = window // 2
    start = max(0, center - half)
    end = min(len(text), start + window)
    body = text[start:end].strip().replace("\n", " ")
    body = " ".join(body.split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{body}{suffix}"


def _format_result(
    hit: dict[str, Any], topics_dir: Path, query_tokens: list[str]
) -> dict[str, Any]:
    """Format a search hit for CLI output (excludes raw text field)."""
    return {
        "slug": hit["slug"],
        "path": str(topics_dir / f"{hit['slug']}.md"),
        "score": round(hit["score"], 4),
        "snippet": make_snippet(hit["text"], query_tokens),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for BM25 query."""
    parser = argparse.ArgumentParser(
        prog="skills.query.query",
        description="BM25 retrieval over wiki/topics/ for agent callers.",
    )
    parser.add_argument("query", help="Natural-language query string.")
    parser.add_argument("--k", type=int, default=10, help="Max results to return.")
    parser.add_argument(
        "--topics-dir",
        type=Path,
        default=Path("wiki/topics"),
        help="Directory of wiki topic markdown files.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output format.",
    )
    args = parser.parse_args(argv)

    index = build_index(args.topics_dir)
    raw_hits = search(index, args.query, k=args.k)
    query_tokens = tokenize(args.query)
    results = [_format_result(h, args.topics_dir, query_tokens) for h in raw_hits]

    if args.format == "json":
        _json.dump({"query": args.query, "results": results}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        if not results:
            print(f"No matches for: {args.query}")
        for r in results:
            print(f"{r['score']:>8.4f}  {r['slug']}")
            print(f"          {r['snippet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

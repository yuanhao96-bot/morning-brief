from pathlib import Path

from skills.query.query import iter_wiki_docs, tokenize


def test_tokenize_lowercases_and_splits_on_word_boundaries():
    assert tokenize("Kelly Criterion") == ["kelly", "criterion"]


def test_tokenize_drops_english_stopwords():
    assert tokenize("the kelly criterion is a formula") == ["kelly", "criterion", "formula"]


def test_tokenize_drops_tokens_shorter_than_two_chars():
    assert tokenize("a b cd ef") == ["cd", "ef"]


def test_tokenize_handles_punctuation_and_markdown():
    # `[[wikilinks]]` and punctuation should be stripped by \w+ split
    assert tokenize("See [[kelly-criterion]]!") == ["see", "kelly", "criterion"]


def test_tokenize_handles_empty_and_stopword_only_input():
    assert tokenize("") == []
    assert tokenize("the a an is") == []


def test_iter_wiki_docs_reads_all_markdown_files(tmp_path: Path):
    topics = tmp_path / "wiki" / "topics"
    topics.mkdir(parents=True)
    (topics / "kelly-criterion.md").write_text("# Kelly Criterion\n\nOptimal bet sizing.")
    (topics / "mean-reversion.md").write_text("# Mean Reversion\n\nPrices revert.")

    docs = list(iter_wiki_docs(tmp_path / "wiki" / "topics"))
    slugs = sorted(slug for slug, _ in docs)
    assert slugs == ["kelly-criterion", "mean-reversion"]


def test_iter_wiki_docs_ignores_non_markdown(tmp_path: Path):
    topics = tmp_path / "wiki" / "topics"
    topics.mkdir(parents=True)
    (topics / "real.md").write_text("# Real")
    (topics / "ignored.txt").write_text("not markdown")

    docs = list(iter_wiki_docs(topics))
    assert [slug for slug, _ in docs] == ["real"]


def test_iter_wiki_docs_returns_empty_for_missing_dir(tmp_path: Path):
    docs = list(iter_wiki_docs(tmp_path / "does-not-exist"))
    assert docs == []


from skills.query.query import build_index, search


def _write_corpus(root: Path, pages: dict[str, str]) -> Path:
    topics = root / "wiki" / "topics"
    topics.mkdir(parents=True)
    for slug, text in pages.items():
        (topics / f"{slug}.md").write_text(text)
    return topics


def test_search_ranks_exact_topic_match_highest(tmp_path: Path):
    topics = _write_corpus(tmp_path, {
        "kelly-criterion": "# Kelly Criterion\n\nOptimal bet sizing formula for compounding edge.",
        "mean-reversion": "# Mean Reversion\n\nPrices revert to the mean over time.",
        "drawdown-control": "# Drawdown Control\n\nLimit losses to preserve capital.",
    })
    index = build_index(topics)
    results = search(index, "kelly criterion", k=3)
    assert results[0]["slug"] == "kelly-criterion"
    assert results[0]["score"] > 0


def test_search_returns_empty_for_no_match(tmp_path: Path):
    topics = _write_corpus(tmp_path, {"a": "alpha", "b": "beta"})
    index = build_index(topics)
    assert search(index, "unrelated", k=5) == []


def test_search_returns_empty_for_stopword_only_query(tmp_path: Path):
    topics = _write_corpus(tmp_path, {"a": "alpha content"})
    index = build_index(topics)
    assert search(index, "the and or", k=5) == []


def test_search_respects_k_limit(tmp_path: Path):
    topics = _write_corpus(tmp_path, {
        "trading-fundamentals": "# Trading Fundamentals\n\nCore trading concepts and definitions.",
        "kelly-trading": "# Kelly Criterion for Trading\n\nApply kelly to trading position sizing.",
        "mean-reversion-trading": "# Mean Reversion Trading Strategy\n\nTrade mean reversion patterns.",
        "risk-management": "# Risk Management\n\nUnrelated content about risk.",
        "diversification": "# Portfolio Diversification\n\nUnrelated content about diversification.",
    })
    index = build_index(topics)
    results = search(index, "trading strategy", k=2)
    assert len(results) == 2


def test_build_index_on_empty_dir_returns_empty_index(tmp_path: Path):
    topics = tmp_path / "empty"
    topics.mkdir()
    index = build_index(topics)
    assert search(index, "anything", k=5) == []


from skills.query.query import make_snippet


def test_make_snippet_centers_on_first_matching_token():
    text = "A" * 200 + " kelly criterion is optimal " + "B" * 200
    snippet = make_snippet(text, ["kelly"], window=100)
    assert "kelly criterion" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")


def test_make_snippet_no_leading_ellipsis_when_match_at_start():
    text = "kelly criterion rules everything"
    snippet = make_snippet(text, ["kelly"], window=100)
    assert not snippet.startswith("…")
    assert "kelly criterion" in snippet


def test_make_snippet_collapses_newlines():
    text = "intro\n\nkelly criterion\n\nmore"
    snippet = make_snippet(text, ["kelly"], window=100)
    assert "\n" not in snippet


def test_make_snippet_falls_back_to_head_when_no_match():
    text = "alpha beta gamma"
    snippet = make_snippet(text, ["nomatch"], window=100)
    assert "alpha" in snippet


import json
import subprocess
import sys


def test_cli_returns_json_with_ranked_results(tmp_path: Path):
    topics = _write_corpus(tmp_path, {
        "kelly-criterion": "# Kelly Criterion\n\nOptimal bet sizing for compounding edge.",
        "mean-reversion": "# Mean Reversion\n\nPrices revert to the mean.",
        "drawdown-control": "# Drawdown Control\n\nLimit losses to preserve capital.",
    })
    result = subprocess.run(
        [
            sys.executable, "-m", "skills.query.query",
            "kelly criterion",
            "--topics-dir", str(topics),
            "--k", "5",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["query"] == "kelly criterion"
    assert payload["results"][0]["slug"] == "kelly-criterion"
    assert "snippet" in payload["results"][0]
    assert "score" in payload["results"][0]
    assert "path" in payload["results"][0]
    # CLI output must not leak raw page text
    assert "text" not in payload["results"][0]


def test_cli_empty_results_on_no_match(tmp_path: Path):
    topics = _write_corpus(tmp_path, {"a": "alpha"})
    result = subprocess.run(
        [
            sys.executable, "-m", "skills.query.query",
            "unrelated",
            "--topics-dir", str(topics),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["results"] == []

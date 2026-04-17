import sqlite3
import pytest
from pathlib import Path
from state_db import init_db, get_meta, set_meta, SCHEMA_VERSION


def test_init_db_creates_tables(tmp_path):
    db = init_db(tmp_path / "t.db")
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert tables == {"books", "files", "meta"}


def test_foreign_key_enforced(tmp_path):
    db = init_db(tmp_path / "t.db")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO files (path, book_slug, sha256) "
                   "VALUES ('a', 'missing', 'deadbeef')")
        db.commit()


def test_status_check_constraint(tmp_path):
    db = init_db(tmp_path / "t.db")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO books (slug, status) VALUES ('x', 'bogus')")
        db.commit()


def test_schema_version_stamped_on_first_init(tmp_path):
    db = init_db(tmp_path / "t.db")
    assert get_meta(db, "schema_version") == SCHEMA_VERSION


def test_meta_roundtrip(tmp_path):
    db = init_db(tmp_path / "t.db")
    set_meta(db, "k", "v1"); set_meta(db, "k", "v2")
    assert get_meta(db, "k") == "v2"
    assert get_meta(db, "missing") is None


from state_db import upsert_book, replace_files_for_book


def test_upsert_book_insert_then_update(tmp_path):
    db = init_db(tmp_path / "t.db")
    upsert_book(db, "foo", title="First", status="pending")
    upsert_book(db, "foo", title="Second", status="extracted")
    row = db.execute("SELECT title, status FROM books WHERE slug='foo'").fetchone()
    assert row == ("Second", "extracted")


def test_upsert_book_rejects_unknown_field(tmp_path):
    db = init_db(tmp_path / "t.db")
    with pytest.raises(ValueError):
        upsert_book(db, "foo", title="t", bogus_field="x")


def test_replace_files_removes_stale_within_book(tmp_path):
    """Regression: adversarial review flagged that upsert-only semantics
    left stale rows when a book's source_files shrunk. Replacement must
    delete the dropped file."""
    db = init_db(tmp_path / "t.db")
    upsert_book(db, "x", status="pending")
    replace_files_for_book(db, "x", [("a.md", "h1", 10), ("b.md", "h2", 20)])
    replace_files_for_book(db, "x", [("a.md", "h1", 10)])  # b.md dropped
    rows = [r[0] for r in db.execute(
        "SELECT path FROM files WHERE book_slug='x'").fetchall()]
    assert rows == ["a.md"]


def test_replace_files_rejects_cross_book_collision(tmp_path):
    """Regression: adversarial review round 4 flagged that silent
    cross-book reassignment hid duplicate-ownership bugs. Claiming a
    path owned by another book must now fail loudly — the caller must
    first release the path from the prior owner."""
    db = init_db(tmp_path / "t.db")
    upsert_book(db, "a", status="pending")
    upsert_book(db, "b", status="pending")
    replace_files_for_book(db, "a", [("p/x.md", "h1", 10)])
    with pytest.raises(sqlite3.IntegrityError):
        replace_files_for_book(db, "b", [("p/x.md", "h2", 12)])
    # a still owns the path; b owns nothing
    assert db.execute(
        "SELECT book_slug FROM files WHERE path='p/x.md'").fetchone()[0] == "a"
    assert db.execute(
        "SELECT COUNT(*) FROM files WHERE book_slug='b'").fetchone()[0] == 0


def test_replace_files_transfers_via_explicit_release(tmp_path):
    """Intentional cross-book moves must go through the previous owner
    giving up the path first."""
    db = init_db(tmp_path / "t.db")
    upsert_book(db, "a", status="pending")
    upsert_book(db, "b", status="pending")
    replace_files_for_book(db, "a", [("p/x.md", "h1", 10)])
    # release: a's new file set omits x.md
    replace_files_for_book(db, "a", [])
    # now b can claim it
    replace_files_for_book(db, "b", [("p/x.md", "h2", 12)])
    assert db.execute(
        "SELECT book_slug FROM files WHERE path='p/x.md'").fetchone()[0] == "b"


def test_replace_files_empty_wipes_book(tmp_path):
    db = init_db(tmp_path / "t.db")
    upsert_book(db, "x", status="pending")
    replace_files_for_book(db, "x", [("a.md", "h1", 10)])
    replace_files_for_book(db, "x", [])
    assert db.execute(
        "SELECT COUNT(*) FROM files WHERE book_slug='x'").fetchone()[0] == 0


def test_replace_files_is_transactional(tmp_path, monkeypatch):
    """If the INSERT raises mid-batch, the DELETE must roll back too."""
    db = init_db(tmp_path / "t.db")
    upsert_book(db, "x", status="pending")
    replace_files_for_book(db, "x", [("a.md", "h1", 10), ("b.md", "h2", 20)])
    # Trigger failure by passing a record with a non-existent book_slug via
    # a malformed second call? Easiest: violate NOT NULL on sha256.
    with pytest.raises(sqlite3.IntegrityError):
        replace_files_for_book(db, "x",
            [("c.md", "h3", 30), ("d.md", None, 40)])  # None sha256 fails NOT NULL
    # Original rows must still be present — rollback preserved prior state.
    rows = sorted(r[0] for r in db.execute(
        "SELECT path FROM files WHERE book_slug='x'").fetchall())
    assert rows == ["a.md", "b.md"]


from state_db import unprocessed_or_changed, books_merged_on, files_for_book


def test_unprocessed_empty_db_returns_all(tmp_path):
    db = init_db(tmp_path / "t.db")
    assert unprocessed_or_changed(db, [("a.md", "h1"), ("b.md", "h2")]) \
        == ["a.md", "b.md"]


def test_unprocessed_detects_hash_change(tmp_path):
    db = init_db(tmp_path / "t.db")
    upsert_book(db, "x", status="merged")
    replace_files_for_book(db, "x", [("a.md", "old", 10)])
    assert unprocessed_or_changed(db, [("a.md", "new")]) == ["a.md"]
    assert unprocessed_or_changed(db, [("a.md", "old")]) == []


def test_books_merged_on_filters_by_date(tmp_path):
    db = init_db(tmp_path / "t.db")
    upsert_book(db, "a", status="merged",
                merged_at="2026-04-10T12:00:00Z")
    upsert_book(db, "b", status="merged",
                merged_at="2026-04-11T12:00:00Z")
    result = books_merged_on(db, "2026-04-10")
    assert [r["slug"] for r in result] == ["a"]


from state_db import (rebuild_from_yamls, compute_yaml_fingerprint,
                      get_meta)
import yaml as _yaml
import os
import time


def _write_fixtures(root):
    (root / "extracts/ingest/state").mkdir(parents=True, exist_ok=True)
    (root / "extracts/ingest/state/foo.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "foo", "title": "F", "status": "merged",
                 "merged_at": "2026-04-10T00:00:00Z", "concept_count": 3}}))
    (root / "extracts/ingest/foo.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "foo"},
        "source_files": [{"path": "a.md", "sha256": "h1", "size": 10}]}))


def test_rebuild_from_yamls(tmp_path):
    _write_fixtures(tmp_path)
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    counts = rebuild_from_yamls(db, tmp_path)
    assert counts == {"books": 1, "files": 1}
    assert db.execute("SELECT title FROM books WHERE slug='foo'").fetchone()[0] == "F"
    assert db.execute("SELECT book_slug FROM files WHERE path='a.md'").fetchone()[0] == "foo"


def test_rebuild_is_idempotent(tmp_path):
    _write_fixtures(tmp_path)
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    rebuild_from_yamls(db, tmp_path)
    rebuild_from_yamls(db, tmp_path)
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


def test_rebuild_writes_fingerprint(tmp_path):
    _write_fixtures(tmp_path)
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    rebuild_from_yamls(db, tmp_path)
    stored = get_meta(db, "yaml_fingerprint")
    assert stored == compute_yaml_fingerprint(tmp_path)


def test_fingerprint_changes_on_yaml_edit(tmp_path):
    _write_fixtures(tmp_path)
    fp_before = compute_yaml_fingerprint(tmp_path)
    time.sleep(0.01)  # ensure mtime_ns advances
    p = tmp_path / "extracts/ingest/foo.yaml"
    p.write_text(p.read_text() + "\n# touched\n")
    assert compute_yaml_fingerprint(tmp_path) != fp_before


def test_fingerprint_stable_across_reads(tmp_path):
    _write_fixtures(tmp_path)
    fp1 = compute_yaml_fingerprint(tmp_path)
    fp2 = compute_yaml_fingerprint(tmp_path)
    assert fp1 == fp2


def test_rebuild_fails_hard_on_missing_extract_for_merged_book(tmp_path):
    """Regression: adversarial review round 2 flagged that rebuild
    silently dropped file ownership when a 'merged' book lost its
    extract file. Must refuse to continue."""
    from state_db import RebuildError
    (tmp_path / "extracts/ingest/state").mkdir(parents=True)
    (tmp_path / "extracts/ingest/state/foo.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "foo", "title": "F", "status": "merged",
                 "merged_at": "2026-04-10T00:00:00Z"}}))
    # note: no extracts/ingest/foo.yaml on disk
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    with pytest.raises(RebuildError, match="foo.yaml is missing"):
        rebuild_from_yamls(db, tmp_path)


def test_rebuild_fails_hard_on_missing_source_files_key(tmp_path):
    from state_db import RebuildError
    (tmp_path / "extracts/ingest/state").mkdir(parents=True)
    (tmp_path / "extracts/ingest/state/foo.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "foo", "title": "F", "status": "extracted",
                 "extracted_at": "2026-04-10T00:00:00Z"}}))
    (tmp_path / "extracts/ingest/foo.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "foo"}, "concepts": []}))  # no source_files key
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    with pytest.raises(RebuildError, match="source_files key is absent"):
        rebuild_from_yamls(db, tmp_path)


def test_rebuild_tolerates_pending_book_without_extract(tmp_path):
    """pending/extracting books legitimately have no source_files yet."""
    (tmp_path / "extracts/ingest/state").mkdir(parents=True)
    (tmp_path / "extracts/ingest/state/foo.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "foo", "title": "F", "status": "pending"}}))
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    counts = rebuild_from_yamls(db, tmp_path)  # no raise
    assert counts == {"books": 1, "files": 0}


def test_rebuild_accepts_empty_source_files_for_merged(tmp_path):
    """A merged book with zero source files (metadata-only) is legitimate,
    as long as the source_files key is present and explicit."""
    (tmp_path / "extracts/ingest/state").mkdir(parents=True)
    (tmp_path / "extracts/ingest/state/foo.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "foo", "status": "merged",
                 "merged_at": "2026-04-10T00:00:00Z"}}))
    (tmp_path / "extracts/ingest/foo.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "foo"}, "source_files": []}))
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    counts = rebuild_from_yamls(db, tmp_path)
    assert counts == {"books": 1, "files": 0}


def test_rebuild_preserves_old_cache_on_validation_failure(tmp_path):
    """Regression: adversarial review round 3 flagged that rebuild
    used to delete the existing cache before hitting a late validation
    error. Validation must happen first; the old cache must survive."""
    from state_db import RebuildError

    # Bootstrap a good cache.
    (tmp_path / "extracts/ingest/state").mkdir(parents=True)
    (tmp_path / "extracts/ingest/state/good.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "good", "status": "pending"}}))
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    rebuild_from_yamls(db, tmp_path)
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1

    # Introduce a broken second book (claims merged, extract missing).
    (tmp_path / "extracts/ingest/state/broken.yaml").write_text(_yaml.safe_dump({
        "book": {"slug": "broken", "status": "merged",
                 "merged_at": "2026-04-10T00:00:00Z"}}))
    with pytest.raises(RebuildError):
        rebuild_from_yamls(db, tmp_path)

    # Old cache (the good book) must still be present; rebuild must
    # have aborted before the DELETE.
    assert db.execute("SELECT slug FROM books").fetchone() == ("good",)


def test_rebuild_rejects_duplicate_paths_across_books(tmp_path):
    """Regression: adversarial review round 4 flagged silent cross-book
    path ownership. If two YAMLs claim the same path, rebuild must
    surface the collision — not pick a winner."""
    from state_db import RebuildError
    (tmp_path / "extracts/ingest/state").mkdir(parents=True)
    for slug in ("a", "b"):
        (tmp_path / f"extracts/ingest/state/{slug}.yaml").write_text(_yaml.safe_dump({
            "book": {"slug": slug, "status": "merged",
                     "merged_at": "2026-04-10T00:00:00Z"}}))
        (tmp_path / f"extracts/ingest/{slug}.yaml").write_text(_yaml.safe_dump({
            "book": {"slug": slug},
            "source_files": [
                {"path": "p/shared.md", "sha256": "h", "size": 10}]}))
    db = init_db(tmp_path / "extracts/ingest/.cache/state.db")
    with pytest.raises(RebuildError, match="claimed by both"):
        rebuild_from_yamls(db, tmp_path)


from state_db import (write_book_state_yaml,
                      write_source_files_into_extract)


def test_write_book_state_yaml_creates_and_merges(tmp_path):
    # first write creates the file
    write_book_state_yaml(tmp_path, "foo",
                           {"title": "T", "status": "pending"})
    p = tmp_path / "extracts/ingest/state/foo.yaml"
    assert p.exists()
    doc = _yaml.safe_load(p.read_text())
    assert doc["book"]["title"] == "T"
    assert doc["book"]["status"] == "pending"

    # second write merges (status updated, title preserved)
    write_book_state_yaml(tmp_path, "foo", {"status": "extracted"})
    doc = _yaml.safe_load(p.read_text())
    assert doc["book"]["title"] == "T"
    assert doc["book"]["status"] == "extracted"


def test_write_source_files_refuses_when_extract_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        write_source_files_into_extract(tmp_path, "foo", [])


def test_write_source_files_preserves_other_keys(tmp_path):
    path = tmp_path / "extracts/ingest/foo.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(_yaml.safe_dump({
        "book": {"slug": "foo", "title": "T"},
        "concepts": [{"id": "x"}]}))
    write_source_files_into_extract(tmp_path, "foo",
        [{"path": "a.md", "sha256": "h", "size": 10}])
    doc = _yaml.safe_load(path.read_text())
    assert doc["book"]["title"] == "T"
    assert doc["concepts"] == [{"id": "x"}]
    assert doc["source_files"] == [
        {"path": "a.md", "sha256": "h", "size": 10}]


def test_read_book_status_returns_none_for_missing_shard(tmp_path):
    from state_db import read_book_status
    assert read_book_status(tmp_path, "nonexistent") is None


def test_read_book_status_returns_yaml_status(tmp_path):
    from state_db import read_book_status
    write_book_state_yaml(tmp_path, "foo", {"status": "extracted"})
    assert read_book_status(tmp_path, "foo") == "extracted"

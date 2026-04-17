"""State database for the ingest skill.

Source of truth is plaintext YAML:
  - extracts/ingest/state/{slug}.yaml   → book metadata
  - extracts/ingest/{slug}.yaml         → concept extract + source_files

This module provides a SQLite-backed index for fast corpus→book
membership lookups and date-range queries. The DB at
extracts/ingest/.cache/state.db is derived and gitignored; if lost,
rebuild_from_yamls() reconstructs it from the canonical YAMLs.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    slug          TEXT PRIMARY KEY,
    title         TEXT,
    author        TEXT,
    domain        TEXT,
    source_path   TEXT,
    status        TEXT NOT NULL CHECK(status IN
                      ('pending','extracting','extracted','merged')),
    extracted_at  TEXT,
    merged_at     TEXT,
    concept_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS files (
    path      TEXT PRIMARY KEY,
    book_slug TEXT NOT NULL REFERENCES books(slug) ON DELETE CASCADE,
    sha256    TEXT NOT NULL,
    size      INTEGER
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_book   ON files(book_slug);
CREATE INDEX IF NOT EXISTS idx_books_merged ON books(merged_at);
CREATE INDEX IF NOT EXISTS idx_books_status ON books(status);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    # schema drift guard — if an older DB hangs around after an incompatible
    # schema change, wipe data tables and let fingerprint-based rebuild repopulate.
    existing = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if existing is None:
        conn.execute("INSERT INTO meta (key, value) VALUES "
                     "('schema_version', ?)", (SCHEMA_VERSION,))
    elif existing[0] != SCHEMA_VERSION:
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM books")
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'",
                     (SCHEMA_VERSION,))
        conn.execute("DELETE FROM meta WHERE key='yaml_fingerprint'")
    conn.commit()
    return conn


def get_meta(conn, key: str) -> str | None:
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value))
    conn.commit()


_BOOK_FIELDS = {'title', 'author', 'domain', 'source_path', 'status',
                'extracted_at', 'merged_at', 'concept_count'}


def upsert_book(conn, slug: str, **fields) -> None:
    unknown = set(fields) - _BOOK_FIELDS
    if unknown:
        raise ValueError(f"unknown book fields: {unknown}")
    fields.setdefault('status', 'pending')
    cols = ['slug'] + list(fields)
    placeholders = ','.join('?' * len(cols))
    updates = ','.join(f"{c}=excluded.{c}" for c in fields)
    conn.execute(
        f"INSERT INTO books ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(slug) DO UPDATE SET {updates}",
        [slug, *fields.values()])
    conn.commit()


def replace_files_for_book(conn, book_slug: str,
                           records: list[tuple[str, str, int | None]]) -> None:
    """records = [(path, sha256, size), ...]

    Atomically replaces the entire file set owned by `book_slug`:
    removes every row previously attributed to this book, then inserts
    the new set. Paths currently owned by a DIFFERENT book raise
    sqlite3.IntegrityError — the adversarial review round 4 flagged
    that silent cross-book transfer (via ON CONFLICT) enabled duplicate
    ownership bugs to pass unnoticed. Transfers must be explicit: the
    previous owner's file set has to be rewritten to release the path
    BEFORE this call claims it.

    Callers must pass the COMPLETE file set for the book. There is no
    API for incremental add/remove by design — partial updates are the
    class of bug this function was rewritten to eliminate.
    """
    with conn:  # context manager commits on success, rolls back on raise
        conn.execute("DELETE FROM files WHERE book_slug = ?", (book_slug,))
        if records:
            conn.executemany(
                "INSERT INTO files (path, book_slug, sha256, size) "
                "VALUES (?,?,?,?)",  # no ON CONFLICT — collisions are errors
                [(p, book_slug, h, s) for p, h, s in records])


def unprocessed_or_changed(conn,
                           corpus: list[tuple[str, str]]) -> list[str]:
    """Returns corpus paths that are absent from `files` or whose
    sha256 differs from what the DB has recorded.
    """
    conn.execute(
        "CREATE TEMP TABLE corpus_current "
        "(path TEXT PRIMARY KEY, sha256 TEXT)")
    try:
        conn.executemany(
            "INSERT INTO corpus_current VALUES (?, ?)", corpus)
        rows = conn.execute("""
            SELECT c.path FROM corpus_current c
            LEFT JOIN files f ON f.path = c.path
            WHERE f.path IS NULL OR f.sha256 != c.sha256
            ORDER BY c.path
        """).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.execute("DROP TABLE corpus_current")


def books_merged_on(conn, date_iso: str) -> list[dict]:
    rows = conn.execute(
        "SELECT slug, title, domain, concept_count FROM books "
        "WHERE DATE(merged_at) = DATE(?) ORDER BY slug",
        (date_iso,)).fetchall()
    return [{'slug': r[0], 'title': r[1], 'domain': r[2],
             'concept_count': r[3]} for r in rows]


def files_for_book(conn, slug: str) -> list[dict]:
    rows = conn.execute(
        "SELECT path, sha256, size FROM files "
        "WHERE book_slug=? ORDER BY path",
        (slug,)).fetchall()
    return [{'path': r[0], 'sha256': r[1], 'size': r[2]} for r in rows]


import hashlib
import yaml


def compute_yaml_fingerprint(project_root: Path) -> str:
    """Deterministic fingerprint of canonical YAML inputs.

    Hashes sorted (relpath, size, mtime_ns) tuples for every state
    YAML and its matching extract YAML. mtime-based so it is cheap
    to recompute on every CLI invocation and naturally detects
    worktree switches (git checkout rewrites mtimes when file
    content changes), YAML edits, and file additions/removals.
    """
    state_dir = project_root / "extracts/ingest/state"
    extract_dir = project_root / "extracts/ingest"
    entries = []
    if state_dir.exists():
        for p in sorted(state_dir.glob("*.yaml")):
            st = p.stat()
            entries.append((str(p.relative_to(project_root)),
                            st.st_size, st.st_mtime_ns))
            ep = extract_dir / p.name
            if ep.exists():
                est = ep.stat()
                entries.append((str(ep.relative_to(project_root)),
                                est.st_size, est.st_mtime_ns))
    h = hashlib.sha256()
    for relpath, size, mtime_ns in entries:
        h.update(f"{relpath}|{size}|{mtime_ns}\n".encode())
    return h.hexdigest()


_REQUIRES_FILES = {"extracted", "merged"}


class RebuildError(RuntimeError):
    """Canonical YAML inputs are missing or incomplete — refuse to
    silently continue, and refuse to wipe the cache in the process."""


def _validate_yamls(project_root: Path):
    """Read and validate all canonical YAMLs into memory.

    Returns (books, files_by_slug) on success, or raises RebuildError
    with every issue concatenated. Performs zero DB mutations.
    """
    state_dir = project_root / "extracts/ingest/state"
    extract_dir = project_root / "extracts/ingest"
    errors: list[str] = []
    books: list[tuple[str, dict]] = []
    files_by_slug: dict[str, list[tuple]] = {}

    for yf in sorted(state_dir.glob("*.yaml")):
        data = yaml.safe_load(yf.read_text()) or {}
        b = data.get("book", {})
        slug = b.get("slug")
        if not slug:
            errors.append(f"{yf.name}: missing book.slug")
            continue

        status = b.get("status", "pending")
        books.append((slug, {
            "title": b.get("title"), "author": b.get("author"),
            "domain": b.get("domain"),
            "source_path": b.get("source_path"),
            "status": status,
            "extracted_at": b.get("extracted_at"),
            "merged_at": b.get("merged_at"),
            "concept_count": b.get("concept_count", 0),
        }))

        extract_path = extract_dir / f"{slug}.yaml"
        extract = None
        if extract_path.exists():
            extract = yaml.safe_load(extract_path.read_text()) or {}
        source_files = (extract or {}).get("source_files")

        if status in _REQUIRES_FILES:
            if extract is None:
                errors.append(
                    f"{slug}: status={status} but {extract_path.name} is missing")
                continue
            if source_files is None:
                errors.append(
                    f"{slug}: status={status} but source_files key is absent in "
                    f"{extract_path.name}")
                continue

        if source_files:
            files_by_slug[slug] = [
                (f["path"], f["sha256"], f.get("size"))
                for f in source_files]

    # Global uniqueness check — the files table has PRIMARY KEY(path) and
    # the writer rejects cross-book collisions, but the YAMLs could still
    # claim the same path from two books (e.g. bad manual edit, merge
    # conflict that kept both sides). Catch it here, before we wipe the
    # cache, so the operator fixes the YAML instead of debugging from a
    # silently-misattributed DB.
    path_owners: dict[str, str] = {}
    for slug, records in files_by_slug.items():
        for path, _sha, _size in records:
            if path in path_owners and path_owners[path] != slug:
                errors.append(
                    f"path '{path}' claimed by both '{path_owners[path]}' "
                    f"and '{slug}'")
            else:
                path_owners[path] = slug

    if errors:
        msg = "Rebuild aborted — canonical inputs incomplete:\n" + \
              "\n".join(f"  - {e}" for e in errors)
        raise RebuildError(msg)
    return books, files_by_slug


def rebuild_from_yamls(conn, project_root: Path) -> dict:
    # Phase A: read and validate. No DB writes. Raises on any error,
    # which leaves the existing cache untouched.
    books, files_by_slug = _validate_yamls(project_root)

    # Phase B: repopulate. Validation was clean, so no helper below
    # will raise for canonical reasons. A process crash in this window
    # is self-healing: the fingerprint stays stale and the next
    # rebuild succeeds.
    conn.execute("DELETE FROM files")
    conn.execute("DELETE FROM books")
    conn.commit()

    file_count = 0
    for slug, fields in books:
        upsert_book(conn, slug, **fields)
    for slug, records in files_by_slug.items():
        replace_files_for_book(conn, slug, records)
        file_count += len(records)

    set_meta(conn, "yaml_fingerprint", compute_yaml_fingerprint(project_root))
    return {"books": len(books), "files": file_count}


import fcntl
import os
import tempfile
from contextlib import contextmanager


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text to `path` via temp file + os.replace so interrupted
    writes cannot leave a half-written file on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@contextmanager
def _slug_lock(project_root: Path, slug: str):
    """Serialize writers touching the same book's canonical YAML.

    fcntl.flock on extracts/ingest/.cache/{slug}.lock. The lock is
    cross-process (two CLI invocations are serialized) and released
    automatically on close. Closes the round-4 adversarial-review
    finding that write_book_state_yaml's read-modify-write was last-
    writer-wins under concurrent callers."""
    lockdir = project_root / "extracts/ingest/.cache"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockpath = lockdir / f"{slug}.lock"
    with open(lockpath, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_book_status(project_root: Path, slug: str) -> str | None:
    """Return the current canonical status for `slug`, or None if no
    state shard exists. Used by phase-transition commands to enforce
    state-machine preconditions before mutating anything."""
    path = project_root / "extracts/ingest/state" / f"{slug}.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("book", {}).get("status")


class TransitionError(ValueError):
    """Raised when a phase-transition CLI would move a book into an
    inconsistent state machine position."""


def write_book_state_yaml(project_root: Path, slug: str, fields: dict) -> Path:
    """Write extracts/ingest/state/{slug}.yaml. Merges with any existing
    content so callers can update a subset of book fields."""
    path = project_root / "extracts/ingest/state" / f"{slug}.yaml"
    existing = {}
    if path.exists():
        existing = yaml.safe_load(path.read_text()) or {}
    book = dict(existing.get("book", {}))
    book["slug"] = slug
    for k, v in fields.items():
        if v is not None:
            book[k] = v
    doc = {"book": book}
    _atomic_write_text(path, yaml.safe_dump(doc, sort_keys=False))
    return path


def write_source_files_into_extract(project_root: Path, slug: str,
                                    records: list[dict]) -> Path:
    """Update the source_files: block in extracts/ingest/{slug}.yaml.
    The extract must already exist (it is written by the ingest skill
    during extraction, before this is called)."""
    path = project_root / "extracts/ingest" / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist — extract the book before calling "
            f"replace-files. The CLI refuses to create an empty extract "
            f"to avoid masking a skill bug.")
    extract = yaml.safe_load(path.read_text()) or {}
    extract.setdefault("book", {})["slug"] = slug
    extract["source_files"] = records
    _atomic_write_text(path, yaml.safe_dump(extract, sort_keys=False))
    return path


import argparse
import json
import sys
import subprocess


def _project_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def _open_db(root: Path, *, skip_rebuild: bool = False) -> sqlite3.Connection:
    """Open the DB and rebuild if the YAML fingerprint has drifted.

    Closes the adversarial-review finding that a non-empty cache was
    always trusted. A rebuild is forced on any change to canonical
    YAML inputs (first run, worktree switch, pull, manual edit).

    `skip_rebuild=True` opens without attempting rebuild — for the
    `status` subcommand, which must stay observable even when canon
    is inconsistent.
    """
    db_path = root / "extracts/ingest/.cache/state.db"
    conn = init_db(db_path)
    if skip_rebuild:
        return conn
    state_dir = root / "extracts/ingest/state"
    if not state_dir.exists() or not any(state_dir.glob("*.yaml")):
        return conn  # nothing to index yet (pre-migration)
    current_fp = compute_yaml_fingerprint(root)
    stored_fp = get_meta(conn, "yaml_fingerprint")
    if stored_fp != current_fp:
        rebuild_from_yamls(conn, root)  # writes new fingerprint, or raises
    return conn


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project-root",
                   help="defaults to git repo root")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("rebuild")
    sub.add_parser("diff",
        help="reads JSON [{path,sha256},...] on stdin")
    mo = sub.add_parser("merged-on"); mo.add_argument("date")
    ff = sub.add_parser("files-for"); ff.add_argument("slug")

    ub = sub.add_parser("upsert-book",
        help="writes extracts/ingest/state/{slug}.yaml THEN "
             "inserts/updates the books row (YAML-first)")
    ub.add_argument("--slug", required=True)
    for fld in ["title", "author", "domain", "source-path", "status",
                "extracted-at", "merged-at"]:
        ub.add_argument(f"--{fld}")
    ub.add_argument("--concept-count", type=int)

    rf = sub.add_parser("replace-files",
        help="reads JSON [{path,sha256,size},...] on stdin, writes "
             "source_files: into extracts/ingest/{slug}.yaml, THEN "
             "atomically replaces the book's rows in the files table")
    rf.add_argument("--slug", required=True)

    # High-level phase-transition commands. These bundle every write the
    # phase needs into one subprocess invocation so a crash between
    # status-advance and source_files-persist (the adversarial-review
    # round-3 finding) is not possible via the documented interface.
    ce = sub.add_parser("complete-extraction",
        help="advance book to status=extracted atomically: writes "
             "source_files, sets status+extracted_at in state YAML, "
             "then updates DB. Reads [{path,sha256,size},...] on stdin.")
    ce.add_argument("--slug", required=True)
    ce.add_argument("--extracted-at", required=True)

    cm = sub.add_parser("complete-merge",
        help="advance book to status=merged atomically: sets "
             "status+merged_at+concept_count in state YAML and DB.")
    cm.add_argument("--slug", required=True)
    cm.add_argument("--merged-at", required=True)
    cm.add_argument("--concept-count", type=int, required=True)

    sub.add_parser("status",
        help="prints cache freshness and counts; never rebuilds")

    args = p.parse_args()
    root = _project_root(args.project_root)

    if args.cmd == "status":
        conn = _open_db(root, skip_rebuild=True)
        print(json.dumps({
            "books": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "schema_version": get_meta(conn, "schema_version"),
            "stored_fingerprint": get_meta(conn, "yaml_fingerprint"),
            "current_fingerprint": compute_yaml_fingerprint(root),
            "fingerprint_matches": (
                get_meta(conn, "yaml_fingerprint")
                == compute_yaml_fingerprint(root)),
        }))
        return

    conn = _open_db(root)

    if args.cmd == "rebuild":
        print(json.dumps(rebuild_from_yamls(conn, root)))
    elif args.cmd == "diff":
        corpus = [(r["path"], r["sha256"]) for r in json.load(sys.stdin)]
        print(json.dumps(unprocessed_or_changed(conn, corpus)))
    elif args.cmd == "merged-on":
        print(json.dumps(books_merged_on(conn, args.date)))
    elif args.cmd == "files-for":
        print(json.dumps(files_for_book(conn, args.slug)))
    elif args.cmd == "upsert-book":
        fields = {k.replace("-", "_"): v
                  for k, v in vars(args).items()
                  if k not in {"cmd", "project_root", "slug"} and v is not None}
        with _slug_lock(root, args.slug):
            write_book_state_yaml(root, args.slug, fields)
            upsert_book(conn, args.slug, **fields)
            set_meta(conn, "yaml_fingerprint",
                     compute_yaml_fingerprint(root))
        print(json.dumps({"ok": True}))
    elif args.cmd == "replace-files":
        records_json = json.load(sys.stdin)
        records_yaml = [{"path": r["path"], "sha256": r["sha256"],
                         **({"size": r["size"]} if r.get("size") is not None else {})}
                        for r in records_json]
        records_db = [(r["path"], r["sha256"], r.get("size"))
                      for r in records_json]
        with _slug_lock(root, args.slug):
            write_source_files_into_extract(root, args.slug, records_yaml)
            replace_files_for_book(conn, args.slug, records_db)
            set_meta(conn, "yaml_fingerprint",
                     compute_yaml_fingerprint(root))
        print(json.dumps({"ok": True, "count": len(records_db)}))
    elif args.cmd == "complete-extraction":
        # Atomic phase-1 transition. Preconditions enforced BEFORE any
        # mutation; the whole operation is held under the per-slug lock.
        records_json = json.load(sys.stdin)
        records_yaml = [{"path": r["path"], "sha256": r["sha256"],
                         **({"size": r["size"]} if r.get("size") is not None else {})}
                        for r in records_json]
        records_db = [(r["path"], r["sha256"], r.get("size"))
                      for r in records_json]
        with _slug_lock(root, args.slug):
            # preconditions — fail before writing anything
            current = read_book_status(root, args.slug)
            if current is None:
                raise TransitionError(
                    f"no state shard for '{args.slug}' — register the "
                    f"book via `upsert-book --status pending` first")
            if current not in ("pending", "extracting", "extracted"):
                raise TransitionError(
                    f"cannot complete-extraction from status={current}; "
                    f"allowed prior statuses: pending, extracting, extracted")
            extract_path = root / "extracts/ingest" / f"{args.slug}.yaml"
            if not extract_path.exists():
                raise TransitionError(
                    f"extract YAML missing at {extract_path}; the skill "
                    f"must write the concepts body before this call")
            # 1) source_files into extract YAML
            write_source_files_into_extract(root, args.slug, records_yaml)
            # 2) advance state YAML status
            write_book_state_yaml(root, args.slug,
                                  {"status": "extracted",
                                   "extracted_at": args.extracted_at})
            # 3) DB mirrors
            replace_files_for_book(conn, args.slug, records_db)
            upsert_book(conn, args.slug,
                        status="extracted", extracted_at=args.extracted_at)
            set_meta(conn, "yaml_fingerprint",
                     compute_yaml_fingerprint(root))
        print(json.dumps({"ok": True, "count": len(records_db)}))
    elif args.cmd == "complete-merge":
        with _slug_lock(root, args.slug):
            current = read_book_status(root, args.slug)
            if current != "extracted":
                raise TransitionError(
                    f"cannot complete-merge from status={current}; "
                    f"book must be extracted first")
            # verify the extract YAML has source_files so the merged
            # state doesn't land with an invariant-violating predecessor
            extract_path = root / "extracts/ingest" / f"{args.slug}.yaml"
            extract = yaml.safe_load(extract_path.read_text()) or {}
            if extract.get("source_files") is None:
                raise TransitionError(
                    f"{extract_path} lacks a source_files block; "
                    f"re-run complete-extraction before merging")
            write_book_state_yaml(root, args.slug,
                                  {"status": "merged",
                                   "merged_at": args.merged_at,
                                   "concept_count": args.concept_count})
            upsert_book(conn, args.slug,
                        status="merged", merged_at=args.merged_at,
                        concept_count=args.concept_count)
            set_meta(conn, "yaml_fingerprint",
                     compute_yaml_fingerprint(root))
        print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()

"""One-shot: monolithic state.yaml → sharded state/*.yaml + source_files.

Two-phase:
  1. Validate every entry against the corpus WITHOUT touching the repo.
     Any missing concepts_file or corpus file aborts with exit 1 (unless
     --allow-skip is set, which downgrades those entries to skipped).
  2. Only if Phase 1 is clean, commit all planned writes via temp file
     + os.replace() so an interrupted commit phase cannot leave a
     half-written file on disk.

Idempotent: re-running overwrites the per-book state YAML and the
`source_files` block of each concept extract YAML.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

import yaml


def _sha256(p: Path) -> tuple[str, int]:
    data = p.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _slug_for(entry: dict) -> str:
    cf = entry.get("concepts_file")
    if cf:
        return Path(cf).stem
    return Path(entry["path"]).name


class PlannedWrite(NamedTuple):
    path: Path
    content: str


def _atomic_write(path: Path, content: str) -> None:
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


def migrate(root: Path, allow_skip: bool = False) -> None:
    legacy = yaml.safe_load(
        (root / "extracts/ingest/state.yaml").read_text())

    # ----- Phase 1: validate, plan, collect errors. Never write. -----
    # Each entry is validated FIRST so we can downgrade its state shard
    # to status=pending when skipping. Emitting the shard with its
    # original status=merged would wedge the rebuild invariant added in
    # Task 5 (which was the round-3 adversarial-review finding).
    planned: list[PlannedWrite] = []
    hard_errors: list[str] = []
    skipped: list[str] = []

    for entry in legacy.get("books", []):
        slug = _slug_for(entry)
        entry_errors: list[str] = []

        # Validate the extract pointer
        cf = entry.get("concepts_file")
        extract_path = (root / cf) if cf else None
        if not cf:
            entry_errors.append(f"{slug}: no concepts_file pointer")
        elif not extract_path.exists():
            entry_errors.append(f"{slug}: extract {extract_path} missing")

        # Validate every source file, hash those that exist
        source_files: list[dict] = []
        for rel in entry.get("files_included", []):
            fp = root / rel
            if not fp.exists():
                entry_errors.append(f"{slug}: corpus file {rel} missing")
                continue
            digest, size = _sha256(fp)
            source_files.append({"path": rel, "sha256": digest, "size": size})

        entry_is_broken = bool(entry_errors)
        if entry_is_broken and not allow_skip:
            hard_errors.extend(entry_errors)
            # do not plan anything for this entry — we'll abort
            continue
        if entry_is_broken and allow_skip:
            skipped.extend(entry_errors)

        # Plan the state shard. If the entry is broken AND allow_skip is
        # set, downgrade to status=pending and strip post-extraction
        # metadata so rebuild does not hard-fail on the cutover.
        if entry_is_broken:
            meta = {"book": {
                "slug": slug,
                "title": entry.get("title"),
                "author": entry.get("author"),
                "domain": entry.get("domain"),
                "source_path": entry.get("path"),
                "status": "pending",
                "migration_note": (
                    "downgraded to pending during migration — source "
                    "inputs were missing; re-ingest to repopulate."),
            }}
        else:
            meta = {"book": {
                "slug": slug,
                "title": entry.get("title"),
                "author": entry.get("author"),
                "domain": entry.get("domain"),
                "source_path": entry.get("path"),
                "status": entry.get("status", "pending"),
                "extracted_at": entry.get("extracted_at"),
                "merged_at": entry.get("merged_at"),
                "concept_count": entry.get("concept_count", 0),
            }}
        planned.append(PlannedWrite(
            path=root / "extracts/ingest/state" / f"{slug}.yaml",
            content=yaml.safe_dump(meta, sort_keys=False)))

        # Only plan the extract YAML write if the entry validated cleanly
        if not entry_is_broken:
            extract = yaml.safe_load(extract_path.read_text()) or {}
            extract.setdefault("book", {})["slug"] = slug
            extract["source_files"] = source_files
            planned.append(PlannedWrite(
                path=extract_path,
                content=yaml.safe_dump(extract, sort_keys=False)))

    if skipped:
        print("Skipped (with --allow-skip):", file=sys.stderr)
        for s in skipped:
            print(f"  - {s}", file=sys.stderr)

    if hard_errors:
        print("\nMigration FAILED — no files written:", file=sys.stderr)
        for e in hard_errors:
            print(f"  - {e}", file=sys.stderr)
        print("\nFix the underlying issues or re-run with --allow-skip "
              "to force cutover (you will lose those entries' "
              "attribution).", file=sys.stderr)
        sys.exit(1)

    # ----- Phase 2: commit. All inputs validated; write atomically. -----
    for w in planned:
        _atomic_write(w.path, w.content)
        print(f"[ok] wrote {w.path.relative_to(root)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--allow-skip", action="store_true",
                    help="downgrade missing-file errors to warnings "
                         "(lossy — only for deliberate legacy cleanup)")
    args = ap.parse_args()
    migrate(Path(args.root).resolve(), allow_skip=args.allow_skip)

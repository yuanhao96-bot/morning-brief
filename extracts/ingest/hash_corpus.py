"""Hash all corpus files and pipe to state_db.py diff."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

corpus = []
for p in Path("sources/corpus/reading").rglob("*"):
    if p.suffix.lower() in {".pdf", ".txt", ".md"}:
        corpus.append(
            {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        )

print(f"Total files: {len(corpus)}", file=sys.stderr)

payload = json.dumps(corpus)
result = subprocess.run(
    ["python", "skills/ingest/state_db.py", "diff"],
    input=payload,
    capture_output=True,
    text=True,
    check=True,
)
print(result.stdout)

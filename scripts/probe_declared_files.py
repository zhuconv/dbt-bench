#!/usr/bin/env python3
"""Compare explicitly declared initial files, without discovering output tables."""
import hashlib
import json
from pathlib import Path
import sys

expected = json.loads(Path(sys.argv[1]).read_text())
observed = []
for row in expected:
    path = Path(row['container_path'])
    value = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    observed.append({**row, 'observed_sha256': value, 'matches': value == row['sha256']})
passed = bool(observed) and all(row['matches'] for row in observed)
print(json.dumps({'passed': passed, 'files': observed}, indent=2))
raise SystemExit(0 if passed else 1)

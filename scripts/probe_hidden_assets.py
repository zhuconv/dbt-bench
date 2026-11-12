#!/usr/bin/env python3
"""Inside a fresh container, check exact copies of declared hidden files."""
import hashlib
import json
from pathlib import Path
import platform
import sys

hidden = json.loads(Path(sys.argv[1]).read_text())
sizes = {r['size'] for r in hidden}
by_hash = {}
for row in hidden:
    by_hash.setdefault(row['sha256'], []).append(row['source'])
matches = []
for directory in sys.argv[2:]:
    for path in sorted(Path(directory).rglob('*')):
        if path.is_file() and path.stat().st_size in sizes:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if checksum in by_hash:
                matches.append({'path': str(path), 'sha256': checksum, 'sources': by_hash[checksum]})
print(json.dumps({'architecture': platform.machine(), 'declared_hidden_files': len(hidden),
                  'roots': sys.argv[2:], 'exact_matches': matches,
                  'scope': 'Exact nonempty full-file matches only; not a general information-flow proof'}, indent=2))

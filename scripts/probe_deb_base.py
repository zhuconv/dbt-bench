#!/usr/bin/env python3
"""Inspect a pristine DEB container before Harbor uploads solution or tests."""
import hashlib
import json
from pathlib import Path
import platform
import stat
import sys


def manifest(directory):
    base = Path(directory)
    return [{"path": str(p.relative_to(base)), "mode": stat.S_IMODE(p.stat().st_mode),
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(base.rglob('*')) if p.is_file()]


source = manifest('/opt/dbt-bench-initial/dbt_packages')
initialized = manifest('/app/dbt_models_duckdb/dbt_packages')
assert source and source == initialized
hidden = json.loads(Path(sys.argv[1]).read_text())
sizes = {r['size'] for r in hidden}
hashes = {r['sha256'] for r in hidden}
hits = []
for directory in ('/app', '/opt', '/root'):
    for path in Path(directory).rglob('*'):
        if path.is_file() and path.stat().st_size in sizes:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in hashes:
                hits.append({'path': str(path), 'sha256': digest})
print(json.dumps({'architecture': platform.machine(), 'identical_initial_packages': True,
                  'package_file_count': len(source), 'package_manifest_sha256': hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest(),
                  'hidden_asset_exact_byte_matches': hits,
                  'scope': 'Exact full-file copies of declared DEB solution/tests scanned in /app,/opt,/root; not a general information-flow proof'}, indent=2))

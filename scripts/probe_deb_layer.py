import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess


def manifest(root):
    records = []
    for p in sorted(root.rglob('*')):
        row = {'path': str(p.relative_to(root))}
        if p.is_symlink():
            row['link'] = os.readlink(p)
        elif p.is_file():
            row.update(sha256=hashlib.sha256(p.read_bytes()).hexdigest(), mode=p.stat().st_mode & 0o777)
        else:
            continue
        records.append(row)
    return records


hidden = json.loads(Path('/review/hidden-files.json').read_text())
allowed = {(r['path'], r['hidden_source'], r['sha256'])
           for r in json.loads(Path('/review/allowed-public-copies.json').read_text())}
by_size = {}
for row in hidden:
    by_size.setdefault(row['size'], []).append(row)
matches = []
for folder in ('/app', '/opt', '/root'):
    for p in Path(folder).rglob('*'):
        if p.is_file() and p.stat().st_size in by_size:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            for row in by_size[p.stat().st_size]:
                if h == row['sha256']:
                    matches.append({'path': str(p), 'hidden_source': row['path'],
                                    'native_public_input': (str(p), row['path'], h) in allowed})
server_python = Path('/opt/harbor-server/bin/python3')
server = None
if server_python.exists():
    code = "import importlib.metadata,json,sys,fastapi,uvicorn; print(json.dumps({'python':sys.executable,'packages':dict(sorted((d.metadata['Name'].lower().replace('_','-'),d.version) for d in importlib.metadata.distributions()))}))"
    server = json.loads(subprocess.check_output([str(server_python), '-c', code], text=True))
print(json.dumps({
    'app': manifest(Path('/app')),
    'initial_packages': manifest(Path('/opt/dbt-bench-initial')),
    'task_python_packages': dict(sorted((d.metadata['Name'].lower().replace('_', '-'), d.version) for d in importlib.metadata.distributions())),
    'server': server,
    'tools': {name: shutil.which(name) for name in ('tmux', 'asciinema')},
    'hidden_asset_exact_byte_matches': matches,
    'unexplained_hidden_matches': [m for m in matches if not m['native_public_input']],
    'hidden_paths_absent': not Path('/solution').exists() and not Path('/tests').exists(),
    'scope': 'Initial task files and package versions, declared hidden files scanned in /app,/opt,/root; no model invocation or general information-flow proof'
}, indent=2))
assert all(m['native_public_input'] for m in matches)

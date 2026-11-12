"""Inspect a freshly initialized Spider image; no model, gold, or dbt deps."""
import hashlib
import json
import os
from pathlib import Path
import sys
import duckdb

root = Path('/workspace')
expected = json.loads(Path('/review/initial-files.json').read_text())
observed = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
errors = []
if observed != set(expected):
    errors.append({'missing': sorted(set(expected) - observed), 'extra': sorted(observed - set(expected))})
for name, row in expected.items():
    p = root / name
    if not p.exists() and not p.is_symlink():
        continue
    if 'link' in row:
        okay = p.is_symlink() and os.readlink(p) == row['link']
    else:
        okay = (not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest() == row['sha256']
                and p.stat().st_mode & 0o777 == row['mode'])
    if not okay:
        errors.append({'changed': name})
database = sys.argv[1]
try:
    con = duckdb.connect(database, read_only=True)
    tables = con.execute('SELECT table_schema,table_name FROM information_schema.tables ORDER BY 1,2').fetchall()
    con.close()
    database_error = None
except Exception as exc:
    tables, database_error = [], str(exc)
hidden_paths = {name: Path(name).exists() for name in ('/solution', '/tests', '/root/.ssh')}
result = {'duckdb': duckdb.__version__, 'python': sys.version, 'python_executable': sys.executable,
          'workspace_files': len(expected), 'initial_workspace_exact': not errors,
          'file_errors': errors, 'database': database, 'database_error': database_error,
          'initial_tables': tables, 'hidden_paths_exist': hidden_paths,
          'vendored_packages_present': (root / 'dbt_packages').is_dir(),
          'scope': 'Exact task project initialization and database readability; no semantic certification.'}
print(json.dumps(result, indent=2))
sys.exit(int(bool(errors or database_error or any(hidden_paths.values()) or duckdb.__version__ != '1.2.2')))

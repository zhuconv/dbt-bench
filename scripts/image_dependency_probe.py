"""Read installed dependency inventories inside a disposable image instance.

This is a build provenance probe, not an agent/verifier compatibility test.
It neither runs dbt nor reads task databases, hidden tests, or solutions.
"""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def package_tree(path):
    """Bind exact file bytes/modes and link text without following symlinks."""
    root = Path(path)
    if not root.is_dir():
        return {'path': str(root), 'present': False}
    h = hashlib.sha256()
    files = links = total_bytes = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + names):
            p = Path(directory) / name
            row = {'path': p.relative_to(root).as_posix(), 'mode': p.lstat().st_mode & 0o777}
            if p.is_symlink():
                row['link'] = os.readlink(p)
                links += 1
            elif p.is_file():
                row['sha256'] = digest(p)
                files += 1
                total_bytes += p.stat().st_size
            elif p.is_dir():
                row['directory'] = True
            else:
                raise ValueError('Unexpected special file in dependency tree: ' + str(p))
            h.update((json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode())
    return {'path': str(root), 'present': True, 'sha256': h.hexdigest(),
            'files': files, 'links': links, 'bytes': total_bytes,
            'hash_format': 'sorted-walk-json-file-mode-and-link-v1'}


def python_inventory():
    packages = []
    for dist in importlib.metadata.distributions():
        metadata = {}
        for name in ('METADATA', 'RECORD', 'INSTALLER', 'direct_url.json'):
            value = dist.read_text(name)
            if value is not None:
                metadata[name] = hashlib.sha256(value.encode()).hexdigest()
        packages.append({'name': dist.metadata.get('Name'), 'version': dist.version,
                         'metadata_sha256': metadata})
    return {'executable': sys.executable, 'prefix': sys.prefix, 'version': sys.version,
            'packages': sorted(packages, key=lambda x: (x['name'] or '', x['version'])),
            'scope': 'Installed distribution metadata; RECORD contents are bound, installed files are not individually verified.'}


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return {'argv': argv, 'returncode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr}


def inspect():
    interpreters = {str(Path(sys.executable).absolute())}
    for candidate in ('/usr/bin/python3', '/usr/local/bin/python3', '/opt/harbor-server/bin/python3'):
        if Path(candidate).is_file():
            interpreters.add(candidate)
    dbt = shutil.which('dbt')
    dbt_info = None
    if dbt:
        p = Path(dbt)
        first = p.read_bytes().split(b'\n', 1)[0].decode('utf-8', errors='replace')
        dbt_info = {'path': dbt, 'sha256': digest(p), 'shebang': first}
        # Do not collapse virtualenv symlinks: the same executable can have
        # different site-packages depending on its invocation path.
        if first.startswith('#!/') and len(first[2:].split()) == 1 and Path(first[2:]).is_file():
            interpreters.add(first[2:])
    inventories = []
    for executable in sorted(interpreters):
        result = command([executable, str(Path(__file__).absolute()), '--python-only'])
        if result['returncode'] == 0:
            result['inventory'] = json.loads(result.pop('stdout'))
        inventories.append(result)
    commands = {}
    for name, args in [('node', ['--version']), ('npm', ['--version']),
                       ('dpkg-query', ['-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n'])]:
        executable = shutil.which(name)
        commands[name] = command([executable, *args]) if executable else {'present': False}
    return {'schema_version': 1, 'python': inventories, 'dbt': dbt_info, 'commands': commands,
            'package_trees': [package_tree(p) for p in ('/app/dbt_packages',
                              '/app/dbt_models_duckdb/dbt_packages', '/workspace/dbt_packages')],
            'scope': 'Disposable initial image dependency inventory only. No dbt, SQL, model, solution, or verifier execution.'}


if __name__ == '__main__':
    print(json.dumps(python_inventory() if sys.argv[1:] == ['--python-only'] else inspect(), indent=2))

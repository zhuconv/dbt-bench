#!/usr/bin/env python3
"""Compare explicit old/new DEB images before registering a server-only layer.

This is a clean container comparison, not Qwen or task semantic validation.
Native task files and the task Python environment must remain identical.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import require_runtime
from dbt_bench.catalog import load_catalog
from dbt_bench.io import contained, evidence_ok, evidence_ref, file_hash, read_json, write_json


def reviewed_public_copies(root, review, selected):
    """Allow only byte-identical inputs explicitly copied by this native task."""
    key = 'deb__dbt-consolidate'
    if key not in selected:
        raise ValueError('Public-copy exception is specific to consolidate')
    task = Path(root) / 'tasks' / key
    instructions = [s.strip() for s in (task / 'environment/Dockerfile').read_text().splitlines()
                    if s.strip() and not s.lstrip().startswith('#')]
    if instructions != ['FROM ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0', 'COPY data /app/data']:
        raise ValueError('Native COPY declaration changed')
    allowed = []
    for record in review['reviewed_matches']:
        source_ref = record['native_public_input']
        source = contained(root, source_ref['path'])
        if source.parent != task / 'environment/data' or not evidence_ok(source_ref, root):
            raise ValueError('Reviewed public input missing, changed or outside native COPY')
        if record['path'] != '/app/data/' + source.name or record['sha256'] != source_ref['sha256']:
            raise ValueError('Public input destination/hash mismatch')
        for name in record['sources']:
            hidden = contained(root, name)
            if hidden.parent != task / 'tests/private_data' or file_hash(hidden) != record['sha256']:
                raise ValueError('Claimed hidden duplicate differs from native public input')
            allowed.append({'path': record['path'], 'hidden_source': key + '/' + str(hidden.relative_to(task)),
                            'sha256': record['sha256']})
    if not allowed or len(allowed) != len({(r['path'], r['hidden_source']) for r in allowed}):
        raise ValueError('Missing or duplicate explicit public-copy review')
    return allowed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--task', action='append', required=True)
    parser.add_argument('--public-copy-review', type=Path, help='Explicit native-public-input duplicate review')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if len(args.task) != len(set(args.task)):
        raise ValueError('Duplicate selected task')
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    if any(k not in rows or rows[k]['source'] != 'deb' for k in args.task):
        raise ValueError('Only explicit DEB candidate IDs are supported')
    runtime = require_runtime('apptainer')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, out / 'runner.py')
    shutil.copy2(ROOT / 'scripts/probe_deb_layer.py', out / 'probe.py')
    hidden = []
    for key in args.task:
        task = ROOT / rows[key]['run_path']
        for part in ('solution', 'tests'):
            for p in sorted((task / part).rglob('*')):
                if p.is_file() and p.stat().st_size:
                    hidden.append({'path': key + '/' + str(p.relative_to(task)),
                                   'size': p.stat().st_size, 'sha256': file_hash(p)})
    write_json(out / 'hidden-files.json', hidden)
    allowed = reviewed_public_copies(ROOT, read_json(args.public_copy_review), args.task) if args.public_copy_review else []
    write_json(out / 'allowed-public-copies.json', allowed)
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    results = {}
    for label, path in [('before', args.before), ('after', args.after)]:
        image = path.resolve()
        command = [runtime['binary'], 'exec', '--fakeroot', '--writable-tmpfs', '--pid',
                   '--no-mount', 'home,tmp,cwd,hostfs,bind-paths', '--bind', str(out) + ':/review:ro',
                   str(image), '/usr/local/bin/python3', '/review/probe.py']
        process = subprocess.run(command, capture_output=True, text=True, timeout=240, env=env)
        (out / (label + '.stderr')).write_text(process.stderr)
        (out / (label + '.json')).write_text(process.stdout)
        if process.returncode:
            raise RuntimeError(f'{label} image inspection failed; see {out}')
        results[label] = json.loads(process.stdout)
    before, after = results['before'], results['after']
    for key in ('app', 'initial_packages', 'task_python_packages'):
        if before[key] != after[key]:
            raise ValueError('Task environment changed: ' + key)
    if before['server'] is not None or not after['server'] or not all(after['tools'].values()):
        raise ValueError('Expected old image without server venv and new image with all bootstrap tools')
    expected = dict(re.findall(r'^([A-Za-z0-9_.-]+)==([^\s]+)',
        (ROOT / 'environments/apptainer/harbor-server-requirements.txt').read_text(), re.M))
    expected = {k.lower().replace('_', '-'): v for k, v in expected.items()}
    actual = {k: v for k, v in after['server']['packages'].items() if k != 'pip'}
    if expected != actual:
        raise ValueError('Resolved server package versions differ from the locked requirements')
    if not before['hidden_paths_absent'] or not after['hidden_paths_absent'] or after['unexplained_hidden_matches']:
        raise ValueError('Hidden-file visibility review failed')
    report = {
        'passed': True, 'tasks': args.task, 'app_file_count': len(after['app']),
        'initial_app_files_exact': True, 'initial_package_files_exact': True,
        'task_python_package_versions_exact': True, 'server_versions_match_lock': True,
        'server_packages': after['server'],
        'old_image': evidence_ref(args.before.resolve(), ROOT), 'new_image': evidence_ref(args.after.resolve(), ROOT),
        'before': evidence_ref(out / 'before.json', ROOT), 'after': evidence_ref(out / 'after.json', ROOT),
        'hidden_inventory': evidence_ref(out / 'hidden-files.json', ROOT),
        'allowed_public_copies': evidence_ref(out / 'allowed-public-copies.json', ROOT),
        'public_copy_review': evidence_ref(args.public_copy_review, ROOT) if args.public_copy_review else None,
        'probe': evidence_ref(out / 'probe.py', ROOT), 'runner': evidence_ref(out / 'runner.py', ROOT),
        'server_lock': evidence_ref(ROOT / 'environments/apptainer/harbor-server-requirements.txt', ROOT),
        'scope': 'Initial task files, package versions and exact declared hidden-file copies; not semantic validation or a general information-flow proof'
    }
    write_json(out / 'summary.json', report)
    print({'passed': True, 'tasks': len(args.task), 'app_files': report['app_file_count'], 'output': str(out / 'summary.json')})


if __name__ == '__main__':
    main()

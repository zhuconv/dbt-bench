#!/usr/bin/env python3
"""Execute native dbt deps in isolated copies and retain exact dependency trees.

No task score or environment certification is assigned here. Failed native
resolution stays a failure; vendored packages are not silently substituted.
"""
import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.io import evidence_ok, evidence_ref, file_hash, tree_hash, write_json
from dbt_bench.catalog import load_catalog
from dbt_bench.backends import require_runtime

DOCKER_SHA = 'bf991c65d29cae2d1e48fbdcf75e4b7459e6701e49d7b0e9d531a11892ef39fd'
GENERATED = {'dbt_packages', 'logs', 'package-lock.yml'}


def manifest(folder):
    result = {}
    for p in sorted(folder.rglob('*')):
        if p.is_symlink():
            result[str(p.relative_to(folder))] = {'link': os.readlink(p)}
        elif p.is_file():
            result[str(p.relative_to(folder))] = {'sha256': file_hash(p), 'mode': p.stat().st_mode & 0o777}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if len(args.task) != len(set(args.task)):
        raise ValueError('Duplicate task selection')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_snapshot = out / 'freeze_spider_deps.py'
    shutil.copy2(__file__, source_snapshot)
    runtime = require_runtime('apptainer')
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    parent = ROOT / 'artifacts/images/spider-workday002-v2.sif'
    parent_provenance = ROOT / 'artifacts/images/spider-workday002-v2-provenance.json'
    provenance = json.loads(parent_provenance.read_text())
    assert file_hash(parent) == provenance['image_sha256']
    assert all(evidence_ok(r, ROOT) for r in provenance['build_evidence'])
    definition = out / 'resolver.def'
    definition.write_text(f'''Bootstrap: localimage
From: {parent}

%post
    set -eu
    echo '#!/bin/sh' > /.singularity.d/env/90-environment.sh
    rm -rf /workspace /opt/dbt-bench-initial
    mkdir -p /workspace

%labels
    dbt_bench_purpose spider-native-dependency-resolution-v1
''')
    image = out / 'resolver.sif'
    with (out / 'build.log').open('w') as log:
        subprocess.run([runtime['binary'], 'build', '--fakeroot', str(image), str(definition)],
                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=1200, check=True)
    base_evidence = [evidence_ref(p, ROOT) for p in
                     (source_snapshot, parent, parent_provenance, definition, image, out / 'build.log')]
    results = []
    for key in args.task:
        row = rows[key]
        task = ROOT / row['run_path']
        source = task / 'environment/dbt_project'
        if row['source'] != 'spider2' or tree_hash(task) != row['adapted_task_hash']:
            raise ValueError('Task input identity changed: ' + key)
        if file_hash(task / 'environment/Dockerfile') != DOCKER_SHA or not (source / 'packages.yml').exists():
            raise ValueError('Task is outside the reviewed dependency-resolution scope: ' + key)
        directory = out / key
        directory.mkdir()
        project = directory / 'project'
        shutil.copytree(source, project, symlinks=True)
        before = manifest(project)
        write_json(directory / 'before.json', before)
        command = [runtime['binary'], 'exec', '--fakeroot', '--writable-tmpfs', '--pid',
                   '--no-mount', 'home,tmp,cwd,hostfs,bind-paths', '--bind', str(project) + ':/workspace',
                   '--pwd', '/workspace', str(image), 'dbt', 'deps', '--profiles-dir', '/workspace']
        result = {'task_key': key, 'task_hash': row['adapted_task_hash'], 'dockerfile_sha256': DOCKER_SHA,
                  'command': command, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                  'resolved': False, 'native_docker_ignores_deps_exit': True,
                  'scope': 'Initial dependency resolution only; no reference, grader or model execution',
                  'base_evidence': base_evidence}
        try:
            with (directory / 'deps.log').open('w') as log:
                proc = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            result['returncode'] = proc.returncode
            after = manifest(project)
            write_json(directory / 'after.json', after)
            unexpected = [name for name in sorted(set(before) | set(after))
                          if before.get(name) != after.get(name) and Path(name).parts[0] not in GENERATED]
            # dbt creates a telemetry UUID under profiles-dir. Permit only a
            # newly created single UUID field, retaining its actual bytes.
            if '.user.yml' in unexpected and '.user.yml' not in before:
                match = re.fullmatch(r'id: ([0-9a-f-]{36})\s*', (project / '.user.yml').read_text())
                if match and str(uuid.UUID(match.group(1))) == match.group(1):
                    unexpected.remove('.user.yml')
                    result['generated_telemetry_id'] = evidence_ref(project / '.user.yml', ROOT)
            result['unexpected_changes'] = unexpected
            result['project_tree_hash'] = tree_hash(project)
            result['package_lock_present'] = (project / 'package-lock.yml').is_file()
            # The archive and per-file hashes pin the downloaded bytes, including
            # transitive dependencies. A package version alone is insufficient.
            archive = directory / 'resolved-dependencies.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name in ('dbt_packages', 'package-lock.yml'):
                    if (project / name).exists():
                        tar.add(project / name, arcname=name, recursive=True)
            result['dependency_archive'] = evidence_ref(archive, ROOT)
            result['evidence'] = [evidence_ref(directory / name, ROOT)
                                  for name in ('before.json', 'after.json', 'deps.log')]
            result['resolved'] = proc.returncode == 0 and not unexpected
        except Exception as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
        write_json(directory / 'resolution.json', result)
        results.append({'task_key': key, 'resolved': result['resolved'],
                        'resolution': evidence_ref(directory / 'resolution.json', ROOT)})
        write_json(out / 'summary.json', {'tasks': results, 'automatic_certification': False})
        print(json.dumps(results[-1]), flush=True)
    return int(any(not r['resolved'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())

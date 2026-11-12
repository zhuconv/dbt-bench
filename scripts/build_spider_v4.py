#!/usr/bin/env python3
"""Add only missing frozen packages, or NBA's pinned pandas, to Spider v3.

Never resolve dbt dependencies or replace a task's vendored package. Native
projects, solutions and verifiers remain immutable; each image has provenance.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile

from review_tools import ROOT, cached_hashes
from build_spider_v3 import project_manifest, run
from dbt_bench import io
from dbt_bench.backends import register_sif, require_runtime
from dbt_bench.catalog import load_catalog

PACKAGES = {
    'netflix001': ['dbt_utils'],
    'scd001': ['dbt_utils'],
    'shopify_holistic_reporting001': ['spark_utils', 'klaviyo_source', 'dbt_date',
        'klaviyo', 'dbt_utils', 'shopify', 'fivetran_utils', 'shopify_source'],
    'zuora001': ['spark_utils', 'dbt_utils', 'fivetran_utils', 'zuora_source'],
    'inzight001': ['dbt_duckdb_utils'],
}


def merge_manifest(native, additions):
    overlap = set(native) & set(additions)
    if overlap:
        raise ValueError('Refuse to replace native files: ' + str(sorted(overlap)))
    for name in additions:
        if not name.startswith('dbt_packages/') or '..' in Path(name).parts:
            raise ValueError('Addition outside explicit package directory')
    return {**native, **additions}


def build_one(row, out, inputs, runtime):
    key = row['task_key']
    task_id = key.removeprefix('spider2__')
    assert task_id in PACKAGES or task_id == 'nba001'
    d = out / key
    d.mkdir()
    task = ROOT / row['run_path']
    project = task / 'environment/dbt_project'
    assert io.tree_hash(task) == row['adapted_task_hash']
    binding_path = ROOT / 'catalog/backends/apptainer' / (key + '.json')
    binding = io.read_json(binding_path)
    parent = ROOT / binding['image']['path']
    assert io.evidence_ok(binding['image'], ROOT)
    provenance_ref = [r for r in binding['evidence'] if r['path'].endswith('/provenance.json')][-1]
    old_provenance = io.read_json(ROOT / provenance_ref['path'])
    assert any('pin DuckDB 1.2.2' in s for s in old_provenance['reviewed_differences'])
    assert all(io.evidence_ok(r, ROOT) for r in binding['evidence'])
    shutil.copyfile(binding_path, d / 'previous-binding.json')
    record = (ROOT / row['validation_ref']).parent / 'record.json'
    if record.exists():
        shutil.copyfile(record, d / 'previous-validation-record.json')
    expected = project_manifest(project)
    refs = [io.evidence_ref(d / 'previous-binding.json', ROOT), binding['image'],
            *binding['evidence'], io.evidence_ref(out / 'builder.py', ROOT)]
    files, post, changes = [], [], []
    if task_id in PACKAGES:
        group = 'spider-frozen-deps-pilot-v1' if task_id == 'netflix001' else 'spider-frozen-deps-batch-v1'
        old = ROOT / 'artifacts/images' / group / key
        old_proof = io.read_json(old / 'provenance.json')
        old_image = old / 'task.sif'
        assert io.file_hash(old_image) == old_proof['image_sha256']
        names = PACKAGES[task_id]
        assert all(not (project / 'dbt_packages' / name).exists() for name in names)
        archive = d / 'missing-packages.tar'
        command = [runtime, 'exec', '--fakeroot', '--writable-tmpfs', '--no-mount',
                   'home,tmp,cwd,hostfs,bind-paths', '--pwd', '/', str(old_image),
                   'tar', '-C', '/opt/dbt-bench-initial/dbt_packages', '-cf', '-', *names]
        with archive.open('wb') as output, (d / 'extract.stderr').open('w') as error:
            subprocess.run(command, stdout=output, stderr=error, check=True, timeout=300)
        additions = d / 'additions'
        packages = additions / 'dbt_packages'
        packages.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            assert all(Path(m.name).parts[0] in names for m in tar.getmembers())
            tar.extractall(packages, filter='data')
        added = project_manifest(additions)
        expected = merge_manifest(expected, added)
        io.write_json(d / 'added-files.json', added)
        files.append(f'    {packages} /opt/dbt-bench-package-additions')
        post.append('    mkdir -p /opt/dbt-bench-initial/dbt_packages')
        for name in names:
            post += [f'    test ! -e /opt/dbt-bench-initial/dbt_packages/{name}',
                     f'    mv /opt/dbt-bench-package-additions/{name} /opt/dbt-bench-initial/dbt_packages/{name}',
                     # Inherited environment initialization can populate the
                     # workspace before %post. Add the same bytes there too;
                     # never overwrite an existing vendored package.
                     '    if [ -d /workspace/dbt_packages ]; then',
                     f'        test ! -e /workspace/dbt_packages/{name}',
                     f'        cp -a /opt/dbt-bench-initial/dbt_packages/{name} /workspace/dbt_packages/{name}',
                     '    fi']
        post.append('    rmdir /opt/dbt-bench-package-additions')
        changes.append({'kind': 'missing_frozen_packages', 'added': names,
                        'native_files_replaced': [], 'source_image': io.evidence_ref(old_image, ROOT)})
        refs += [io.evidence_ref(p, ROOT) for p in (old_image, old / 'provenance.json', archive,
                  d / 'extract.stderr', d / 'added-files.json')]
    else:
        downloads = io.read_json(inputs / 'downloads.json')
        assert downloads['resolver_executed'] is False
        assert {(w['name'], w['version']) for w in downloads['wheels']} == {('pandas', '2.2.2'), ('tzdata', '2024.1')}
        for wheel in downloads['wheels']:
            path = Path(wheel['path'])
            assert io.file_hash(path) == wheel['sha256']
            files.append(f'    {path} /opt/dbt-bench-wheel/{path.name}')
            refs.append(io.evidence_ref(path, ROOT))
        post += ['    python3 -m pip install --no-index --no-deps --force-reinstall /opt/dbt-bench-wheel/*.whl',
                 "    python3 -c 'import pandas; assert pandas.__version__ == \"2.2.2\"'",
                 '    python3 -m pip check', '    rm -rf /opt/dbt-bench-wheel']
        changes.append({'kind': 'python_compatibility', 'pandas': {'before': '3.1.1', 'after': '2.2.2'},
                        'added_dependency': {'tzdata': '2024.1'}, 'other_python_packages_changed': False})
        refs.append(io.evidence_ref(inputs / 'downloads.json', ROOT))
    io.write_json(d / 'changes.json', changes)
    io.write_json(d / 'initial-files.json', expected)
    shutil.copyfile(ROOT / 'scripts/spider_project_probe.py', d / 'probe.py')
    definition = d / 'image.def'
    definition.write_text(f'Bootstrap: localimage\nFrom: {parent}\n\n%files\n' + '\n'.join(files)
        + '\n\n%post\n    set -eu\n' + '\n'.join(post)
        + "\n    python3 -c 'import duckdb; assert duckdb.__version__ == \"1.2.2\"'\n"
        + '    python3 -m pip freeze\n\n%labels\n    dbt_bench_environment ' + key + '-frozen-additions-v4\n')
    image = d / 'task.sif'
    run([runtime, 'build', '--fakeroot', '--mksquashfs-args', '-processors 6', str(image), str(definition)], d / 'build.log')
    config = io.read_json(task / 'solution/config.json')
    command = [runtime, 'exec', '--fakeroot', '--writable-tmpfs', '--pid', '--no-mount',
               'home,tmp,cwd,hostfs,bind-paths', '--bind', str(d) + ':/review:ro', str(image),
               'python3', '/review/probe.py', config['result_db']]
    p = subprocess.run(command, capture_output=True, text=True, timeout=300)
    (d / 'probe.stdout').write_text(p.stdout)
    (d / 'probe.stderr').write_text(p.stderr)
    assert p.returncode == 0, 'Exact initial project/database probe failed'
    io.write_json(d / 'initial-visibility.json', json.loads(p.stdout))
    refs += [io.evidence_ref(p, ROOT) for p in (definition, d / 'build.log', d / 'changes.json',
             d / 'initial-files.json', d / 'probe.py', d / 'probe.stdout', d / 'probe.stderr')]
    provenance = {'architecture': platform.machine(), 'native_task_hash': row['adapted_task_hash'],
        'dockerfile_sha256': io.file_hash(task / 'environment/Dockerfile'), 'image_sha256': io.file_hash(image),
        'complete_native_build': True, 'build_kind': 'reviewed_apptainer_definition',
        'reviewed_differences': ['Retain v3 pin DuckDB 1.2.2 and original task-vendored packages.',
            'Only explicit missing packages copied from the hash-locked historical frozen-deps image; no dbt deps.',
            'NBA only: pandas 2.2.2 and its fixed tzdata dependency installed without dependency resolution.',
            'Native task, solution and verifier unchanged; historical results remain historical.'],
        'build_evidence': refs, 'agent_visibility_evidence': io.evidence_ref(d / 'initial-visibility.json', ROOT),
        'scope': 'Environment compatibility only; actual Qwen/dbt/verifier evidence remains separate.'}
    io.write_json(d / 'provenance.json', provenance)
    binding_path = register_sif(ROOT, row, image, d / 'provenance.json')
    io.write_json(d / 'result.json', {'task_key': key, 'built_and_registered': True,
        'binding': io.evidence_ref(binding_path, ROOT), 'provenance': io.evidence_ref(d / 'provenance.json', ROOT)})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--inputs', type=Path, required=True)
    p.add_argument('--task', action='append', required=True)
    a = p.parse_args()
    if os.environ.get('APPTAINER_TMPDIR'):
        Path(os.environ['APPTAINER_TMPDIR']).mkdir(parents=True, exist_ok=True)
    out = a.output.resolve()
    assert str(out).startswith('/scratch/') and not out.exists()
    out.mkdir(parents=True)
    shutil.copyfile(__file__, out / 'builder.py')
    runtime = require_runtime('apptainer')['binary']
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    with cached_hashes():
        for key in a.task:
            try:
                build_one(rows[key], out, a.inputs.resolve(), runtime)
                print(key, 'built_and_registered', flush=True)
            except Exception as exc:
                io.write_json(out / key / 'result.json', {'task_key': key, 'built_and_registered': False,
                    'error': f'{type(exc).__name__}: {exc}'})
                print(key, 'FAILED', str(exc), flush=True)


if __name__ == '__main__':
    main()

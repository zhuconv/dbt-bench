#!/usr/bin/env python3
"""Build the Spider-only DuckDB 1.2.2 environment with exact vendored packages.

Never execute dbt deps, modify native projects/verifiers, or reuse v2 evidence
as v3 certification. Individual task failures are retained and do not stop peers.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from review_tools import ROOT, cached_hashes
from dbt_bench import io
from dbt_bench.catalog import load_catalog
from dbt_bench.backends import register_sif, require_runtime

DOCKER_SHA = 'bf991c65d29cae2d1e48fbdcf75e4b7459e6701e49d7b0e9d531a11892ef39fd'


def run(command, log, timeout=1200):
    with log.open('w') as f:
        p = subprocess.run(command, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f'Exit {p.returncode}: {log}')


def project_manifest(project):
    return {str(p.relative_to(project)): ({'link': os.readlink(p)} if p.is_symlink() else
            {'sha256': io.file_hash(p), 'mode': p.stat().st_mode & 0o777})
            for p in sorted(project.rglob('*')) if p.is_file() or p.is_symlink()}


def base(out, inputs, runtime):
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, out / 'builder.py')
    downloads = io.read_json(inputs / 'downloads.json')
    wheel = Path(downloads['wheel']['path'])
    assert io.file_hash(wheel) == downloads['wheel']['sha256']
    parent = ROOT / 'artifacts/images/spider-workday002-v2.sif'
    old_provenance = ROOT / 'artifacts/images/spider-workday002-v2-provenance.json'
    old = io.read_json(old_provenance)
    assert io.file_hash(parent) == old['image_sha256']
    assert all(io.evidence_ok(r, ROOT) for r in old['build_evidence'])
    definition = out / 'image.def'
    definition.write_text(f'''Bootstrap: localimage
From: {parent}

%files
    {wheel} /opt/spider-v3/{wheel.name}

%post
    set -eu
    echo '#!/bin/sh' > /.singularity.d/env/90-environment.sh
    rm -rf /workspace /opt/dbt-bench-initial
    mkdir -p /workspace
    echo '{downloads['wheel']['sha256']}  /opt/spider-v3/{wheel.name}' | sha256sum -c -
    python3 -m pip install --no-index --no-deps --force-reinstall /opt/spider-v3/{wheel.name}
    python3 -c 'import duckdb; assert duckdb.__version__ == "1.2.2"'
    python3 -m pip check
    python3 -m pip freeze
    rm -rf /opt/spider-v3

%labels
    dbt_bench_environment spider-duckdb122-vendored-v3
''')
    image = out / 'base.sif'
    run([runtime, 'build', '--fakeroot', '--mksquashfs-args', '-processors 8', str(image), str(definition)], out / 'build.log')
    refs = [io.evidence_ref(p, ROOT) for p in (out / 'builder.py', parent, old_provenance, wheel,
            inputs / 'downloads.json', definition, out / 'build.log', ROOT / 'environments/python/spider-v3/pyproject.toml',
            ROOT / 'environments/python/spider-v3/uv.lock')]
    io.write_json(out / 'manifest.json', {'image': io.evidence_ref(image, ROOT), 'evidence': refs,
        'duckdb': '1.2.2', 'dbt_deps_executed': False, 'contains_task_project': False,
        'changed_python_packages': {'duckdb': {'before': '2.0.0', 'after': '1.2.2'}},
        'scope': 'Spider-only common dependency layer; ADE/DEB untouched.'})
    return io.read_json(out / 'manifest.json')


def build_one(row, out, base_manifest, runtime):
    key = row['task_key']
    directory = out / key
    directory.mkdir(exist_ok=False)
    result = {'task_key': key, 'built_and_registered': False}
    try:
        task = ROOT / row['run_path']
        project = task / 'environment/dbt_project'
        assert row['source'] == 'spider2' and io.tree_hash(task) == row['adapted_task_hash']
        assert io.file_hash(task / 'environment/Dockerfile') == DOCKER_SHA
        config = io.read_json(task / 'solution/config.json')
        assert Path(config['result_db']).parent == Path('/workspace')
        assert (project / Path(config['result_db']).name).is_file()
        expected = project_manifest(project)
        io.write_json(directory / 'initial-files.json', expected)
        vendored = {k: v for k, v in expected.items() if k.startswith('dbt_packages/')}
        io.write_json(directory / 'vendored-packages.json', {'files': vendored, 'hash': io.digest(vendored),
            'present': bool(vendored), 'packages_yml_present': (project / 'packages.yml').exists(),
            'policy': 'Retain source bytes, modes and symlinks; no dependency resolution during build.'})
        shutil.copyfile(ROOT / 'scripts/spider_project_probe.py', directory / 'probe.py')
        definition = directory / 'image.def'
        definition.write_text(f'''Bootstrap: localimage
From: {base_manifest['image']['path']}

%files
    {project} /opt/dbt-bench-project

%post
    set -eu
    echo '#!/bin/sh' > /.singularity.d/env/90-environment.sh
    rmdir /workspace
    mv /opt/dbt-bench-project /workspace
    mkdir -p /opt/dbt-bench-initial
    for folder in dbt_packages target logs; do
        if [ -d "/workspace/$folder" ]; then
            mv "/workspace/$folder" "/opt/dbt-bench-initial/$folder"
        fi
    done

%environment
    for folder in dbt_packages target logs; do
        if [ -d "/opt/dbt-bench-initial/$folder" ] && [ ! -e "/workspace/$folder" ]; then
            cp -a "/opt/dbt-bench-initial/$folder" "/workspace/$folder"
        fi
    done

%labels
    dbt_bench_environment {key}-duckdb122-vendored-v3
''')
        image = directory / 'task.sif'
        run([runtime, 'build', '--fakeroot', '--mksquashfs-args', '-processors 8', str(image), str(definition)], directory / 'build.log')
        command = [runtime, 'exec', '--fakeroot', '--writable-tmpfs', '--pid', '--no-mount',
            'home,tmp,cwd,hostfs,bind-paths', '--bind', str(directory) + ':/review:ro',
            str(image), 'python3', '/review/probe.py', config['result_db']]
        p = subprocess.run(command, capture_output=True, text=True, timeout=300)
        (directory / 'probe.stderr').write_text(p.stderr)
        (directory / 'probe.stdout').write_text(p.stdout)
        observed = json.loads(p.stdout)
        io.write_json(directory / 'initial-visibility.json', observed)
        assert p.returncode == 0, 'Initial project/version/database probe failed'
        refs = base_manifest['evidence'] + [base_manifest['image']] + [io.evidence_ref(p, ROOT) for p in
            (out / 'builder.py', directory / 'probe.py', directory / 'probe.stdout', directory / 'probe.stderr',
             definition, directory / 'build.log', directory / 'initial-files.json', directory / 'vendored-packages.json')]
        provenance = {'architecture': platform.machine(), 'native_task_hash': row['adapted_task_hash'],
            'dockerfile_sha256': DOCKER_SHA, 'image_sha256': io.file_hash(image), 'complete_native_build': True,
            'build_kind': 'reviewed_apptainer_definition', 'reviewed_differences': [
                'Environment compatibility repair: pin DuckDB 1.2.2; all other Python packages retain the v2 versions.',
                'Skip native Dockerfile dbt deps to preserve the entire task-vendored dependency tree byte for byte.',
                'Remove parent task data; initialize only this task exact project, database and package state.',
                'No vendored packages is recorded explicitly, not filled by a floating dependency download.',
                'Native verifier and solution bytes unchanged. Historical v2 results are not v3 evidence.'],
            'build_evidence': refs, 'agent_visibility_evidence': io.evidence_ref(directory / 'initial-visibility.json', ROOT),
            'scope': 'Environment initialization only; real Qwen/dbt/verifier checks remain separate.'}
        io.write_json(directory / 'provenance.json', provenance)
        binding = register_sif(ROOT, row, image, directory / 'provenance.json')
        result.update(built_and_registered=True, binding=io.evidence_ref(binding, ROOT),
            provenance=io.evidence_ref(directory / 'provenance.json', ROOT), vendored_packages=bool(vendored))
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    io.write_json(directory / 'result.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', action='store_true')
    p.add_argument('--base-dir', type=Path, required=True)
    p.add_argument('--inputs', type=Path, default=ROOT / 'artifacts/images/spider-v3-inputs')
    p.add_argument('--output', type=Path)
    p.add_argument('--task', action='append')
    p.add_argument('--concurrent', type=int, default=3)
    a = p.parse_args()
    runtime = require_runtime('apptainer')['binary']
    with cached_hashes():
        if a.base:
            print(base(a.base_dir.resolve(), a.inputs.resolve(), runtime)['image'], flush=True)
            return
        assert a.output and a.task and len(a.task) == len(set(a.task))
        out = a.output.resolve()
        assert str(out).startswith('/scratch/')
        out.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(__file__, out / 'builder.py')
        base_manifest = io.read_json(a.base_dir / 'manifest.json')
        assert io.evidence_ok(base_manifest['image'], ROOT)
        assert all(io.evidence_ok(r, ROOT) for r in base_manifest['evidence'])
        rows = {r['task_key']: r for r in load_catalog(ROOT)}
        selected = [rows[k] for k in a.task]
        results = []
        with ThreadPoolExecutor(max_workers=a.concurrent) as pool:
            futures = [pool.submit(build_one, r, out, base_manifest, runtime) for r in selected]
            for f in as_completed(futures):
                result = f.result()
                results.append(result)
                io.write_json(out / 'summary.json', {'tasks': results, 'planned_tasks': len(selected),
                    'model_requests': 0, 'native_verifier_modified': False})
                print(json.dumps(result), flush=True)
        raise SystemExit(int(any(not r['built_and_registered'] for r in results)))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Build Spider images from verified, frozen native dbt-deps outputs.

Reuse the verified fixed Python/server layer, replace the entire workspace and
initial copies, verify every initial file, then probe native Harbor contexts.
Dependency resolution is performed by freeze_spider_deps.py; this build makes
no network requests. This is image preparation, never task certification.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import argparse
import json
import os
import platform
import shutil
import subprocess
from dbt_bench.backends import require_runtime, register_sif
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, evidence_ok, file_hash, tree_hash, write_json

DOCKER_SHA = 'bf991c65d29cae2d1e48fbdcf75e4b7459e6701e49d7b0e9d531a11892ef39fd'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--resolution-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_snapshot = out/'build_spider_frozen_deps.py'
    shutil.copy2(__file__, source_snapshot)
    rows = {r['task_key']:r for r in load_catalog(ROOT)}
    if len(args.task) != len(set(args.task)):
        raise ValueError('Duplicate requested task')
    runtime = require_runtime('apptainer')
    parent = ROOT/'artifacts/images/spider-workday002-v2.sif'
    parent_provenance = ROOT/'artifacts/images/spider-workday002-v2-provenance.json'
    provenance = json.loads(parent_provenance.read_text())
    assert file_hash(parent) == provenance['image_sha256']
    assert all(evidence_ok(r,ROOT) for r in provenance['build_evidence'])
    results = []
    for key in args.task:
        row = rows[key]
        task = ROOT/row['run_path']
        source_project = task/'environment/dbt_project'
        if row['source'] != 'spider2' or tree_hash(task) != row['adapted_task_hash']:
            raise ValueError('Source task unavailable or changed: '+key)
        if file_hash(task/'environment/Dockerfile') != DOCKER_SHA or not (source_project/'packages.yml').exists():
            raise ValueError('This builder does not cover the native Docker instructions/dependencies: '+key)
        resolution_path = args.resolution_dir.resolve()/key/'resolution.json'
        resolution = json.loads(resolution_path.read_text())
        project = resolution_path.parent/'project'
        refs = resolution['base_evidence'] + resolution['evidence'] + [resolution['dependency_archive']]
        if not (resolution['resolved'] and resolution['returncode'] == 0
                and resolution['task_hash'] == row['adapted_task_hash']
                and resolution['dockerfile_sha256'] == DOCKER_SHA
                and not resolution['unexpected_changes']
                and tree_hash(project) == resolution['project_tree_hash']
                and all(evidence_ok(ref, ROOT) for ref in refs)):
            raise ValueError('Missing, unsuccessful or changed dependency snapshot: '+key)
        native_config = json.loads((task/'solution/config.json').read_text())
        if Path(native_config['result_db']).parent != Path('/workspace') or not (project/Path(native_config['result_db']).name).is_file():
            raise ValueError('Declared native database is not in the copied workspace')
        directory = out/key
        directory.mkdir()
        definition = directory/'image.def'
        image = directory/'task.sif'
        definition.write_text(f'''Bootstrap: localimage
From: {parent}

%files
    {project} /opt/dbt-bench-project

%post
    set -eu
    # localimage appends new %environment to inherited 90-environment.sh.
    # This pinned parent's file contains only its workspace-copy loop.
    # Replace that loop once; leave Docker's 10-docker2singularity env intact.
    echo '#!/bin/sh' > /.singularity.d/env/90-environment.sh
    rm -rf /workspace /opt/dbt-bench-initial
    mv /opt/dbt-bench-project /workspace
    test -f /workspace/packages.yml
    mkdir -p /opt/dbt-bench-initial
    for folder in dbt_packages target logs; do
        if [ -d "/workspace/$folder" ]; then
            mv "/workspace/$folder" "/opt/dbt-bench-initial/$folder"
        fi
    done

%environment
    for folder in dbt_packages target logs; do
        if [ -d "/opt/dbt-bench-initial/$folder" ]; then
            cp -a "/opt/dbt-bench-initial/$folder" "/workspace/$folder"
        fi
    done

%labels
    dbt_bench_derivative {key}-frozen-deps-v1
''')
        def run(command, log, timeout):
            with log.open('w') as handle:
                p = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout)
            if p.returncode:
                raise RuntimeError(f'Command failed ({p.returncode}): {log.relative_to(ROOT)}')
        try:
            run([runtime['binary'],'build','--fakeroot',str(image),str(definition)],directory/'build.log',1200)
            expected=[]
            for p in sorted(project.rglob('*')):
                if p.is_symlink():
                    expected.append({'relative':str(p.relative_to(project)),'link':os.readlink(p)})
                elif p.is_file():
                    expected.append({'relative':str(p.relative_to(project)),'sha256':file_hash(p),'mode':p.stat().st_mode & 0o777})
            write_json(directory/'initial-files.json',expected)
            hidden=[]
            for section in ('solution','tests'):
                for p in sorted((task/section).rglob('*')):
                    if p.is_file() and p.stat().st_size:
                        hidden.append({'source':str(p.relative_to(task)),'size':p.stat().st_size,'sha256':file_hash(p)})
            write_json(directory/'hidden-files.json',hidden)
            probe = directory/'probe.py'
            probe.write_text('''import hashlib,json,os
from pathlib import Path
root=Path('/workspace');expected=json.loads(Path('/review/initial-files.json').read_text())
observed={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
assert observed == {r['relative'] for r in expected}, 'Missing/extra initial workspace files'
for row in expected:
    p=root/row['relative']
    if 'link' in row: assert p.is_symlink() and os.readlink(p)==row['link']
    else: assert not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256'] and p.stat().st_mode & 0o777 == row['mode'], row['relative']
hidden=json.loads(Path('/review/hidden-files.json').read_text());sizes={r['size'] for r in hidden};by_hash={}
for r in hidden:by_hash.setdefault(r['sha256'],[]).append(r['source'])
matches=[]
for folder in ['/workspace','/opt/dbt-bench-initial','/root']:
    for p in Path(folder).rglob('*'):
        if p.is_file() and p.stat().st_size in sizes:
            h=hashlib.sha256(p.read_bytes()).hexdigest()
            if h in by_hash: matches.append({'path':str(p),'hidden_sources':by_hash[h]})
print(json.dumps({'initial_workspace_exact':True,'files':len(expected),'exact_hidden_matches':matches,'scope':'Exact nonempty file scan, not an information-flow proof'},indent=2))
assert not matches, 'Hidden matches require human source review'
''')
            inspection = subprocess.run([runtime['binary'],'exec','--fakeroot','--writable-tmpfs','--pid','--no-mount','home,tmp,cwd,hostfs,bind-paths',
                 '--bind',str(directory)+':/review:ro',str(image),'python3','/review/probe.py'],capture_output=True,text=True,timeout=180)
            (directory/'initial-visibility.log').write_text(inspection.stdout+inspection.stderr)
            if inspection.returncode:
                raise RuntimeError('Initial file/visibility probe failed; inspect initial-visibility.log')
            write_json(directory/'initial-visibility.json',json.loads(inspection.stdout))
            run([sys.executable,str(ROOT/'scripts/check_harbor_apptainer.py'),'--task',str(task),'--image',str(image),
                 '--db',native_config['result_db'],'--output-dir',str(directory/'context')],directory/'context-command.log',900)
            assert json.loads((directory/'context/result.json').read_text())['integration_passed']
            p={'architecture':platform.machine(),'native_task_hash':row['adapted_task_hash'],
               'dockerfile_sha256':DOCKER_SHA,'image_sha256':file_hash(image),'complete_native_build':True,
               'build_kind':'reviewed_apptainer_definition',
               'reviewed_differences':['Reuse the exact verified Python/server layer from the workday-v2 SIF; replace its known 90-environment workspace-copy loop once, and delete the entire parent workspace and initial package copies before placing this native project.',
                   'Native dbt deps executed in a clean project copy. The package lock and complete downloaded dependency archive are hashed; this image reuses that exact resolved state without downloading again.',
                   'All project/data bytes outside the declared dependency/log/lock paths remain native; dbt-generated telemetry UUID is recorded. Full resolved initial workspace is verified, and writable startup copies preserve later mutations.',
                   'Actual Docker/Apptainer byte parity and Qwen tool execution are not claimed.'],
               'dependency_resolution':evidence_ref(resolution_path,ROOT),
               'build_evidence':[evidence_ref(p,ROOT) for p in [source_snapshot,resolution_path,parent,parent_provenance,definition,directory/'build.log',directory/'initial-files.json',directory/'hidden-files.json',probe,directory/'context/result.json']] + refs,
               'agent_visibility_evidence':evidence_ref(directory/'initial-visibility.json',ROOT),
               'scope':'Build and native-context readiness only, no reference/semantic/task certification'}
            write_json(directory/'provenance.json',p)
            binding=register_sif(ROOT,row,image,directory/'provenance.json')
            results.append({'task_key':key,'built_and_registered':True,'binding':str(binding),'provenance':evidence_ref(directory/'provenance.json',ROOT)})
        except Exception as exc:
            results.append({'task_key':key,'built_and_registered':False,'error':f'{type(exc).__name__}: {exc}'})
        write_json(out/'summary.json',{'qwen_smoke':False,'benchmark_trials':0,'tasks':results})
        print(json.dumps(results[-1]),flush=True)
    return int(any(not r['built_and_registered'] for r in results))


if __name__=='__main__':raise SystemExit(main())

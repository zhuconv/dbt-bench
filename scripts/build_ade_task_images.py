#!/usr/bin/env python3
"""Execute pinned ADE setup in task-specific images and check native contexts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import register_sif, require_runtime
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ok, evidence_ref, file_hash, tree_hash, write_json

DOCKER_SHA = '4d95718efb6632bfa8f44e2807b1f53fe0e3ff75417420663260ef1db5092553'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--setup-patch', type=Path, help='Explicit environment patch metadata for the selected tasks')
    args = parser.parse_args()
    if len(args.task) != len(set(args.task)):
        raise ValueError('Duplicate task IDs')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_snapshot = out / Path(__file__).name
    shutil.copy2(__file__, source_snapshot)
    runtime = require_runtime('apptainer')
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    base = ROOT / 'artifacts/images/ade-software-v2/base.sif'
    base_pin = ROOT / 'artifacts/images/ade-software-v2/provenance.json'
    pin = json.loads(base_pin.read_text())
    assert file_hash(base) == pin['image_sha256']
    assert all(evidence_ok(ref, ROOT) for ref in pin['build_evidence'])
    results = []
    patch_path = args.setup_patch.resolve() if args.setup_patch else None
    patch = json.loads(patch_path.read_text()) if patch_path else None
    if patch and (patch['type'] != 'environment' or patch['native_verifier_modified']):
        raise ValueError('Only the reviewed setup environment repair is supported')

    def run(cmd, log, timeout=1200):
        with log.open('w') as handle:
            p = subprocess.run(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout)
        if p.returncode:
            raise RuntimeError(f'Exit {p.returncode}: {log.relative_to(ROOT)}')

    for key in args.task:
        row = rows[key]
        task = ROOT / row['run_path']
        env = task / 'environment'
        if row['source'] != 'ade' or tree_hash(task) != row['adapted_task_hash'] or file_hash(env / 'Dockerfile') != DOCKER_SHA:
            raise ValueError('Task outside reviewed native Docker scope: ' + key)
        db = (env / 'db_name.txt').read_text().strip()
        fid = (env / 'db_file_id.txt').read_text().strip()
        if db == 'airbnb':
            data_pin = ROOT / 'artifacts/images/ade-airbnb-data-pin.json'
            data = ROOT / 'artifacts/images/ade-airbnb.duckdb'
            dp = json.loads(data_pin.read_text())
            assert dp['source_file_id'] == fid and file_hash(data) == dp['sha256']
        else:
            data_pin = ROOT / f'artifacts/images/ade-additional-data-v1/{db}-pin.json'
            dp = json.loads(data_pin.read_text())
            data = ROOT / dp['file']['path']
            assert dp['file_id'] == fid and evidence_ok(dp['file'], ROOT)
        directory = out / key
        directory.mkdir()
        setup = env / 'setup.sh'
        patch_refs = []
        if patch:
            change = patch['tasks'][key]
            assert change['upstream_task_hash'] == row['adapted_task_hash']
            assert change['file'] == 'environment/setup.sh' and file_hash(setup) == change['before_sha256']
            assert evidence_ok(change['diff'], ROOT) and evidence_ok(change['reproduction'], ROOT)
            before = setup.read_text()
            assert before.count(change['before_text']) == 1
            after = before.replace(change['before_text'], change['after_text'], 1)
            assert hashlib.sha256(after.encode()).hexdigest() == change['after_sha256']
            setup = directory / 'setup-patched.sh'
            setup.write_text(after)
            patch_refs = [evidence_ref(patch_path, ROOT), evidence_ref(setup, ROOT), change['diff'], change['reproduction']]
        # Snapshot is made after native setup and before moving mutable folders.
        snapshot = directory / 'snapshot.py'
        snapshot.write_text('''import hashlib,json,os
from pathlib import Path
root=Path('/app');rows=[]
for p in sorted(root.rglob('*')):
    if p.is_symlink(): rows.append({'relative':str(p.relative_to(root)),'link':os.readlink(p)})
    elif p.is_file(): rows.append({'relative':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':p.stat().st_mode & 0o777})
Path('/opt/dbt-bench-initial-files.json').write_text(json.dumps(rows,indent=2)+'\\n')
''')
        definition = directory / 'image.def'
        files = [(env / 'project', '/opt/dbt-bench-task/project'), (data, f'/opt/dbt-bench-task/{db}.duckdb'),
                 (env / 'shared-scripts', '/opt/dbt-bench-task/shared-scripts'), (setup, '/opt/dbt-bench-task/setup.sh'),
                 (env / 'setup-data', '/opt/dbt-bench-task/setup'), (snapshot, '/opt/snapshot.py')]
        files += [(env / name, '/opt/dbt-bench-task/' + name) for name in ('CLAUDE.md', 'GEMINI.md', 'AGENTS.md', 'MACRO.md')]
        definition.write_text(f'Bootstrap: localimage\nFrom: {base}\n\n%files\n' +
            ''.join(f'    {src} {dest}\n' for src, dest in files) + '''
%post
    set -eu
    rm -rf /app /scripts
    mv /opt/dbt-bench-task/project /app
    mv /opt/dbt-bench-task/shared-scripts /scripts
    cp -a /opt/dbt-bench-task/. /app/
    rm -rf /opt/dbt-bench-task
    cd /app
    bash setup.sh --db-type=duckdb --project-type=dbt
    rm -f /app/setup.sh
    rm -rf /app/setup
    python3 /opt/snapshot.py
    rm /opt/snapshot.py
    mkdir -p /opt/dbt-bench-initial
    for folder in dbt_packages target logs; do
        if [ -d "/app/$folder" ]; then
            mv "/app/$folder" "/opt/dbt-bench-initial/$folder"
        fi
    done

%environment
    for folder in dbt_packages target logs; do
        if [ -d "/opt/dbt-bench-initial/$folder" ]; then
            cp -a "/opt/dbt-bench-initial/$folder" "/app/$folder"
        fi
    done
''')
        image = directory / 'task.sif'
        try:
            run([runtime['binary'], 'build', '--fakeroot', str(image), str(definition)], directory / 'build.log')
            hidden = []
            for section in ('solution', 'tests'):
                for p in sorted((task / section).rglob('*')):
                    if p.is_file() and p.stat().st_size:
                        hidden.append({'source': str(p.relative_to(task)), 'size': p.stat().st_size, 'sha256': file_hash(p)})
            write_json(directory / 'hidden-files.json', hidden)
            public_scripts = {str(p.relative_to(env / 'shared-scripts')): file_hash(p)
                              for p in (env / 'shared-scripts').rglob('*') if p.is_file()}
            write_json(directory / 'native-public-scripts.json', public_scripts)
            probe = directory / 'probe.py'
            probe.write_text('''import hashlib,json,os
from pathlib import Path
root=Path('/app');expected=json.loads(Path('/opt/dbt-bench-initial-files.json').read_text())
observed={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
assert observed == {r['relative'] for r in expected}, 'Initial workspace file set differs'
for r in expected:
    p=root/r['relative']
    if 'link' in r: assert p.is_symlink() and os.readlink(p)==r['link']
    else: assert not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==r['sha256'] and p.stat().st_mode & 0o777 == r['mode'], r['relative']
hidden=json.loads(Path('/review/hidden-files.json').read_text());public=json.loads(Path('/review/native-public-scripts.json').read_text());sizes={r['size'] for r in hidden};by_hash={}
for r in hidden:by_hash.setdefault(r['sha256'],[]).append(r['source'])
matches=[];unexplained=[]
for folder in ['/app','/scripts','/sage','/seeds','/solutions','/opt/dbt-bench-initial','/root']:
    for p in Path(folder).rglob('*'):
        if p.is_file() and p.stat().st_size in sizes:
            h=hashlib.sha256(p.read_bytes()).hexdigest()
            if h in by_hash:
                allowed=folder=='/scripts' and public.get(str(p.relative_to('/scripts')))==h and all('/scripts/shared/' in s for s in by_hash[h])
                match={'path':str(p),'hidden_sources':by_hash[h],'explicit_native_public_helper':allowed}
                matches.append(match)
                if not allowed:unexplained.append(match)
print(json.dumps({'initial_workspace_exact':True,'initial_files':expected,'exact_hidden_matches':matches,'unexplained_matches':unexplained,'scope':'Exact file scan; explicit native shared helper matches disclosed, not an information-flow proof'},indent=2))
assert not unexplained, 'Hidden asset matches require individual review'
''')
            inspection = subprocess.run([runtime['binary'], 'exec', '--fakeroot', '--writable-tmpfs', '--pid',
                '--no-mount', 'home,tmp,cwd,hostfs,bind-paths', '--bind', str(directory) + ':/review:ro',
                str(image), 'python3', '/review/probe.py'], capture_output=True, text=True, timeout=180)
            (directory / 'initial-visibility.log').write_text(inspection.stdout + inspection.stderr)
            if inspection.returncode:
                raise RuntimeError('Initial workspace/visibility check failed')
            write_json(directory / 'initial-visibility.json', json.loads(inspection.stdout))
            run([sys.executable, str(ROOT / 'scripts/check_harbor_apptainer.py'), '--task', str(task), '--image', str(image),
                 '--db', f'/app/{db}.duckdb', '--output-dir', str(directory / 'context')], directory / 'context-command.log', 900)
            assert json.loads((directory / 'context/result.json').read_text())['integration_passed']
            provenance = {'architecture': 'aarch64', 'native_task_hash': row['adapted_task_hash'],
                'dockerfile_sha256': DOCKER_SHA, 'image_sha256': file_hash(image), 'complete_native_build': True,
                'build_kind': 'reviewed_apptainer_definition',
                'environment_patch_ids': [patch['id']] if patch else [],
                'reviewed_differences': ['Reuse locked native ADE software and ARM64 node/yq adaptations; preinstall isolated Harbor server dependencies.',
                    'Native Google Drive ID input pinned by observed SHA256; no independently published data checksum supplied.',
                    'Run full native setup in a clean task project; fail if its final exit code fails instead of masking with cleanup.',
                    'Record generated dbt dependencies/build outputs in the image and initial file manifest; native setup may contact its declared package sources.',
                    'Copy package/target/log state into writable tmpfs once on startup; verify complete post-setup file state.',
                    'No Docker byte parity or actual Qwen execution claim.'] + ([patch['application_scope']] if patch else []),
                'build_evidence': [evidence_ref(p, ROOT) for p in (source_snapshot, base, base_pin, data_pin, data,
                    snapshot, definition, directory / 'build.log', probe, directory / 'hidden-files.json',
                    directory / 'native-public-scripts.json', directory / 'context/result.json')] + patch_refs,
                'agent_visibility_evidence': evidence_ref(directory / 'initial-visibility.json', ROOT),
                'scope': 'Native setup/build/environment readiness only; no semantic certification'}
            write_json(directory / 'provenance.json', provenance)
            binding = register_sif(ROOT, row, image, directory / 'provenance.json')
            results.append({'task_key': key, 'built_and_registered': True, 'binding': str(binding),
                            'provenance': evidence_ref(directory / 'provenance.json', ROOT)})
        except Exception as exc:
            results.append({'task_key': key, 'built_and_registered': False, 'error': f'{type(exc).__name__}: {exc}'})
        write_json(out / 'summary.json', {'tasks': results, 'qwen_smoke': False, 'benchmark_trials': 0})
        print(json.dumps(results[-1]), flush=True)
    return int(any(not r['built_and_registered'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())

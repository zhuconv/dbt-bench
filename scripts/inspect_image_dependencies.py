#!/usr/bin/env python3
"""Bind current images to actual dependency inventories without changing tasks."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess

from review_tools import ROOT, cached_hashes
from dbt_bench import io


def group_bindings(bindings):
    groups = {}
    seen = set()
    for binding in bindings:
        key = binding['task_key']
        if key in seen:
            raise ValueError('Duplicate task binding: ' + key)
        seen.add(key)
        image = binding['image']
        groups.setdefault(image['sha256'], []).append(binding)
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task', action='append', help='Limit to explicit task keys')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(__file__), out / 'inspector.py')
    shutil.copy2(ROOT / 'scripts/image_dependency_probe.py', out / 'probe.py')
    state = io.read_json(ROOT / 'artifacts/validation/phase2-review-v1/state.json')
    if not io.evidence_ok(state['inventory'], ROOT):
        raise ValueError('Changed phase2 inventory')
    inventory = io.read_json(ROOT / state['inventory']['path'])
    isolated = set(inventory['isolated_tasks'])
    paths = sorted((ROOT / 'catalog/backends/apptainer').glob('*.json'))
    bindings = [io.read_json(p) for p in paths]
    known = {b['task_key'] for b in bindings}
    requested = set(args.task or known - isolated)
    if requested - known or requested & isolated:
        raise ValueError('Unknown or isolated task requested')
    selected = [b for b in bindings if b['task_key'] in requested]
    runtime = '/opt/apps/tacc-apptainer/1.4.1/bin/apptainer'
    environment = dict(os.environ)
    for key in list(environment):
        if key in {'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME'} or key.startswith(('APPTAINERENV_', 'SINGULARITYENV_')):
            environment.pop(key)
    environment.update(PYTHONDONTWRITEBYTECODE='1', APPTAINERENV_PYTHONDONTWRITEBYTECODE='1')
    results = []
    with cached_hashes():
        for sha, group in group_bindings(selected).items():
            directory = out / sha
            directory.mkdir()
            result = {'image_sha256': sha, 'tasks': [b['task_key'] for b in group],
                      'bindings': [], 'passed': False}
            try:
                for b in group:
                    path = ROOT / 'catalog/backends/apptainer' / (b['task_key'] + '.json')
                    refs = [b['image'], *b['evidence']]
                    failed = [ref for ref in refs if not io.evidence_ok(ref, ROOT)]
                    result['bindings'].append({'task_key': b['task_key'],
                                               'binding': io.evidence_ref(path, ROOT),
                                               'evidence_count': len(refs), 'invalid': failed})
                    if failed:
                        raise ValueError('Changed or missing declared build evidence')
                image = ROOT / group[0]['image']['path']
                argv = [runtime, 'exec', '--fakeroot', '--writable-tmpfs', '--pid', '--cleanenv',
                        '--no-mount', 'home,tmp,cwd,hostfs,bind-paths', '--bind',
                        str(out) + ':/review:ro', str(image), 'python3', '/review/probe.py']
                process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, env=environment, start_new_session=True)
                timed_out = False
                try:
                    stdout, stderr = process.communicate(timeout=300)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGKILL)
                    stdout, stderr = process.communicate()
                (directory / 'stdout.json').write_text(stdout)
                (directory / 'stderr.log').write_text(stderr)
                result.update(command=argv, returncode=process.returncode, timed_out=timed_out,
                              stdout=io.evidence_ref(directory / 'stdout.json', ROOT),
                              stderr=io.evidence_ref(directory / 'stderr.log', ROOT))
                if process.returncode != 0:
                    raise ValueError('Dependency inventory process failed')
                probe = json.loads(stdout)
                if probe.get('schema_version') != 1 or not probe.get('python'):
                    raise ValueError('Incomplete dependency inventory')
                result['python_probe_failures'] = [p['argv'][0] for p in probe['python'] if p['returncode'] != 0]
                result['passed'] = not result['python_probe_failures']
                result['scope'] = 'Image/build references and inventory execution only; package-source provenance and cross-backend equivalence require separate review.'
            except Exception as exc:
                result['error'] = type(exc).__name__ + ': ' + str(exc)
            io.write_json(directory / 'result.json', result)
            results.append(io.evidence_ref(directory / 'result.json', ROOT))
            print(json.dumps({'tasks': result['tasks'], 'passed': result['passed']}), flush=True)
    completed = [io.read_json(ROOT / ref['path']) for ref in results]
    summary = {'schema_version': 1, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
               'tasks': sorted(requested), 'isolated_excluded': sorted(isolated),
               'unique_images': len(results), 'results': results,
               'passed_images': sum(r['passed'] for r in completed),
               'failed_images': sum(not r['passed'] for r in completed),
               'inspector': io.evidence_ref(out / 'inspector.py', ROOT),
               'probe': io.evidence_ref(out / 'probe.py', ROOT),
               'model_requests': 0, 'native_rewards_modified': False,
               'scope': 'Dependency inventory and declared artifact integrity; no task validation or source-rebuild equivalence certification.'}
    io.write_json(out / 'summary.json', summary)


if __name__ == '__main__':
    main()

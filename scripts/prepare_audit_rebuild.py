#!/usr/bin/env python3
"""Prepare an isolated Harbor batch for explicit clean submission reconstructions.

The base mechanical task and native verifier are immutable inputs. The request
contains only declared source files and observation declarations, not gold.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil

from dbt_bench import io
from dbt_bench.prepare import check_harbor
from review_tools import ROOT
from audit_rebuild_driver import validate_capture_hooks, validate_commands


def prepare(base_batch, requests_path, output, concurrent=2):
    base_batch, requests_path, output = map(Path, (base_batch, requests_path, output))
    if output.exists() or not str(output.resolve()).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH batch directory')
    requested = io.read_json(requests_path)
    if requested.get('model_requests') != 0:
        raise ValueError('No model requests allowed')
    manifest = io.read_json(base_batch / 'manifest.json')
    config_ref = manifest['config']
    if not io.evidence_ok(config_ref, ROOT):
        raise ValueError('Base config changed')
    config = io.read_json(ROOT / config_ref['path'])
    if not requested.get('tasks'):
        raise ValueError('No requested tasks')
    attempts = requested.get('n_attempts', 2)
    if type(attempts) is not int or attempts not in (1, 2):
        raise ValueError('Declare one case execution or two independent repeat initializations')
    labels = [entry['label'] for entry in requested['tasks']]
    if len(labels) != len(set(labels)) or any(not label or '/' in label or '..' in label for label in labels):
        raise ValueError('Unsafe/duplicate task labels')
    for entry in requested['tasks']:
        validate_commands(entry['rebuild'])
        validate_capture_hooks(entry['rebuild'])
    output.mkdir(parents=True)
    shutil.copyfile(Path(__file__), output / 'preparation-source.py')
    shutil.copytree(base_batch / 'tools', output / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('audit_rebuild_agent.py', 'native_mechanical_agent.py', 'mechanical_observer.py', 'audit_relation_inventory.py'):
        shutil.copyfile(ROOT / 'scripts' / name, output / 'tools' / name)
    if len(config['agents']) != 1 or config['agents'][0].get('import_path') != 'native_mechanical_agent:NativeMechanicalAgent':
        raise ValueError('Base must use the locked no-model mechanical adapter')
    config['agents'][0]['import_path'] = 'audit_rebuild_agent:AuditRebuildAgent'
    config.update(job_name=output.name, jobs_dir=str(output / 'jobs'), n_attempts=attempts,
                  n_concurrent_trials=concurrent, tasks=[])
    entries = {}
    for entry in requested['tasks']:
        key, label = entry['task_key'], entry['label']
        original = base_batch / 'tasks' / key
        base = manifest['tasks'][key]
        if io.tree_hash(original) != base['validation_copy_hash']:
            raise ValueError('Base task changed: ' + key)
        target = output / 'tasks' / label
        shutil.copytree(original, target)
        payload = target / 'audit-rebuild'
        payload.mkdir()
        request = deepcopy(entry['rebuild'])
        validate_capture_hooks(request)
        if request['task_key'] != key:
            raise ValueError('Rebuild request task mismatch')
        source_refs = []
        for index, file in enumerate(request['submission']['files']):
            ref = file.pop('source')
            if not io.evidence_ok(ref, ROOT):
                raise ValueError('Submitted source changed')
            source_refs.append(ref)
            name = f'sources/{index:04d}'
            dest = payload / name
            dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / ref['path'], dest)
            file.update(payload_path=name, sha256=ref['sha256'])
        for index, hook in enumerate(request.get('capture_hooks', [])):
            ref = hook.pop('source')
            if not io.evidence_ok(ref, ROOT):
                raise ValueError('Capture hook source changed')
            name = f'capture-hook-{index:02d}.py'
            shutil.copyfile(ROOT / ref['path'], payload / name)
            hook.update(payload_path=name, sha256=ref['sha256'])
        shutil.copyfile(ROOT / 'scripts/audit_rebuild_driver.py', payload / 'audit_rebuild_driver.py')
        io.write_json(payload / 'request.json', request)
        spec_path = target / 'mechanical/spec.json'
        spec = io.read_json(spec_path)
        spec.update(entry['observation'])
        if spec['task_key'] != key:
            raise ValueError('Observer task changed')
        spec['observation_sql_types'] = True
        io.write_json(spec_path, spec)
        shutil.copyfile(ROOT / 'scripts/mechanical_observer.py', target / 'mechanical/mechanical_observer.py')
        shutil.copyfile(ROOT / 'scripts/audit_relation_inventory.py', target / 'mechanical/audit_relation_inventory.py')
        native = target / 'tests/mechanical-native-test.sh'
        if io.file_hash(native) != base['native_verifier']['sha256']:
            raise ValueError('Native verifier changed')
        entries[label] = {'task_key': key, 'base_task': base, 'submission_source_refs': source_refs,
            'request': io.evidence_ref(payload / 'request.json', ROOT),
            'validation_copy_hash': io.tree_hash(target), 'native_verifier': io.evidence_ref(native, ROOT),
            'kind': 'audit_clean_reconstruction', 'model_requests': 0}
        config['tasks'].append({'path': str(target)})
    io.write_json(output / 'config.yaml', check_harbor(config, [Path(t['path']) for t in config['tasks']]))
    tools = [io.evidence_ref(p, ROOT) for p in (output / 'tools').glob('*.py')]
    io.write_json(output / 'manifest.json', {'schema_version': 1, 'model_requests': 0,
        'kind': 'audit_clean_reconstruction', 'base_batch': io.evidence_ref(base_batch / 'manifest.json', ROOT),
        'requests': io.evidence_ref(requests_path, ROOT), 'config': io.evidence_ref(output / 'config.yaml', ROOT),
        'tools': tools, 'tasks': entries, 'planned_trials': attempts * len(entries),
        'preparation_code': io.evidence_ref(output / 'preparation-source.py', ROOT),
        'scope': 'Explicit one or two fresh Harbor initializations per submission; no model or solution upload; native verifier preserved'})
    return {'tasks': len(entries), 'trials': attempts * len(entries), 'config': str(output / 'config.yaml')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-batch', type=Path, required=True)
    parser.add_argument('--requests', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--concurrent', type=int, default=2)
    args = parser.parse_args()
    print(prepare(args.base_batch, args.requests, args.output, args.concurrent))

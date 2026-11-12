#!/usr/bin/env python3
"""Account for actual Harbor reconstruction trials without changing rewards."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
from dbt_bench import io
from mechanical_compare import compare_snapshots
from review_tools import ROOT


def reconcile(batch):
    batch = Path(batch).resolve()
    manifest = io.read_json(batch / 'manifest.json')
    if manifest.get('kind') != 'audit_clean_reconstruction' or manifest.get('model_requests') != 0:
        raise ValueError('Not a no-model audit reconstruction batch')
    if not io.evidence_ok(manifest['config'], ROOT):
        raise ValueError('Configuration changed')
    config = io.read_json(ROOT / manifest['config']['path'])
    planned = {str(Path(t['path']).resolve()): config['n_attempts'] for t in config['tasks']}
    trials, seen, grouped = [], set(), defaultdict(list)
    errors = []
    job = Path(config['jobs_dir']) / config['job_name']
    for result in sorted(job.glob('*/result.json')):
        raw = io.read_json(result)
        if raw['id'] in seen:
            raise ValueError('Duplicate trial ID: ' + raw['id'])
        seen.add(raw['id'])
        path = str(Path(raw['config']['task']['path']).resolve())
        if path not in planned:
            raise ValueError('Unexpected trial task: ' + path)
        label = Path(path).name
        if label not in manifest['tasks']:
            raise ValueError('Unknown task label: ' + label)
        entry = manifest['tasks'][label]
        request = io.read_json(ROOT / entry['request']['path'])
        trial = result.parent
        execution = trial / 'agent/audit-rebuild/execution.json'
        item = {'trial_id': raw['id'], 'task_key': entry['task_key'], 'label': label,
            'result': io.evidence_ref(result, ROOT), 'finished_at': raw.get('finished_at'),
            'native_rewards': (raw.get('verifier_result') or {}).get('rewards'),
            'exception_info': raw.get('exception_info'), 'audit_reward': None,
            'scope': 'Execution/capture reconciliation only; supplemental checker has not been scored here'}
        if not io.evidence_ok(entry['native_verifier'], ROOT):
            raise ValueError('Native verifier changed')
        if execution.exists():
            run = io.read_json(execution)
            item['execution'] = io.evidence_ref(execution, ROOT)
            item['build_success'] = run.get('build_success')
            payload = Path(path) / 'audit-rebuild'
            if run.get('spec_sha256') != entry['request']['sha256'] or run.get('driver_sha256') != io.file_hash(payload / 'audit_rebuild_driver.py'):
                errors.append(label + ': executed spec/driver identity mismatch')
            if run.get('model_requests') != 0 or run.get('task_key') != entry['task_key']:
                errors.append(label + ': execution scope mismatch')
            initial_trees, final_trees = run.get('initial_trees'), run.get('final_trees')
            item['declared_dependency_trees_unchanged'] = initial_trees == final_trees if initial_trees is not None and final_trees is not None else None
            # deps is an explicitly recorded native initialization step, so
            # initial-absent/final-present is not silently called unchanged.
            item['dependency_setup_commands'] = [v for v in request['commands'] if v[1] == 'deps']
        elif request.get('mode') == 'nop':
            item['build_skipped'] = 'Actual nop: no submission install, artifact clearing or dbt execution'
        else:
            item['capture_missing'] = ['agent/audit-rebuild/execution.json']
        item['relation_inventories'] = [io.evidence_ref(p, ROOT) for p in
            (trial / 'agent/audit-initial-relations.json', trial / 'agent/audit-submission-relations.json') if p.is_file()]
        submission = trial / 'agent/audit-submission/snapshot.json'
        before = trial / 'verifier/mechanical-before/snapshot.json'
        after = trial / 'verifier/mechanical-after/snapshot.json'
        item['snapshots'] = {name: io.evidence_ref(p, ROOT) if p.is_file() else None
                              for name, p in [('submission', submission), ('verifier_before', before), ('verifier_after', after)]}
        for name, left, right in [('producer_to_verifier', submission, before), ('native_checker_output_changes', before, after)]:
            if left.is_file() and right.is_file():
                item[name] = compare_snapshots(left, right)
            else:
                item[name] = {'equal': None, 'reason': 'Required snapshot unavailable'}
        # Keep all declared capture files as immutable evidence; never export
        # a workspace or full database in this reconciliation step.
        item['capture_artifacts'] = [io.evidence_ref(p, ROOT) for directory in
            (trial / 'agent/audit-rebuild', trial / 'agent/audit-submission',
             trial / 'verifier/mechanical-before', trial / 'verifier/mechanical-after')
            if directory.is_dir() for p in sorted(directory.rglob('*')) if p.is_file()]
        trials.append(item)
        grouped[path].append(item)
    counts = Counter(str(Path(io.read_json(ROOT / t['result']['path'])['config']['task']['path']).resolve()) for t in trials)
    missing = {path: n - counts[path] for path, n in planned.items() if counts[path] < n}
    excess = {path: counts[path] - n for path, n in planned.items() if counts[path] > n}
    repeats = []
    for path, values in grouped.items():
        snapshots = [v['snapshots']['submission'] for v in values]
        if len(snapshots) == 2 and all(snapshots):
            repeats.append({'label': Path(path).name, 'comparison': compare_snapshots(
                ROOT / snapshots[0]['path'], ROOT / snapshots[1]['path'])})
    job_result = io.read_json(job / 'result.json') if (job / 'result.json').exists() else {}
    return {'schema_version': 1, 'batch': batch.name, 'manifest': io.evidence_ref(batch / 'manifest.json', ROOT),
        'planned_trials': sum(planned.values()), 'observed_trials': len(trials), 'missing': missing,
        'excess': excess, 'errors': errors,
        'complete': bool(job_result.get('finished_at')) and not missing and not excess and not errors and all(t['finished_at'] for t in trials),
        'trials': trials, 'repeat_comparisons': repeats, 'model_requests': 0,
        'native_rewards_modified': False, 'audit_certified': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists(): raise ValueError('Refuse to overwrite reconciliation')
    result = reconcile(args.batch); io.write_json(args.output, result)
    print({k: result[k] for k in ('complete', 'observed_trials', 'planned_trials', 'missing', 'excess', 'errors')})

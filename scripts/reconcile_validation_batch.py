#!/usr/bin/env python3
"""Reconcile an explicitly planned reference/nop batch without assigning certification."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.io import evidence_ok, evidence_ref, read_json, tree_hash, write_json


def artifact_directory(path):
    path = Path(path)
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def reconcile(manifest_path, output):
    manifest = read_json(manifest_path)
    errors = []
    if not evidence_ok(manifest['config'], ROOT):
        raise ValueError('Pre-run config hash changed')
    config_path = ROOT / manifest['config']['path']
    config = read_json(config_path)
    job = Path(config['jobs_dir']) / config['job_name']
    if config['retry']['max_retries'] != 0:
        raise ValueError('This reference-batch reconciliation requires retry=0; use explicit bindings for retries')
    if {a['name'] for a in config['agents']} - {'oracle', 'nop'}:
        raise ValueError('Model rollouts require the experiment/trial binding reporting path')
    planned = Counter((r['task_key'], r['agent']) for r in manifest['planned'])
    if len({(r['task_key'], r['agent'], r['attempt']) for r in manifest['planned']}) != len(manifest['planned']):
        raise ValueError('Duplicate pre-run attempt IDs')
    paths = {str(Path(t['path']).resolve()): Path(t['path']).name for t in config['tasks']}
    expected = Counter({(key, agent['name']): config['n_attempts'] for key in paths.values() for agent in config['agents']})
    if expected != planned:
        raise ValueError('Manifest planned attempts disagree with native Harbor config')
    for path, key in paths.items():
        if tree_hash(path) != manifest['tasks'][key]['validation_copy_hash']:
            errors.append({'kind': 'task_content_changed', 'task_key': key})
    observed = Counter()
    groups = defaultdict(list)
    trials = []
    seen = set()
    for path in sorted(job.glob('*/result.json')):
        native = read_json(path)
        trial_id = native['id']
        if trial_id in seen:
            errors.append({'kind': 'duplicate_trial_id', 'trial_id': trial_id, 'result': str(path)})
        seen.add(trial_id)
        task_path = str(Path(native['config']['task']['path']).resolve())
        key = paths.get(task_path)
        agent = native['config']['agent']['name']
        if key is None or (key, agent) not in planned:
            errors.append({'kind': 'unplanned_trial', 'trial_id': trial_id, 'task_path': task_path, 'agent': agent})
        else:
            observed[key, agent] += 1
        rewards = (native.get('verifier_result') or {}).get('rewards')
        record = {'trial_id': trial_id, 'trial_name': native['trial_name'], 'task_key': key,
                  'agent': agent, 'native_rewards': rewards, 'exception_info': native.get('exception_info'),
                  'agent_result': native.get('agent_result'), 'verifier_environment_mode': native.get('verifier_environment_mode'),
                  'started_at': native.get('started_at'), 'finished_at': native.get('finished_at'),
                  'result': evidence_ref(path, ROOT), 'raw_directory': artifact_directory(path.parent),
                  'attempt': None, 'retry': None,
                  'attempt_note': 'Native result has no ordinal; compare the multiset of independently initialized trials with planned repeats. No guessed attempt/retry IDs.',
                  'audit_reward': None, 'root_cause': None}
        trials.append(record)
        groups[key, agent].append(record)
    coverage = []
    for key, agent in sorted(set(planned) | set(observed)):
        runs = groups[key, agent]
        n, expected_n = observed[key, agent], planned[key, agent]
        coverage.append({'task_key': key, 'agent': agent, 'planned': expected_n, 'observed': n,
                         'missing': max(expected_n-n, 0), 'extra': max(n-expected_n, 0),
                         'native_reward_one': sum(r['native_rewards'] == {'reward': 1.0} for r in runs),
                         'native_reward_zero': sum(r['native_rewards'] == {'reward': 0.0} for r in runs),
                         'reward_missing': sum(r['native_rewards'] is None for r in runs),
                         'exceptions': sum(r['exception_info'] is not None for r in runs),
                         'trial_ids': [r['trial_id'] for r in runs]})
        if n != expected_n:
            errors.append({'kind': 'coverage_mismatch', 'task_key': key, 'agent': agent, 'planned': expected_n, 'observed': n})
    job_result = read_json(job / 'result.json')
    if len(trials) != job_result['stats']['n_completed_trials']:
        errors.append({'kind': 'job_trial_count_disagrees', 'observed': len(trials), 'native_completed': job_result['stats']['n_completed_trials']})
    complete = not errors and bool(job_result.get('finished_at'))
    result = {'schema_version': 1, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'manifest': evidence_ref(manifest_path, ROOT), 'job_result': evidence_ref(job / 'result.json', ROOT),
              'planned_trials': sum(planned.values()), 'observed_trials': len(trials),
              'coverage_complete': complete, 'errors': errors, 'coverage': coverage, 'trials': trials,
              'qwen_smoke': False, 'automatic_certification': False,
              'scope': 'Raw reference/nop outcomes only. Equal rewards across repeats do not establish output stability or independent semantics.'}
    write_json(output, result)
    print({'planned': result['planned_trials'], 'observed': len(trials), 'complete': complete, 'errors': len(errors), 'output': str(output)})
    return 0 if complete else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(reconcile(args.manifest, args.output))

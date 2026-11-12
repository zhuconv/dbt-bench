#!/usr/bin/env python3
"""Summarize registered audit executions without interpreting or rewriting scores."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from dbt_bench import io
from review_tools import ROOT


def checked(ref, root):
    if not io.evidence_ok(ref, root):
        raise ValueError('Changed or unavailable execution evidence: ' + str(ref))
    return io.read_json(Path(root) / ref['path'])


def summarize(paths, root):
    batches, trials, ids, names = [], [], set(), set()
    for path in paths:
        path = Path(path)
        report = io.read_json(path)
        if (not report['complete'] or report['missing'] or report['excess'] or report['errors']
                or report['model_requests'] != 0):
            raise ValueError('Only complete reconciled no-model batches can be summarized: ' + str(path))
        manifest = checked(report['manifest'], root)
        if manifest.get('kind') != 'audit_clean_reconstruction' or manifest.get('model_requests') != 0:
            raise ValueError('Unexpected execution scope')
        name = report['batch']
        if name in names:
            raise ValueError('Duplicate batch: ' + name)
        names.add(name)
        if report['planned_trials'] != report['observed_trials'] or report['observed_trials'] != len(report['trials']):
            raise ValueError('Trial accounting differs from reconciliation')
        for item in report['trials']:
            raw = checked(item['result'], root)
            trial_id = raw['id']
            if trial_id != item['trial_id'] or trial_id in ids:
                raise ValueError('Duplicate or mismatched trial ID: ' + trial_id)
            ids.add(trial_id)
            label = Path(raw['config']['task']['path']).name
            if label != item['label'] or manifest['tasks'][label]['task_key'] != item['task_key']:
                raise ValueError('Trial task differs from manifest')
            rewards = (raw.get('verifier_result') or {}).get('rewards')
            if (not raw.get('finished_at') or rewards != item['native_rewards']
                    or raw.get('exception_info') != item['exception_info']):
                raise ValueError('Raw trial outcome differs from reconciliation')
            execution = checked(item['execution'], root) if item.get('execution') else None
            if execution is not None:
                if (execution.get('task_key') != item['task_key'] or execution.get('model_requests') != 0
                        or execution.get('build_success') != item.get('build_success')):
                    raise ValueError('Producer outcome differs from reconciliation')
            trials.append({
                **{key: item.get(key) for key in ('trial_id', 'task_key', 'label', 'result', 'execution',
                    'build_success', 'build_skipped', 'capture_missing')},
                'native_rewards': rewards, 'exception_info': raw.get('exception_info'),
                'reconstruction_error': execution.get('error') if execution else None,
            })
        batches.append({'batch': name, 'planned': report['planned_trials'],
                        'observed': report['observed_trials'], 'reconciliation': io.evidence_ref(path, root)})
    return {
        'schema_version': 1, 'batches': batches, 'trials': trials,
        'planned_trials': sum(b['planned'] for b in batches), 'observed_trials': len(trials),
        'harbor_exception_count': sum(t['exception_info'] is not None for t in trials),
        'reconstruction_error_count': sum(t['reconstruction_error'] is not None for t in trials),
        'build_failure_count': sum(t['build_success'] is False for t in trials),
        'model_requests': 0, 'native_rewards_modified': False, 'audit_certified': False,
        'scope': ('Registered terminal reconstruction/workflow batches only. Native rewards, Harbor exceptions, '
                  'producer failures and actual nop are separate. Expected negative builds can fail; '
                  'these counts do not infer infrastructure retries, semantic validity or model success.'),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review', type=Path, required=True, help='Review directory containing root-owned state.json')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError('Refuse to overwrite an execution summary')
    review = a.review.resolve()
    state = io.read_json(review / 'state.json')
    paths = []
    for name in state['completed_rebuild_batches']:
        if Path(name).name != name or name in {'.', '..'}:
            raise ValueError('Invalid registered batch name')
        paths.append(review.parent / name / 'reconciliation-v1.json')
    result = summarize(paths, ROOT)
    result.update(recorded_at_utc=datetime.now(timezone.utc).isoformat(),
                  state_at_read=io.evidence_ref(review / 'state.json', ROOT),
                  checker=io.evidence_ref(Path(__file__), ROOT))
    # Archive the state bytes: later staging/publication updates must not invalidate this report.
    state_copy = a.output.with_suffix('.state.json')
    if state_copy.exists():
        raise ValueError('Refuse to overwrite archived state')
    io.write_json(state_copy, state)
    result['state_at_read'] = io.evidence_ref(state_copy, ROOT)
    io.write_json(a.output, result)
    print({k: result[k] for k in ('observed_trials', 'harbor_exception_count',
                                'reconstruction_error_count', 'build_failure_count')})


if __name__ == '__main__':
    main()

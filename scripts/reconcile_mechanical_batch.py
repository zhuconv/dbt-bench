#!/usr/bin/env python3
"""Reconcile actual native/Qwen-shell mechanical evidence without changing scores."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
from review_tools import ROOT, cached_hashes
from dbt_bench import io
from mechanical_compare import compare_snapshots


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    manifest = io.read_json(a.batch / 'manifest.json')
    assert io.evidence_ok(manifest['config'], ROOT)
    config = io.read_json(ROOT / manifest['config']['path'])
    job = Path(config['jobs_dir']) / config['job_name']
    job_result = io.read_json(job / 'result.json')
    assert job_result['finished_at'], 'Batch still running'
    groups = defaultdict(list)
    ids = set()
    trials = []
    task_paths = {str(Path(t['path']).resolve()): Path(t['path']).name for t in config['tasks']}
    for path in sorted(job.glob('*/result.json')):
        raw = io.read_json(path)
        key = task_paths[str(Path(raw['config']['task']['path']).resolve())]
        assert key in manifest['tasks'] and raw['finished_at'] and raw['id'] not in ids
        ids.add(raw['id'])
        spec = io.read_json(ROOT / manifest['tasks'][key]['normalized_specification']['path'])
        row = {'task_key': key, 'native_task_name': raw['task_name'], 'trial_id': raw['id'], 'result': io.evidence_ref(path, ROOT),
            'native_reward': (raw.get('verifier_result') or {}).get('rewards'),
            'exception_info': raw.get('exception_info'), 'audit_reward': None, 'model_requests': 0,
            'reference_kind': spec['reference_kind'], 'snapshots': {}, 'shell_results': {}}
        for name, subpath in {'initial': 'agent/initial/snapshot.json', 'submission': 'agent/submission/snapshot.json',
            'verifier_before': 'verifier/mechanical-before/snapshot.json', 'verifier_after': 'verifier/mechanical-after/snapshot.json'}.items():
            f = path.parent / subpath
            row['snapshots'][name] = io.evidence_ref(f, ROOT) if f.exists() else None
        for name in ('context-pipe', 'context-pty', 'visibility', 'initial', 'reference', 'reference-build', 'submission'):
            f = path.parent / 'agent' / (name + '-shell.json')
            row['shell_results'][name] = {'evidence': io.evidence_ref(f, ROOT), 'exit_code': io.read_json(f)['exit_code']} if f.exists() else None
        ref_shell = path.parent / 'agent/reference-shell.json'
        clock = io.read_json(ref_shell).get('started_at_utc') if ref_shell.exists() else None
        def window(snapshot):
            value = io.read_json(ROOT / snapshot['path']) if snapshot else {}
            return [clock, value.get('finished_at_utc')] if clock and value.get('finished_at_utc') else None
        row['reference_window'] = window(row['snapshots']['submission'])
        for label, first, second in [('producer_to_verifier', 'submission', 'verifier_before'),
                                     ('verifier_effects', 'verifier_before', 'verifier_after')]:
            l, r = row['snapshots'][first], row['snapshots'][second]
            row[label] = compare_snapshots(ROOT / l['path'], ROOT / r['path'], [window(l), window(r)] if window(l) and window(r) else None) if l and r else {'equal': None, 'reason': 'Actual observer did not produce both snapshots'}
        # Evidence includes every typed output, not just a scalar success flag.
        row['artifacts'] = [io.evidence_ref(f, ROOT) for directory in ('agent', 'verifier')
            for f in sorted((path.parent / directory).rglob('*')) if f.is_file()]
        groups[key].append(row)
        trials.append(row)
    counts = Counter(r['task_key'] for r in trials)
    assert counts == {key: 2 for key in manifest['tasks']}, {'expected_two_each': len(manifest['tasks']), 'actual': counts}
    tasks = []
    with cached_hashes():
        for key, rows in sorted(groups.items()):
            entry = manifest['tasks'][key]
            task = next(Path(t['path']) for t in config['tasks'] if Path(t['path']).name == key)
            assert io.tree_hash(task) == entry['validation_copy_hash']
            assert all(io.evidence_ok(entry[n], ROOT) for n in ('native_verifier', 'wrapper', 'normalized_specification', 'visibility_specification'))
            snapshots = [r['snapshots']['submission'] for r in rows]
            stability = (compare_snapshots(*(ROOT / r['path'] for r in snapshots),
                            reference_windows=[r['reference_window'] for r in rows] if all(r['reference_window'] for r in rows) else None)
                         if all(snapshots) else {'equal': None, 'reason': 'Actual execution failed before required snapshots'})
            tasks.append({'task_key': key, 'trials': [r['trial_id'] for r in rows], 'stability': stability,
                          'reference_kind': entry['reference_kind'], 'native_rewards': [r['native_reward'] for r in rows],
                          'scope': 'Actual observed values and execution only; final protocol judgment requires the task contract and visibility review.'})
    io.write_json(a.output, {'schema_version': 1, 'execution_backed': True,
        'manifest': io.evidence_ref(a.batch / 'manifest.json', ROOT), 'job_result': io.evidence_ref(job / 'result.json', ROOT),
        'planned_trials': manifest['planned_trials'], 'observed_trials': len(trials), 'duplicates': [], 'missing': [],
        'tasks': tasks, 'trials': trials, 'model_requests': 0, 'native_rewards_modified': False,
        'scope': 'Reconciliation and exact typed snapshots; no automatic task certification.', 'checker': io.evidence_ref(Path(__file__), ROOT)})
    print({'tasks': len(tasks), 'trials': len(trials), 'stable': dict(Counter(str(t['stability']['equal']) for t in tasks)),
           'exceptions': sum(bool(r['exception_info']) for r in trials)})


if __name__ == '__main__':
    main()

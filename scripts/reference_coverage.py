#!/usr/bin/env python3
"""Reconcile current-image reference/nop coverage for every catalog candidate.

Select the most recent complete 2-oracle/2-nop set with matching task and
backend binding identity, never by reward. Older sets remain referenced.
This is reference coverage, not a model baseline or semantic certification.
"""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ok, evidence_ref, file_hash, read_json, tree_hash, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, action='append', required=True, help='Completed reconciliation.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    candidates = defaultdict(list)

    def add(key, task_hash, binding, result_refs, source):
        if key not in rows:
            raise ValueError('Unknown task: ' + key)
        native = []
        for ref in result_refs:
            if not evidence_ok(ref, ROOT):
                raise ValueError('Missing/changed raw result: ' + str(ref))
            r = read_json(ROOT / ref['path'])
            native.append({'trial_id': r['id'], 'agent': r['config']['agent']['name'],
                'finished_at': r['finished_at'], 'native_rewards': (r.get('verifier_result') or {}).get('rewards'),
                'exception_info': r.get('exception_info'), 'result': ref})
        if Counter(r['agent'] for r in native) != {'oracle': 2, 'nop': 2}:
            raise ValueError('Expected two independent references and two nops: ' + key)
        if len({r['trial_id'] for r in native}) != 4 or any(not r['finished_at'] for r in native):
            raise ValueError('Duplicate/incomplete reference trials: ' + key)
        current_path = ROOT / 'catalog/backends/apptainer' / (key + '.json')
        current = read_json(current_path)
        current_identity = {'binding_sha256': file_hash(current_path),
                            'image_sha256': current['image']['sha256'], 'task_sha256': current['run_hash']}
        matched = task_hash == rows[key]['adapted_task_hash'] and all(binding.get(k) == v for k, v in current_identity.items())
        candidates[key].append({'identity_matches_current_binding': matched, 'binding': binding,
            'task_hash': task_hash, 'source': source, 'finished_at': max(r['finished_at'] for r in native),
            'trials': native})

    for path in args.batch:
        reconciliation = read_json(path)
        if not reconciliation['coverage_complete'] or reconciliation['errors']:
            raise ValueError('Incomplete batch reconciliation: ' + str(path))
        for ref in (reconciliation['manifest'], reconciliation['job_result']):
            if not evidence_ok(ref, ROOT):
                raise ValueError('Batch evidence changed: ' + str(ref))
        manifest = read_json(ROOT / reconciliation['manifest']['path'])
        if not evidence_ok(manifest['config'], ROOT):
            raise ValueError('Batch config changed')
        grouped = defaultdict(list)
        for trial in reconciliation['trials']:
            grouped[trial['task_key']].append(trial['result'])
        for key, refs in grouped.items():
            task = manifest['tasks'][key]
            add(key, task.get('original_task_hash', task.get('adapted_task_hash')),
                task.get('backend_binding', task.get('backend')), refs, evidence_ref(path, ROOT))

    for path in sorted((ROOT / 'artifacts/validation').glob('*/reference-nop-*/execution.json')):
        m = read_json(path)
        if m.get('backend') != 'apptainer' or m.get('case') is not None:
            continue
        if not m.get('completed'):
            continue  # Retained separately; cannot form a complete four-trial set.
        config_path = path.parent / 'config.yaml'
        cfg = read_json(config_path)
        if cfg['retry']['max_retries'] != 0 or cfg['n_attempts'] != 2:
            raise ValueError('Unexpected individual validation plan: ' + str(path))
        if tree_hash(cfg['tasks'][0]['path']) != m['validation_copy_hash']:
            raise ValueError('Individual validation copy changed: ' + str(path))
        if not all(evidence_ok(ref, ROOT) for ref in m['evidence']):
            raise ValueError('Individual execution evidence changed: ' + str(path))
        add(m['task_key'], m['original_task_hash'], m['backend_binding'],
            [t['result'] for t in m['trials']], evidence_ref(path, ROOT))

    coverage, missing, selected_ids = [], [], set()
    for key in sorted(rows):
        sets = candidates[key]
        matching = [s for s in sets if s['identity_matches_current_binding']]
        selected = max(matching, key=lambda s: (s['finished_at'], s['source']['path'])) if matching else None
        if selected is None:
            missing.append(key)
        else:
            ids = {t['trial_id'] for t in selected['trials']}
            if selected_ids & ids:
                raise ValueError('A trial is assigned to multiple catalog tasks')
            selected_ids.update(ids)
        coverage.append({'task_key': key, 'source': rows[key]['source'], 'selected': selected,
                         'other_sets': [s for s in sets if s is not selected],
                         'semantic_certification': False})
    selected_trials = [t for row in coverage if row['selected'] for t in row['selected']['trials']]
    counts = Counter((t['agent'], str(t['native_rewards']), t['exception_info'] is not None) for t in selected_trials)
    result = {'schema_version': 1, 'candidate_count': len(rows), 'covered_tasks': len(rows) - len(missing),
        'planned_trials': 4 * len(rows), 'selected_trials': len(selected_trials), 'missing_tasks': missing,
        'coverage_complete': not missing, 'selection_policy': 'latest complete set matching exact task hash and backend binding hash; rewards and exceptions never used for selection',
        'raw_outcomes': [{'agent': agent, 'rewards_repr': reward, 'has_exception': exception, 'count': n}
                         for (agent, reward, exception), n in sorted(counts.items())],
        'coverage': coverage, 'qwen_smoke': False, 'model_baseline': False,
        'scope': 'Reference/nop execution coverage only. Does not certify independent semantics, output stability, task solvability or trace readiness.'}
    write_json(args.output, result)
    print({k: result[k] for k in ('candidate_count', 'covered_tasks', 'selected_trials', 'coverage_complete', 'missing_tasks', 'raw_outcomes')})
    return int(bool(missing))


if __name__ == '__main__':
    raise SystemExit(main())

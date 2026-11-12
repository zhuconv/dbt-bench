"""Reconcile declared original-data cases, preserving native results and proofs."""
from collections import Counter
from pathlib import Path
import argparse
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.io import read_json, write_json, evidence_ref, file_hash, tree_hash

spec = importlib.util.spec_from_file_location('independent', Path(__file__).with_name('independent.py'))
independent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(independent)


def reconcile(batch):
    manifest = read_json(batch / 'manifest.json')
    expected = {p['task_label']: p for p in manifest['planned']}
    trials = []
    seen = set()
    for path in sorted((batch / 'jobs').glob('*/*/result.json')):
        result = read_json(path)
        label = Path(result['config']['task']['path']).name
        if label not in expected or label in seen:
            raise ValueError('Unknown or duplicate case trial: ' + label)
        seen.add(label)
        task = manifest['tasks'][label]
        copied = batch / 'tasks' / label
        assert tree_hash(copied) == task['validation_copy_hash']
        proof_path = path.parent / 'agent/case-proof.json'
        proof = read_json(proof_path) if proof_path.is_file() else None
        snapshots = {}
        if proof:
            assert proof['case'] == expected[label]['case_mode']
            assert not proof['tested_checker_invoked_to_define_validity']
            for when in ['before', 'after']:
                snapshot_path = path.parent / 'agent' / ('consolidate-' + when) / 'snapshot.json'
                recorded = next(r for r in proof['submission_artifacts'] if r['path'].endswith('consolidate-' + when + '/snapshot.json'))
                assert file_hash(snapshot_path) == recorded['sha256']
                snapshot = read_json(snapshot_path)
                for relative, ref in snapshot['source_files'].items():
                    assert file_hash(snapshot_path.parent / 'submission' / relative) == ref['sha256']
                snapshots[when] = evidence_ref(snapshot_path, ROOT)
            public = {}
            for source, (filename, _) in independent.SOURCES.items():
                original = ROOT / 'tasks/deb__dbt-consolidate/environment/data' / filename
                saved = path.parent / 'agent' / ('public-' + filename)
                assert file_hash(saved) == file_hash(original)
                public[source] = evidence_ref(saved, ROOT)
            wanted = independent.derive(independent.read_inputs(ROOT / 'tasks/deb__dbt-consolidate/environment/data'))
            before = read_json(ROOT / snapshots['before']['path'])
            after = read_json(ROOT / snapshots['after']['path'])
            assert all(independent.compare(wanted[k], before['tables'][k]['rows'], k == 'unified')['equal'] for k in independent.TABLES)
            actual = {k: independent.compare(wanted[k], after['tables'][k]['rows'], k == 'unified') for k in independent.TABLES}
            assert actual == proof['facts']['comparisons']
        rewards = (result.get('verifier_result') or {}).get('rewards')
        kind = read_json(ROOT / task['case']['path'])['kind']
        expected_validity = {'witness':'built_from_inputs', 'equivalent':'legal_equivalent', 'negative':'business_error'}[kind]
        # Absence of proof/reward or execution failure is never semantic detection.
        valid = bool(proof and proof['validity'] == expected_validity and result.get('exception_info') is None)
        outcome = 'unresolved_execution'
        if valid and rewards == {'reward':1.0}:
            outcome = 'missed_wrong' if kind == 'negative' else 'accepted_correct'
        elif valid and rewards == {'reward':0.0}:
            outcome = 'detected_wrong' if kind == 'negative' else 'rejected_legal'
        logs = [evidence_ref(p, ROOT) for p in [path.parent / 'agent/oracle.txt', path.parent / 'verifier/test-stdout.txt'] if p.is_file()]
        trials.append({**expected[label], 'trial_id':result['id'], 'raw_directory':str(path.parent.relative_to(ROOT)),
            'kind':kind, 'native_rewards':rewards, 'exception_info':result.get('exception_info'),
            'independent_proof':evidence_ref(proof_path, ROOT) if proof else None,
            'violation_kind':proof.get('violation_kind') if proof else None,
            'proof_validity':proof['validity'] if proof else None,
            'requirement_ids':proof['requirement_ids'] if proof else [],
            'case_outcome':outcome, 'result':evidence_ref(path, ROOT), 'snapshots':snapshots, 'logs':logs})
    missing = sorted(set(expected) - seen)
    report = {'schema_version':1, 'planned_trials':len(expected), 'observed_trials':len(trials),
              'missing':missing, 'coverage_complete':not missing, 'outcomes':dict(Counter(t['case_outcome'] for t in trials)),
              'manifest':evidence_ref(batch / 'manifest.json', ROOT), 'trials':trials,
              'scope':'Case-level observations only; protocol completion and semantic conclusions require scoped review',
              'qwen_smoke':False}
    write_json(batch / 'reconciliation.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('batch', type=Path)
    args = parser.parse_args()
    report = reconcile(args.batch.resolve())
    print({k:report[k] for k in ['planned_trials','observed_trials','missing','outcomes']})
    raise SystemExit(0 if report['coverage_complete'] else 1)

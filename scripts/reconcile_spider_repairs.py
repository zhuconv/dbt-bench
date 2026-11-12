#!/usr/bin/env python3
"""Publish explicit repaired-environment probes without certifying references.

Only selected tasks change. Every unsuccessful run requires a reviewed,
evidence-bound adjudication; old probe results and other tasks stay untouched.
"""
import argparse
from collections import Counter
from pathlib import Path

from review_tools import ROOT, cached_hashes
from dbt_bench import io
from dbt_bench.catalog import load_catalog
from dbt_bench.validation import CHECKS, build_record, identity, review_inputs, summarize


def classify(facts, review):
    if facts['exit_code'] == 0:
        return {'status': 'pass', 'reason': 'Actual locked Qwen shell dbt run succeeded on the unmodified project with frozen dependencies. This is environment evidence, not a solved-task claim.'}
    if not review or review.get('status') not in ('fail', 'gap') or not review.get('reason'):
        raise ValueError('Unsuccessful execution needs an explicit reviewed reason')
    if facts['exit_code'] not in (1, 2, None):
        raise ValueError('Unexpected dbt termination needs investigation')
    return {'status': review['status'], 'reason': review['reason']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--adjudications', type=Path)
    p.add_argument('--publish', action='store_true')
    a = p.parse_args()
    assert not a.output.exists() and str(a.output.resolve()).startswith('/scratch/')
    a.output.mkdir(parents=True)
    manifest = io.read_json(a.batch / 'manifest.json')
    assert manifest['verifier_disabled'] and io.evidence_ok(manifest['config'], ROOT)
    cfg = io.read_json(ROOT / manifest['config']['path'])
    job = Path(cfg['jobs_dir']) / cfg['job_name']
    assert io.read_json(job / 'result.json')['finished_at']
    reviews = io.read_json(a.adjudications) if a.adjudications else {}
    paths = {str(Path(t['path']).resolve()): Path(t['path']).name for t in cfg['tasks']}
    observations, ids = {}, set()
    with cached_hashes():
        for result in sorted(job.glob('*/result.json')):
            trial = io.read_json(result)
            key = paths[str(Path(trial['config']['task']['path']).resolve())]
            assert key.startswith('spider2__') and key != 'spider2__movie_recomm001'
            assert key not in observations and trial['id'] not in ids and trial['finished_at']
            ids.add(trial['id'])
            assert not trial.get('verifier_result') and not trial.get('exception_info')
            entry = manifest['tasks'][key]
            assert io.evidence_ok(entry['binding'], ROOT)
            assert io.tree_hash(Path(trial['config']['task']['path'])) == entry['task_hash']
            evidence_dir = result.parent / 'agent/unmodified-project'
            facts = io.read_json(evidence_dir / 'result.json')
            no_model = io.read_json(result.parent / 'agent/no-model.json')
            assert no_model['model_requests'] == 0 and no_model['hidden_material_uploaded'] is False
            assert facts['context']['duckdb_version'] == '1.2.2'
            assert facts['packages_unchanged'] and facts['model_sources_unchanged'] and not facts['dbt_deps_executed']
            review = reviews.get(key)
            if review:
                assert review['observed_result'] == io.evidence_ref(evidence_dir / 'result.json', ROOT)
                assert review.get('evidence') and all(io.evidence_ok(r, ROOT) for r in review['evidence'])
            check = classify(facts, review)
            check['evidence'] = [io.evidence_ref(f, ROOT) for f in sorted(evidence_dir.iterdir()) if f.is_file()]
            check['evidence'] += [io.evidence_ref(result, ROOT), entry['binding'],
                io.evidence_ref(a.batch / 'manifest.json', ROOT),
                io.evidence_ref(result.parent / 'agent/unmodified-dbt-shell.json', ROOT)]
            if review:
                check['evidence'] += review['evidence'] + [io.evidence_ref(a.adjudications, ROOT)]
            observations[key] = {'task_key': key, 'trial_id': trial['id'], 'environment_agent': check,
                'dbt_exit_code': facts['exit_code'], 'model_requests': 0, 'native_reward': None,
                'environment_identity': entry['binding'], 'vendored_packages_present': facts['vendored_packages_present']}
            io.write_json(a.output / key / 'environment-agent.json', observations[key])
        assert set(observations) == set(manifest['tasks']) and set(reviews) <= set(observations)
        summary = {'tasks': len(observations), 'counts': dict(Counter(v['environment_agent']['status'] for v in observations.values())),
            'reference_certified': False, 'independent_semantics_executed': False, 'new_model_requests': 0,
            'native_rewards_modified': False, 'native_verifier_modified': False,
            'reconciler': io.evidence_ref(Path(__file__), ROOT)}
        io.write_json(a.output / 'summary.json', summary)
        if a.publish:
            rows = load_catalog(ROOT)
            records = {r['task_key']: r for r in io.read_jsonl(ROOT / 'validation/ledger.jsonl')}
            backup = a.output / 'previous-canonical'
            saved = {}
            unchanged = {r['task_key']: io.file_hash((ROOT / r['validation_ref']).parent / 'record.json')
                         for r in rows if r['task_key'] not in observations}
            try:
                for row in rows:
                    key = row['task_key']
                    if key not in observations:
                        continue
                    directory = (ROOT / row['validation_ref']).parent
                    for name in ('contract.yaml', 'record.json', 'environment.json'):
                        path = directory / name
                        saved[path] = path.read_bytes()
                        destination = backup / key / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(saved[path])
                    contract_path = directory / 'contract.yaml'
                    contract = io.read_json(contract_path)
                    assert not contract.get('audit'), 'Audited tasks require separate review'
                    io.write_json(directory / 'environment.json', {'schema_version': 1, 'backend': 'apptainer',
                        'environment_version': 'spider-duckdb122-frozen-additions-v4', 'duckdb': '1.2.2',
                        'binding': observations[key]['environment_identity'],
                        'actual_agent_probe': io.evidence_ref(a.output / key / 'environment-agent.json', ROOT),
                        'scope': 'Actual Qwen shell unmodified dbt run; no reference or semantic certification.'})
                    contract.setdefault('review_history', []).append(io.evidence_ref(backup / key / 'contract.yaml', ROOT))
                    contract['checks'] = {name: {'status': 'pending', 'reason': 'Environment changed; current-version evidence not yet executed or reviewed.', 'evidence': []} for name in CHECKS}
                    contract['checks']['environment_agent'] = observations[key]['environment_agent']
                    for requirement in contract.get('requirements', []):
                        requirement.setdefault('coverage', {})['native'] = {'status': 'pending', 'reason': 'Await current-environment review; old evidence retained.'}
                    contract['diagnoses'] = []
                    contract['review_evidence'] = [io.evidence_ref(a.output / key / 'environment-agent.json', ROOT)]
                    contract['reviewed_inputs'] = review_inputs(identity(ROOT, row, contract_path, 'apptainer'))
                    io.write_json(contract_path, contract)
                    record = build_record(ROOT, row, contract_path, backend='apptainer')
                    assert record['review_current'] and not record['blockers']
                    assert all(record['native']['checks'][n]['status'] == 'pending' for n in ('reference', 'independent_semantics'))
                    io.write_json(directory / 'record.json', record)
                    records[key] = record
                assert all(io.file_hash((ROOT / r['validation_ref']).parent / 'record.json') == unchanged[r['task_key']]
                           for r in rows if r['task_key'] in unchanged)
            except BaseException:
                for path, data in saved.items():
                    path.write_bytes(data)
                raise
            summarize(ROOT, rows, [records[r['task_key']] for r in rows])
            io.write_json(a.output / 'published.json', {'tasks': len(observations), 'unchanged_other_records': len(unchanged),
                'ledger': io.evidence_ref(ROOT / 'validation/ledger.jsonl', ROOT), 'new_model_requests': 0})
        print(summary)


if __name__ == '__main__':
    main()

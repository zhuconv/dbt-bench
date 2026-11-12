#!/usr/bin/env python3
"""Publish the 55 actual unmodified-project probes without certifying solutions."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
from review_tools import ROOT, cached_hashes
from dbt_bench import io
from dbt_bench.catalog import load_catalog
from dbt_bench.validation import CHECKS, build_record, identity, review_inputs, summarize

# Reviewed against complete retained dbt logs, not inferred from reward or
# incidental warning strings (several runtime failures also have missing-node warnings).
MISSING_NODES = {'activity001', 'airbnb002', 'app_reporting001', 'f1001', 'f1002',
                 'maturity001', 'provider001', 'recharge001', 'recharge002', 'tickit001'}
FAULTS = {
    'atp_tour001': 'Undefined to_date_key/to_age macros and absent countries input table.',
    'flicks001': 'Unquoted Jinja expression in report/report.yml causes YAML parsing failure.',
    'google_play002': 'Invalid refs indentation in google_play.yml causes YAML parsing failure.',
    'inzight001': 'Four packages declared but only three vendored; no dbt deps overwrite attempted.',
    'nba001': 'Python-model DataFrame registration fails: DuckDB 1.2.2 does not recognize pandas str dtype.',
    'netflix001': 'One package declared, zero installed in task-vendored dbt_packages.',
    'qualtrics001': 'Native project SQL fails on percentile_cont usage and unquoted day identifier.',
    'sap001': 'Native source tables sap_bkpf_data, sap_faglflexa_data, sap_faglflext_data and sap_lfa1_data absent.',
    'scd001': 'One package declared, zero installed in task-vendored dbt_packages.',
    'shopify_holistic_reporting001': 'Two packages declared, zero installed in task-vendored dbt_packages.',
    'social_media001': 'Native Facebook reporting SQL groups by an aggregate expression.',
    'synthea001': 'Native models call undefined dbt_utils macros.',
    'tickit002': 'Invalid indentation in _marketing__models.yml causes YAML parsing failure.',
    'tpch002': 'Native source tables including partsupp and lineitem are absent.',
    'zuora001': 'One package declared, zero installed in task-vendored dbt_packages.',
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--publish', action='store_true')
    a = p.parse_args()
    assert not a.output.exists()
    a.output.mkdir(parents=True)
    rows = load_catalog(ROOT)
    catalog = {r['task_key']: r for r in rows}
    observations, ids = {}, set()
    with cached_hashes():
        for batch in a.batch:
            manifest = io.read_json(batch / 'manifest.json')
            assert io.evidence_ok(manifest['config'], ROOT) and manifest['verifier_disabled']
            config = io.read_json(ROOT / manifest['config']['path'])
            job = Path(config['jobs_dir']) / config['job_name']
            assert io.read_json(job / 'result.json')['finished_at']
            paths = {str(Path(t['path']).resolve()): Path(t['path']).name for t in config['tasks']}
            seen = set()
            for result in sorted(job.glob('*/result.json')):
                trial = io.read_json(result)
                key = paths[str(Path(trial['config']['task']['path']).resolve())]
                assert key not in observations and trial['id'] not in ids and trial['finished_at']
                assert not trial.get('verifier_result'), 'Environment probe must not fabricate native reward'
                assert not trial.get('exception_info'), 'Inspect probe infrastructure failure separately'
                ids.add(trial['id']); seen.add(key)
                entry = manifest['tasks'][key]
                assert io.evidence_ok(entry['binding'], ROOT)
                assert io.tree_hash(Path(trial['config']['task']['path'])) == entry['task_hash']
                evidence_dir = result.parent / 'agent/unmodified-project'
                facts = io.read_json(evidence_dir / 'result.json')
                no_model = io.read_json(result.parent / 'agent/no-model.json')
                assert no_model['model_requests'] == 0 and no_model['hidden_material_uploaded'] is False
                assert facts['context']['duckdb_version'] == '1.2.2'
                assert facts['packages_unchanged'] and facts['model_sources_unchanged']
                short = key.split('__', 1)[1]
                if facts['exit_code'] == 0:
                    assert short not in MISSING_NODES | FAULTS.keys()
                    status, category = 'pass', 'existing_project_build_succeeded'
                    reason = 'Actual locked Qwen shell dbt run succeeded on the unmodified project; missing target models may still be task work. This is not a solved-task claim.'
                elif short in MISSING_NODES:
                    assert facts['exit_code'] == 2 and facts['missing_model_dependencies_reported']
                    status, category = 'gap', 'missing_node_blocks_further_execution'
                    reason = 'Actual compilation stopped at a missing node: ' + ', '.join(sorted(set(facts['missing_model_dependencies_reported']))) + '. Remaining project execution cannot be assessed without supplying task models; no full solution was written.'
                else:
                    assert short in FAULTS and facts['exit_code'] in (1, 2)
                    status, category, reason = 'fail', 'non_missing_model_execution_error', FAULTS[short]
                refs = [io.evidence_ref(f, ROOT) for f in sorted(evidence_dir.iterdir()) if f.is_file()]
                refs += [io.evidence_ref(result, ROOT), io.evidence_ref(result.parent / 'agent/unmodified-dbt-shell.json', ROOT), entry['binding'], io.evidence_ref(batch / 'manifest.json', ROOT)]
                observations[key] = {'task_key': key, 'trial_id': trial['id'], 'category': category,
                    'environment_agent': {'status': status, 'reason': reason, 'evidence': refs},
                    'dbt_exit_code': facts['exit_code'], 'vendored_packages_present': facts['vendored_packages_present'],
                    'packages_yml_present': facts['packages_yml_present'], 'native_reward': None,
                    'model_requests': 0, 'environment_identity': entry['binding']}
            assert seen == set(manifest['tasks'])
        expected = {r['task_key'] for r in rows if r['source'] == 'spider2'}
        assert set(observations) == expected and len(observations) == 55
        for key, observation in observations.items():
            io.write_json(a.output / key / 'environment-agent.json', observation)
        summary = {'candidate_count': 55, 'counts': dict(Counter(v['environment_agent']['status'] for v in observations.values())),
            'categories': dict(Counter(v['category'] for v in observations.values())),
            'without_vendored_packages': sorted(k for k, v in observations.items() if not v['vendored_packages_present']),
            'native_verifier_modified': False, 'native_rewards_modified': False, 'new_model_requests': 0,
            'reference_nop_reexecution_started': False, 'all_environment_checks_passed': False,
            'remaining_work': 'Review 15 non-missing-model errors; no floating dbt deps or hand-written complete solutions. Native reference/nop reruns remain pending.',
            'reconciler': io.evidence_ref(Path(__file__), ROOT)}
        io.write_json(a.output / 'summary.json', summary)
        if a.publish:
            records = {r['task_key']: r for r in io.read_jsonl(ROOT / 'validation/ledger.jsonl')}
            backup = a.output / 'previous-canonical'
            for key in sorted(observations):
                row = catalog[key]
                directory = (ROOT / row['validation_ref']).parent
                contract_path = directory / 'contract.yaml'
                contract = io.read_json(contract_path)
                for name in ('contract.yaml', 'record.json', 'environment.json'):
                    src = directory / name
                    if not src.exists():
                        continue
                    dest = backup / key / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(src.read_bytes())
                previous = deepcopy(contract)
                io.write_json(directory / 'environment.json', {'schema_version': 1, 'backend': 'apptainer',
                    'environment_version': 'spider-duckdb122-vendored-v3',
                    'binding': observations[key]['environment_identity'],
                    'actual_agent_probe': io.evidence_ref(a.output / key / 'environment-agent.json', ROOT),
                    'duckdb': '1.2.2', 'dbt_packages_policy': 'exact vendored task bytes; no dbt deps',
                    'native_verifier_probe': 'pending except separately retained asana001 build witnesses',
                    'scope': 'Actual initial Qwen shell and unmodified project execution; no cross-backend or solvability certification.'})
                contract.setdefault('review_history', []).append(io.evidence_ref(backup / key / 'contract.yaml', ROOT))
                contract['checks'] = {name: {'status': 'pending', 'reason': 'Spider environment changed to DuckDB 1.2.2 with exact vendored packages; historical runtime evidence is not resumed.', 'evidence': []} for name in CHECKS}
                contract['checks']['environment_agent'] = observations[key]['environment_agent']
                for requirement in contract.get('requirements', []):
                    requirement.setdefault('coverage', {})['native'] = {'status': 'pending', 'reason': 'Revalidate under corrected Spider environment; historical coverage retained in review_history.'}
                if key == 'spider2__movie_recomm001':
                    # Keep the explicitly isolated static contradiction. Verify
                    # source evidence is unchanged; no new task investigation.
                    assert previous.get('diagnoses')
                    for diagnosis in previous['diagnoses']:
                        static = [r for r in diagnosis['evidence'] if r['path'].startswith('tasks/')]
                        assert static and all(io.evidence_ok(r, ROOT) for r in static)
                    contract['checks']['contract'] = previous['checks']['contract']
                else:
                    contract['diagnoses'] = []
                if key == 'spider2__asana001':
                    witness_path = ROOT / 'artifacts/validation/spider-v3-asana-witness-v1/reconciliation.json'
                    witness = io.read_json(witness_path)
                    comparison = ROOT / 'artifacts/validation/phase1-review-v1/asana-v3-all-columns.json'
                    assert len(witness['trials']) == 2 and witness['tasks'][0]['stability']['equal'] is True
                    assert all(t['native_reward'] == {'reward': 1.0} and t['shell_results']['reference']['exit_code'] == 0
                               and t['producer_to_verifier']['equal'] is True for t in witness['trials'])
                    assert all(r['exact_values_equal'] for r in io.read_json(comparison)['results'])
                    refs = [io.evidence_ref(witness_path, ROOT), io.evidence_ref(comparison, ROOT)]
                    contract['witness_kind'] = 'built_from_inputs'
                    for check in ('reference', 'reference_repeat', 'stability', 'cross_context', 'environment_verifier'):
                        contract['checks'][check] = {'status': 'pass',
                            'reason': 'Two clean builds add only the two pinned upstream v0.8.1 models, execute 40 dbt models through Qwen shell, preserve 45 full-column output rows across the unchanged verifier and both initializations. Build phase has no gold access; native reward is 1 twice.',
                            'evidence': refs}
                contract['reviewed_inputs'] = review_inputs(identity(ROOT, row, contract_path, 'apptainer'))
                contract.setdefault('review_evidence', []).append(io.evidence_ref(a.output / key / 'environment-agent.json', ROOT))
                io.write_json(contract_path, contract)
                record = build_record(ROOT, row, contract_path, backend='apptainer')
                assert record['review_current'] and not record['blockers'], key
                assert record['native']['checks']['environment_agent']['status'] == observations[key]['environment_agent']['status']
                if key == 'spider2__movie_recomm001':
                    assert record['native']['conclusion'] == 'broken'
                io.write_json(directory / 'record.json', record)
                records[key] = record
            summarize(ROOT, rows, [records[r['task_key']] for r in rows])
            io.write_json(a.output / 'published.json', {'tasks': 55, 'environment_agent_counts': summary['counts'],
                'ledger': io.evidence_ref(ROOT / 'validation/ledger.jsonl', ROOT),
                'scope': 'New environment checks only; prior runtime certification invalidated, all raw scores retained.'})
        print(summary)


if __name__ == '__main__':
    main()

"""Bind the reviewed FX cases, keeping unresolved reference semantics pending."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, file_hash, read_json, write_json
from dbt_bench.validation import CHECKS, identity, review_inputs
from independent import encoded


def main():
    key = 'deb__dbt-exchange-rate-settlement-date'
    out = ROOT / 'validation/evidence/fx-settlement-review-v1'
    out.mkdir(parents=True, exist_ok=False)
    stage = ROOT / 'artifacts/validation/fx-settlement-review-v1'
    reports = [ROOT / 'artifacts/validation' / b / 'reconciliation.json' for b in
               ['fx-settlement-cases-pilot-v1', 'fx-settlement-cases-remaining-v1']]
    batches = [read_json(p) for p in reports]
    assert all(b['coverage_complete'] for b in batches)
    trials = [t for b in batches for t in b['trials']]
    assert len(trials) == 9 and len({t['trial_id'] for t in trials}) == 9
    assert not any(t['case_outcome'] == 'unresolved_execution' for t in trials)
    groups = {}
    for t in trials:
        groups.setdefault(t['case_mode'], []).append(t)
    assert len(groups['reference']) == 2
    assert all(t['case_outcome'] == 'accepted_scoped_positive' for t in groups['reference'] + groups['reordered'])
    observed = {}
    for mode, expected in [('duplicate-row', 'missed_wrong'), ('missing-count-column', 'missed_wrong'),
                           ('null-usd-for-null-currency', 'missed_wrong'), ('view-materialization', 'missed_wrong'),
                           ('missing-row', 'detected_wrong'), ('order-date-rate', 'detected_wrong')]:
        trial = groups[mode][0]
        assert trial['case_outcome'] == expected
        observed[mode] = expected
        log = (ROOT / trial['native_log']['path']).read_text()
        if expected == 'missed_wrong':
            assert '20 passed' in log and 'SUCCESS: All 20 tests passed' in log
        elif mode == 'missing-row':
            assert 'Missing keys in fact_revenue' in log and 'ERROR at setup' not in log
        else:
            assert 'Found 154 rows with fx_rate != dim rate' in log and 'ERROR at setup' not in log
    # Retain volatile metadata and explicitly check the actual build window.
    refs = [read_json(ROOT / t['snapshots']['after']['path']) for t in groups['reference']]
    stable_rows = [sorted(encoded({k: v for k, v in r.items() if k != 'dbt_updated_at'}) for r in s['rows']) for s in refs]
    assert stable_rows[0] == stable_rows[1] and refs[0]['types'] == refs[1]['types']
    assert all(t['metadata_timestamp_checks']['after']['within_build'] for t in groups['reference'])
    native = read_json(ROOT / 'validation/deb/dbt-exchange-rate-settlement-date/native-reference-server-v1.json')
    nops = [t for t in native['trials'] if t['agent'] == 'nop']
    assert len(nops) == 2 and all(t['native_rewards'] == {'reward': 0.0} and t['exception_info'] is None for t in nops)
    nop_logs = []
    for t in nops:
        path = ROOT / t['raw_directory'] / 'verifier/test-stdout.txt'
        text = path.read_text()
        assert 'settlement_date' in text and 'not found' in text
        nop_logs.append(evidence_ref(path, ROOT))
    input_review = read_json(stage / 'independent-input-review.json')
    assert input_review['reference_grain_review']['original_grain_groups'] == 331
    assert input_review['reference_grain_review']['reference_grain_groups'] == 340
    null_proof = read_json(ROOT / groups['null-usd-for-null-currency'][0]['independent_proof']['path'])
    assert len(null_proof['facts']['comparison']['value_mismatches']) == 38
    sources = [evidence_ref(ROOT / 'tasks' / key / p, ROOT) for p in
               ['instruction.md', 'task.toml', 'environment/Dockerfile', 'solution/solve.sh', 'tests/test.sh', 'tests/test_outputs.py']]
    sources += [evidence_ref(p, ROOT) for p in sorted((stage / 'initial/project').rglob('*')) if p.is_file()]
    summary = {'schema_version': 1, 'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'task_key': key,
               'planned_trials': 9, 'observed_trials': 9, 'outcomes': dict(Counter(t['case_outcome'] for t in trials)),
               'negative_detection': {'should_detect': 6, 'detected': 2, 'missed': 4}, 'cases': observed,
               'null_currency_wrong_numeric_cells': 38, 'stable_reference_nonvolatile_columns': 28,
               'metadata_stability_rule': 'dbt_updated_at retained and verified within each table build; current_timestamp is defined by the original model.',
               'reference_builds': 2, 'reference_semantic_certification': False,
               'reference_type_gap': {k: refs[0]['types'][k] for k in ['fx_rate', 'net_revenue_usd', 'total_revenue_usd']},
               'alignment': input_review['alignment_gap'], 'independent_input_review': evidence_ref(stage / 'independent-input-review.json', ROOT),
               'reviewed_sources': sources, 'batches': [evidence_ref(p, ROOT) for p in reports],
               'untouched_results': [t['result'] for t in nops], 'untouched_logs': nop_logs,
               'trials': trials, 'native_conclusion': 'weak', 'audit_conclusion': 'pending',
               'scope': 'Four demonstrated native omissions. Grain wording and DECIMAL output types are unresolved, so reference acceptance is not full solvability certification. Other protocol gaps remain explicit.'}
    write_json(out / 'summary.json', summary)
    proof_ref = evidence_ref(out / 'summary.json', ROOT)
    path = ROOT / 'validation/deb/dbt-exchange-rate-settlement-date/contract.yaml'
    old = read_json(path)
    history = out / ('contract-before-' + file_hash(path) + '.json')
    shutil.copyfile(path, history)
    specs = [
        ('R-workflow', 'Update the existing DuckDB fact_revenue dbt model and build it through dbt.', 'run_dbt_pipeline runs the submitted model; it does not prove unchanged upstream source.', []),
        ('R-backend', 'Determine the live DB_TYPE before writing; use the matching project.', 'get_dbt_project_dir and database connection select the live backend; agent command ordering is not checked.', []),
        ('R-profile', 'Configure retail_dw_master profiles.yml with the declared DuckDB database and DBT_PROFILES_DIR.', 'dbt run and read-only database access exercise the configured paths.', []),
        ('R-settlement', 'Use the most recent payment settlement timestamp for each order.', 'fetch_expected_keys and expected_totals derive MAX(processed_at); independent Python maxima agree for current inputs.', []),
        ('R-fallback', 'Orders without payments use order_date as settlement_date.', 'Expected key set covers all groups; explicit no-payment check samples ten groups.', []),
        ('R-fx', 'Use settlement-date daily FX rates for conversion to USD.', 'All matching non-USD rate rows are compared; expected USD sums use settlement-date rates.', ['order-date-rate']),
        ('R-usd-identity', 'USD local amounts remain unchanged and fx_rate is one.', 'All USD fx_rate values checked; local/USD value identity samples 50 rows.', []),
        ('R-missing-rate', 'Handle missing rates sensibly and avoid NULL fx_rate.', 'No-NULL rate check is complete; reference uses 1.0 fallback, with partial/sampled fallback checks.', []),
        ('R-usd-values', 'net_revenue_usd and total_revenue_usd equal local revenue times fx_rate, rounded to two decimals.', 'Full expected query uses inner equality joins and 0.20 tolerance; self-consistency comparisons permit SQL NULL unknown; rounding samples 100 rows.', ['null-usd-for-null-currency']),
        ('R-null-keys', 'Preserve required groups whose currency/source keys are NULL and their numeric converted amounts when local amounts are non-NULL.', 'Key set preserves NULL but equality joins skip it; NULL monetary comparisons are not true, exposing the demonstrated omission.', ['null-usd-for-null-currency']),
        ('R-original-grain', 'Maintain the existing revenue fact grain while incorporating settlement date.', 'Original SQL has three grouping keys; reference/grader use four. Nine original groups split (331 to 340). Task interpretation remains unresolved.', []),
        ('R-unique-grain', 'Do not duplicate a revenue group.', 'fetch_fact_revenue_keys groups first and Python converts to set; no multiplicity assertion.', ['duplicate-row']),
        ('R-complete-grain', 'Do not omit required source revenue groups.', 'Full expected-vs-actual key set detects the demonstrated missing group under the reference interpretation.', ['missing-row']),
        ('R-preserve-columns', 'Preserve all 25 original output columns and add the four requested columns.', 'Only the four new columns are explicitly asserted; other queries reference a limited subset of old columns.', ['missing-count-column']),
        ('R-types', 'Provide settlement_date as DATE and the requested FX/USD fields as DECIMAL.', 'No type assertions. Native reference currently emits DOUBLE for the three numeric additions; physical-type requirement remains unresolved.', []),
        ('R-upstream', 'Do not modify upstream staging models or fixed initial inputs.', 'Native queries read the current upstream database; no independent pristine-input or staging-file check.', []),
        ('R-materialization', 'Do not change the original table materialization.', 'Native verifier does not assert relation type; a successfully built view is accepted.', ['view-materialization']),
        ('R-order', 'No output row ordering is required.', 'Queries use keys/values or their own sample order; reversed output is accepted.', ['reordered']),
    ]
    requirements = []
    for req_id, description, mapping, modes in specs:
        coverage = {'status': 'pending', 'reason': 'No executed task-specific negative for this requirement; source mapping is not certification.', 'evidence': []}
        if modes:
            t = groups[modes[0]][0]
            coverage = {'status': 'missed_wrong' if t['case_outcome'] == 'missed_wrong' else 'covered',
                        'case_validity': t['proof_validity'], 'independent_proof': t['independent_proof'],
                        'checker_evidence': t['result'], 'evidence': [t['independent_proof'], t['result'], proof_ref],
                        'scope': 'Only the independently established stated case on fixed inputs.'}
        requirements.append({'id': req_id, 'critical': True, 'description': description,
                             'native_mapping': mapping, 'cases': modes,
                             'coverage': {'native': coverage, 'audit': {'status': 'pending'}}})
    checks = {name: {'status': 'pending', 'reason': 'Task-specific protocol coverage or full reference interpretation is incomplete.', 'evidence': []} for name in CHECKS}
    def check(status, reason, evidence=None):
        return {'status': status, 'reason': reason, 'evidence': evidence or [proof_ref]}
    checks.update(assets=check('pass', 'Pinned original inputs, task, verifier, backend binding and independent input hashes checked in both batches.'),
                  contract=check('pass', 'Pinned instruction, native setup/model/docs, full verifier and reference read; every requirement has explicit coverage or a gap.', sources + [proof_ref]),
                  nop=check('pass', 'Two current-server-layer untouched runs fail because the required settlement/USD columns are absent.', [t['result'] for t in nops] + nop_logs),
                  negatives=check('gap', 'Six independently established negatives ran; two rejected, four accepted. Other rule negatives remain pending.'),
                  comparator=check('fail', 'Native grouping hides duplicate rows and SQL NULL comparisons miss 38 required numeric USD values.'),
                  stability=check('pass', 'Two clean reference builds agree on 340 rows and all 28 nonvolatile columns/types; timestamp metadata is retained and falls within each actual build.'))
    contract = {**old, 'review_status': 'reviewed', 'workflow': 'fix_existing_finance_model_settlement_date_fx',
                'reference_kind': 'combined', 'witness_kind': 'unknown',
                'construction_observation': {'built_from_inputs': True, 'runs': 2, 'compliant_witness_certified': False, 'reason': 'Original-grain and declared DECIMAL alignment unresolved.'},
                'inputs': [{'path': '/app/database/retail.duckdb', 'tables': ['main.fct_sales', 'main.order_payments', 'main.dim_exchange_rates'], 'role': 'fixed public prebuilt tables; do not rebuild the random-rate upstream dimension'}],
                'deliverables': [{'path': '/app/dbt_models_duckdb/models/marts/finance/fact_revenue.sql', 'kind': 'existing dbt model'},
                                 {'path': '/app/database/retail.duckdb', 'table': 'main.fact_revenue', 'preserved_columns': input_review['original_output_columns'], 'add_columns': ['settlement_date', 'fx_rate', 'net_revenue_usd', 'total_revenue_usd']}],
                'comparison_contract': {'keys': ['order_date', 'settlement_date', 'currency_code', 'source_system'],
                                        'scope': 'Reference interpretation only; original three-key requirement remains unresolved.',
                                        'duplicates': 'Reject', 'row_order': 'irrelevant', 'extra_columns': 'No prohibition stated',
                                        'key_types': 'Dates and categories exact, NULL-safe; never float keys.',
                                        'counts': 'Exact integer values', 'core_numeric_tolerance': '0.000001 only for declared numeric sums/FX fields in independent proof; not a blanket native success rule.',
                                        'metadata': 'Preserve dbt_updated_at and check its actual build time; current_timestamp is explicitly defined in original SQL.'},
                'requirements': requirements, 'checks': checks, 'native_success': {'metric': 'reward', 'equals': 1.0, 'reviewed': True},
                'missing_contract_work': ['Resolve original three-key grain versus reference/grader four-key grain without silently changing task text.',
                                          'Resolve explicit DECIMAL types versus DOUBLE in reference and produce a fully compliant construction witness.',
                                          'Cross-check remaining preserved values and execute uncovered settlement/fallback/USD/rounding/upstream negatives.',
                                          'Review actual Qwen tool context, submission transfer and hidden-asset visibility for this task.',
                                          'Independently validate an audit checker; the proof helper is not a certified audit.'],
                'audit': None, 'review_evidence': [proof_ref], 'review_history': old.get('review_history', []) + [evidence_ref(history, ROOT)],
                'notes': old.get('notes', []) + ['Reference acceptance certifies neither literal grain preservation nor DECIMAL output types. Native weak is based on executed independent negative cases, not on those unresolved interpretations.']}
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == key)
    contract['reviewed_inputs'] = review_inputs(identity(ROOT, row, path, 'apptainer'))
    write_json(path, contract)
    print({'task_key': key, 'native': 'weak', 'audit': 'pending', 'negative_detection': summary['negative_detection'], 'witness_certified': False})


if __name__ == '__main__':
    main()

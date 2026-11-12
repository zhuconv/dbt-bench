"""Bind the explicitly reviewed cases to the task contract, without certification."""
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.catalog import load_catalog
from dbt_bench.io import read_json, write_json, evidence_ref, file_hash
from dbt_bench.validation import identity, review_inputs, CHECKS
from independent import TABLES, STAGING_COLUMNS, INT_COLUMNS, canonical


def main():
    out = ROOT / 'validation/evidence/deb-consolidate-review'
    batches = [ROOT / 'artifacts/validation' / name for name in [
        'consolidate-semantic-pilot-v2', 'consolidate-semantic-remaining-v1']]
    reports = [read_json(b / 'reconciliation.json') for b in batches]
    assert all(r['coverage_complete'] for r in reports)
    trials = [t for r in reports for t in r['trials']]
    assert len(trials) == 16 and len({t['trial_id'] for t in trials}) == 16
    assert all(t['case_outcome'] != 'unresolved_execution' for t in trials)
    groups = {}
    for trial in trials:
        groups.setdefault(trial['case_mode'], []).append(trial)
    references = groups['reference']
    assert len(references) == 2 and all(t['case_outcome'] == 'accepted_correct' for t in references)
    # Compare the declared values and materialization types across independent starts.
    snapshots = [read_json(ROOT / t['snapshots']['after']['path']) for t in references]
    stable = {}
    for table in TABLES:
        values = [Counter(canonical(r, table == 'unified') for r in s['tables'][table]['rows']) for s in snapshots]
        stable[table] = values[0] == values[1] and snapshots[0]['tables'][table]['types'] == snapshots[1]['tables'][table]['types'] and snapshots[0]['tables'][table]['table_type'] == snapshots[1]['tables'][table]['table_type']
    assert all(stable.values())
    for s in snapshots:
        assert all(s['tables'][k]['table_type'] == 'VIEW' for k in ['google','meta','tiktok'])
        assert s['tables']['unified']['table_type'] == 'BASE TABLE'
    # Review current-runtime untouched runs; do not relabel old-runtime observations.
    native = read_json(ROOT / 'artifacts/validation/lifecycle-native-regression-v1/reconciliation.json')
    nops = [t for t in native['trials'] if t['task_key'] == 'deb__dbt-consolidate' and t['agent'] == 'nop']
    assert len(nops) == 2 and all(t['native_rewards'] == {'reward':0.0} and t['exception_info'] is None for t in nops)
    nop_logs = []
    for t in nops:
        p = ROOT / t['raw_directory'] / 'verifier/test-stdout.txt'
        text = p.read_text()
        assert 'dbt_project.yml' in text and ('not found' in text or 'No dbt_project.yml' in text)
        nop_logs.append(evidence_ref(p, ROOT))
    task = ROOT / 'tasks/deb__dbt-consolidate'
    reviewed_sources = [evidence_ref(task / p, ROOT) for p in [
        'instruction.md','environment/Dockerfile','solution/solve.sh','tests/test.sh','tests/test_outputs.py']]
    # Inspect the actual native failure, not only the zero reward.
    decimal_trial = groups['decimal-counts'][0]
    decimal_log = ROOT / decimal_trial['raw_directory'] / 'verifier/test-stdout.txt'
    assert decimal_trial['case_outcome'] == 'rejected_legal'
    assert 'AssertionError: int__ads_unified output mismatch' in decimal_log.read_text()
    assert "'10.00'" in decimal_log.read_text()
    summary = {
        'schema_version':1, 'task_key':'deb__dbt-consolidate',
        'planned_trials':16, 'observed_trials':16, 'outcomes':dict(Counter(t['case_outcome'] for t in trials)),
        'negative_detection_by_kind':{
            kind:{'should_detect':sum(t['violation_kind'] == kind for t in trials),
                  'detected':sum(t['violation_kind'] == kind and t['case_outcome'] == 'detected_wrong' for t in trials),
                  'missed':sum(t['violation_kind'] == kind and t['case_outcome'] == 'missed_wrong' for t in trials)}
            for kind in ['data_semantics','explicit_workflow_requirement']},
        'stable_declared_outputs':stable, 'reference_runs':2,
        'independent_input_review':evidence_ref(out / 'independent-input-review.json', ROOT),
        'reviewed_sources':reviewed_sources, 'batches':[evidence_ref(b / 'reconciliation.json', ROOT) for b in batches],
        'untouched_results':[t['result'] for t in nops], 'untouched_failure_logs':nop_logs,
        'trials':trials, 'native_conclusion':'weak', 'audit_conclusion':'pending',
        'scope':'Pinned original input and these explicit cases only; Qwen context, complete workflow coverage and separately certified audit remain unfinished'}
    summary_path = out / 'case-summary.json'
    write_json(summary_path, summary)
    proof_ref = evidence_ref(summary_path, ROOT)
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == 'deb__dbt-consolidate')
    path = ROOT / 'validation/deb/dbt-consolidate/contract.yaml'
    old = read_json(path)
    history = out / ('contract-before-' + file_hash(path) + '.json')
    if not history.exists():write_json(history, old)
    pending = {'status':'pending','reason':'No task-specific executed negative case yet; native source mapping is not runtime coverage','evidence':[]}
    specifications = [
        ('R-paths','Create the project and four SQL models at the explicitly named paths.', 'verify_structure', []),
        ('R-project','Set dbt_project.yml name to exactly dbt_consolidate.', 'No explicit project-name assertion', ['wrong-project-name']),
        ('R-profile','Use local profiles.yml, retail_dw_master profile, analytics schema, DuckDB /app/consolidate.duckdb and DBT_PROFILES_DIR.', 'run_dbt_pipeline and get_db_connection indirectly use paths/schema; profile-name/env adherence not asserted', []),
        ('R-seed','Load the three supplied CSVs through dbt seed without changing source data.', 'sync_private_seeds then dbt seed and ensure_immutability_of_files; private files are byte-identical to public files', []),
        ('R-packages','Install dbt-utils and run dbt deps.', 'run_dbt_pipeline and ensure_dbt_packages_installation', []),
        ('R-columns','Expose the five canonical staging columns and six canonical intermediate columns.', 'verify_schema requires subsets; exact type, column order and absence of extra columns are not specified', []),
        ('R-values','Preserve counts and dates; Google views=views, Meta views=views_1+views_2, TikTok views=views_1.', 'validate_intermediate_data compares all final rows; staging values are not separately compared', ['omit-meta-views-2']),
        ('R-staging-values','Each of the three staging outputs must itself contain the correct standardized input values.', 'Only staging schema and date uniqueness; no staging value comparison', ['compensated-staging']),
        ('R-dedup','Deduplicate identical standardized rows, retaining one copy per full row.', 'validate_dupes checks staging ad_date and final source/ad_date; these are unique in the fixed inputs after full-row deduplication', ['keep-tiktok-duplicates']),
        ('R-qualify-row-number','Use QUALIFY and ROW_NUMBER for staging deduplication.', 'No workflow assertion for these operators', ['distinct-dedup']),
        ('R-staging-views','Materialize all three staging models as views.', 'No information_schema materialization assertion', ['staging-tables']),
        ('R-union-values','Retain all deduplicated platform rows and the exact google/meta/tiktok source labels.', 'Full final row comparison and key duplicate check', ['drop-google-date']),
        ('R-union-all','Combine staging outputs with UNION ALL.', 'Only final values checked; source tags make UNION equivalent on these inputs', ['union-distinct']),
        ('R-intermediate-table','Materialize int__ads_unified as a table.', 'No relation-type assertion', ['intermediate-view']),
        ('R-row-association','Keep dates, source labels and their metrics associated in the same rows.', 'Full row comparison, retaining row associations', ['swap-google-clicks']),
        ('R-yaml','Provide both staging.yml and int.yml model YAML files.', 'No explicit staging.yml assertion; test artifacts indirectly require some intermediate test declaration', ['missing-staging-yaml']),
        ('R-unique-test','Declare dbt_utils.unique_combination_of_columns for ad_date and source on int__ads_unified and run dbt test.', 'check_tests accepts any passing test linked to the intermediate model, not this specific test', ['wrong-dbt-test']),
        ('R-count-representation','Accept exact integer-valued numeric representations without requiring an unstated scale; e.g. 10 and Decimal(10.00).', 'get_data stringifies numbers, so decimal scale affects comparison', ['decimal-counts']),
        ('R-order','Do not impose an output row order.', 'get_data and validate_intermediate_data sort full rows', ['reordered']),
    ]
    requirements = []
    for req_id, text, mapping, modes in specifications:
        coverage = dict(pending)
        observations = [t for mode in modes for t in groups[mode]]
        if observations:
            t = observations[0]
            status = t['case_outcome']
            coverage = {'status':status if status in {'missed_wrong','rejected_legal'} else 'covered',
                'case_validity':t['proof_validity'], 'independent_proof':t['independent_proof'],
                'checker_evidence':t['result'], 'evidence':[t['independent_proof'],t['result'],proof_ref],
                'scope':'Executed stated original-data case only; broader SQL/data generalization is not claimed'}
            if req_id == 'R-values':
                coverage['status'] = 'partial'
                coverage['reason'] = 'Meta combination negative executed; independent positive recomputation covers all platforms, but Google/TikTok value corruption cases remain unexecuted'
        requirements.append({'id':req_id,'critical':True,'description':text,
            'native_mapping':mapping,'cases':modes,'coverage':{'native':coverage,'audit':{'status':'pending'}}})
    def check(status, reason, refs=None):
        return {'status':status,'reason':reason,'evidence':refs or [proof_ref]}
    checks = {name:{'status':'pending','reason':'Task-specific actual Qwen context and complete protocol review not yet performed','evidence':[]} for name in CHECKS}
    checks.update(
        assets=check('pass','Pinned source/data hashes and current bound task are captured in both batch manifests; public/private seed bytes match', [evidence_ref(out / 'independent-input-review.json',ROOT)] + [evidence_ref(b / 'manifest.json',ROOT) for b in batches]),
        contract=check('pass','Instruction, Dockerfile, reference, full native verifier and all CSVs reviewed; all declared requirements mapped, including explicit gaps',reviewed_sources+[proof_ref]),
        reference=check('pass','Native dbt deps/seed/run/test built all declared outputs from public CSVs; independent recomputation matches all four tables in both runs'),
        reference_repeat=check('pass','Two independently initialized reference cases accepted with independent proof and captured declared outputs'),
        nop=check('pass','Two current-runtime untouched cases fail because no dbt project was submitted',[t['result'] for t in nops]+nop_logs),
        equivalent=check('fail','Order equivalence is accepted; exact integer-valued Decimal outputs are rejected solely by string representation'),
        independent_semantics=check('pass','Independent stdlib CSV transformation reproduces all original-data staging and unified rows, cross-checked against hand-computable unit fixtures'),
        negatives=check('gap','Executed semantic and workflow negatives expose missed requirements; remaining unexecuted coverage stays explicit'),
        comparator=check('fail','Native final row comparison rejects semantic row association errors but falsely rejects equivalent Decimal representations'),
        stability=check('pass','Both clean reference outputs match as typed row multisets and materialization types; no unstable column discarded'))
    contract = {**old, 'review_status':'reviewed', 'workflow':'create_project_seed_standardize_deduplicate_union_and_test',
        'reference_kind':'combined','witness_kind':'built_from_inputs',
        'inputs':[{'path':'/app/data/'+filename,'role':'public CSV','immutable':True} for filename in ['googleads.csv','metaads.csv','tiktokads.csv']],
        'deliverables':[
            {'path':'/app/dbt_consolidate','kind':'dbt project','models':[f'analytics.{t}' for t in TABLES.values()]},
            {'path':'/app/consolidate.duckdb','kind':'DuckDB outputs','staging_columns':list(STAGING_COLUMNS),'intermediate_columns':list(INT_COLUMNS)}],
        'comparison_contract':{'row_order':'irrelevant','keys':{'staging':'full standardized row for deduplication; ad_date is unique in fixed inputs','intermediate':['source','ad_date']},
            'numeric':'Exact integer-valued counts; no float coercion or tolerance; equivalent Decimal scale allowed',
            'null_nan':'Fixed inputs have no missing values; no rule invented for out-of-scope missing inputs',
            'extra_columns':'Instruction gives canonical outputs but no explicit ban on extras; no added rejection rule',
            'date':'ISO calendar date; no ordering requirement','precision':'No rounding required by fixed integer inputs'},
        'requirements':requirements,'checks':checks,'native_success':{'metric':'reward','equals':1.0,'reviewed':True},
        'missing_contract_work':[
            'Capture actual Qwen tool execution context and task-specific agent/verifier compatibility and hidden-asset visibility.',
            'Execute negative coverage for project paths/profile, input lineage, packages, required columns and remaining platform value mappings.',
            'Build and independently certify a supplemental checker covering both workflow and values; native is weak.',
            'Generalized full-row deduplication with distinct same-date rows needs explicit handling of the requested final source/date uniqueness test; fixed inputs do not exercise this branch.'],
        'audit':None,'review_evidence':[proof_ref,evidence_ref(out / 'independent-input-review.json',ROOT)],
        'review_history':old.get('review_history',[])+[evidence_ref(history,ROOT)],
        'notes':[
            'The specific consolidation profile section overrides the generic retail DB boilerplate; reference and grader both use /app/consolidate.duckdb.',
            'The native verifier rebuilds from the submission and replaces seeds with byte-identical private CSVs; proof snapshots capture the source submission and pre-verifier values.',
            'Workflow violations and incorrect data values are reported separately; no native score is overwritten.',
            'The independent recomputation is proof evidence, not a fully certified audit checker.']}
    contract['reviewed_inputs'] = review_inputs(identity(ROOT,row,path,'apptainer'))
    write_json(path, contract)
    print({'task':row['task_key'],'native':'weak','audit':'pending','outcomes':summary['outcomes'],'stable_outputs':stable})


if __name__ == '__main__':main()

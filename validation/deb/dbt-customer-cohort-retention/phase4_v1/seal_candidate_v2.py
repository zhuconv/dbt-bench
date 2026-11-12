"""Final actual candidate score with explicit workflow and source-fixture findings."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import argparse, json, hashlib, yaml
from dbt_bench import io
from run_components_v7 import load, expected_for, evaluate
from review_support_v4 import verify_trial, outputs, workflow, STAGES, ck
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase4-review-v1/customer'))

def walk(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)

def check_workflow(task, v, parser, source_fixture):
    name_map = {'dbt-customer-ltv-fix': ['customer_ltv'], 'dbt-customer-cltv-forecasting': ['rpt_customer_cltv_forecast'], 'dbt-fix-email-attribution': ['rpt_email_attribution_fixed'], 'dbt-fix-paid-search-attribution': ['rpt_paid_search_attribution_fixed'], 'dbt-fix-multi-touch-attribution': ['rpt_multi_touch_attribution_fixed'], 'dbt-customer-lifetime-value': ['int_clv_customer_orders', 'int_clv_metrics', 'clv_predictions', 'clv_segments', 'clv_segment_summary', 'clv_cohort_analysis', 'clv_monthly_trends']}
    names = name_map.get(task, list(outputs(v, 'agent-submission')[1]))
    policy = {'project_root': v['request']['project_root'], 'database': '/app/consolidate.duckdb' if task == 'dbt-consolidate' else '/app/database/retail.duckdb', 'models': names, 'deps_required': task in ('dbt-customer-cohort-retention', 'dbt-order-interval-metrics', 'dbt-fix-repeat-purchase-cohort-revenue', 'dbt-consolidate'), 'tests_required': task == 'dbt-consolidate'}
    if task in ('dbt-customer-order-analytics', 'dbt-fix-multi-touch-attribution', 'dbt-fix-paid-search-attribution', 'dbt-customer-lifetime-value', 'dbt-order-interval-metrics', 'dbt-customer-cohort-retention', 'dbt-consolidate'):
        policy['profile'] = 'retail_dw_master'
    if task in ('dbt-fix-email-attribution', 'dbt-rfm-customer-segmentation'):
        policy['profile'] = 'dbt_project'
    if task in ('dbt-rfm-customer-segmentation', 'dbt-loyalty-points-analysis'):
        policy['forbid_project_schema'] = True
    if task in ('dbt-fix-email-attribution', 'dbt-fix-multi-touch-attribution', 'dbt-fix-paid-search-attribution'):
        policy['required_paths'] = {names[0]: 'models/marts/marketing/' + names[0] + '.sql'}
    if task == 'dbt-fix-email-attribution':
        policy['upstream_models'] = ['stg_ga__sessions', 'stg_ga__events', 'int_sessions_events_joined']
    if task == 'dbt-fix-repeat-purchase-cohort-revenue':
        policy.update(upstream_models=['int_sales__orders_enriched'], required_paths={names[0]: 'models/marts/customer/' + names[0] + '.sql'})
    if task == 'dbt-customer-order-analytics':
        policy.update(required_paths={'int_customer_order_metrics': 'models/intermediate/int_customer_order_metrics.sql', 'dim_customer_tiers': 'models/marts/dim_customer_tiers.sql'})
    if task == 'dbt-loyalty-points-analysis':
        policy.update(profile_schema='loyalty_analytics', required_paths={n: ('models/staging/' if n.startswith('stg_') else 'models/intermediate/' if n.startswith('int_') else 'models/marts/') + n + '.sql' for n in names})
    result = workflow(v, policy)
    checks = result['checks']
    final = [c for c in v['commands'] if c['run']['command'][1] in ('run', 'build') and '--project-dir' not in c['run']['command']][-1]
    m = final['manifest']
    files = {k: p.read_text() for k, p in v['final_files'].items() if p.suffix in ('.sql', '.yml', '.yaml')}
    if task in ('dbt-customer-ltv-fix', 'dbt-customer-cohort-retention', 'dbt-order-interval-metrics', 'dbt-customer-lifetime-value'):
        initial = {k: io.file_hash(p) for k, p in v['initial_files'].items() if k.startswith('models/staging/')}
        after = {k: io.file_hash(p) for k, p in v['final_files'].items() if k.startswith('models/staging/')}
        changed = [k for k in initial if initial.get(k) != after.get(k)]
        allowed = {'models/staging/stg_orders__timeline.sql'} if task == 'dbt-order-interval-metrics' else set()
        changed = [k for k in changed if k not in allowed]
        checks['original_staging'] = ck('pass' if not changed else 'gap', 'Actual original supplied staging bytes compared; changed sources require semantic review rather than assumed failure', changed=changed)
    if task == 'dbt-fix-paid-search-attribution':
        checks['schema_macro'] = ck('pass' if 'macros/utils/generate_schema_name.sql' in files and any((n.get('name') == 'generate_schema_name' and n.get('original_file_path') == 'macros/utils/generate_schema_name.sql' for n in m['macros'].values())) else 'fail', 'Required schema naming macro file and compiled macro definition')
    if task in parser['tasks']:
        records = parser['tasks'][task]['models']
        bad = []
        for name, x in records.items():
            nodes = [n for n in m['nodes'].values() if n.get('resource_type') == 'model' and n['name'] == name]
            if len(nodes) != 1 or hashlib.sha256(nodes[0]['compiled_code'].encode()).hexdigest() != x['compiled_sha256']:
                bad.append(name)
        checks['source_parser_binding'] = ck('pass' if not bad else 'fail', 'Independently parsed source SQL is exactly the actual successful manifest content for this clean trial', mismatches=bad)
        if task in ('dbt-fix-email-attribution', 'dbt-fix-multi-touch-attribution'):
            value = records[names[0]]['template']
            checks['adapter_introspection'] = ck(value['status'], 'Sandboxed actual-metadata render executes required adapter calls and exactly matches captured compiled SQL after comment/whitespace normalization', details=value)
        if task == 'dbt-customer-cltv-forecasting':
            ast = records[names[0]]['ast']
            nodes = list(walk(ast))
            functions = {n.get('function_name', '').lower() for n in nodes}
            leaves = {(n.get('schema_name', '').lower(), n.get('table_name', '').lower()) for n in nodes if n.get('type') == 'BASE_TABLE'}
            checks['required_sql_form'] = ck('pass' if 'datediff' in functions and ('main', 'int_sales__orders_enriched') in leaves else 'fail', 'Parsed actual statement uses DATEDIFF and the declared original enriched source')
    if task in source_fixture['tasks']:
        f = source_fixture['tasks'][task]
        node = next((n for n in m['nodes'].values() if n.get('resource_type') == 'model' and n['name'] == names[0]))
        bound = hashlib.sha256(node['raw_code'].encode()).hexdigest() == f['source_sha256']
        states = [x['status'] for x in f['cases']]
        status = 'fail' if not bound or 'fail' in states else 'gap' if 'gap' in states else 'pass'
        checks['conditional_source_behavior'] = ck(status, 'Actual submitted model executed on separately marked tiny purchase/nonpurchase and source-availability fixtures; this does not claim these branches were distinguished by original complete input.', source_hash_bound=bound, cases=f['cases'])
    states = [x['status'] for x in checks.values()]
    reward = 0 if 'fail' in states else None if any((x in states for x in ('gap', 'pending'))) else 1
    result.update(reward=reward, status='unknown' if reward is None else 'scored', policy=policy)
    return result

def run(report, out):
    prior = io.read_json(report)
    task = prior['task_key'].removeprefix('deb__')
    e, a, code = load(task)
    parser = io.read_json(D / 'source-parser-v1.json')
    fixture = io.read_json(D / 'source-fixtures-v1.json')
    result = deepcopy(prior)
    result.update(prior_components=io.evidence_ref(report, R), checker_scope='Original-input full declared table values and types, actual source/process/profile/model workflow, plus explicit extra source-selection fixtures where available. Unresolved business branches stay unknown.')
    for row in result['trials']:
        v = verify_trial(Path(prior['batch']['path']).parent, row['trial'])
        expected, raw = expected_for(task, e, a, v)
        wf = check_workflow(task, v, parser, fixture)
        w = ck('pass' if wf['reward'] == 1 else 'fail' if wf['reward'] == 0 else 'gap', 'Actual task-specific workflow and separately identified conditional source fixtures', details=wf)
        row['workflow'] = wf
        for stage in row['stages']:
            sr, o = outputs(v, stage['stage'])
            stage['audit'] = evaluate(task, a, o, expected, w)
    result['six_stage_audit_rewards'] = [s['audit']['reward'] for t in result['trials'] for s in t['stages']]
    result['checker_code'] = [io.evidence_ref(p, R) for p in [Path(__file__), Path(__file__).with_name('run_components_v7.py'), Path(__file__).with_name('review_support_v4.py'), Path(__file__).with_name('parse_sources_v1.py'), R / 'validation/deb/dbt-fix-email-attribution/phase4_v1/source_fixtures_v1.py', *code, R / 'scripts/phase4_capture.py', R / 'scripts/phase3_capture.py', R / 'scripts/audit_tables.py', R / 'src/dbt_bench/comparison.py']]
    result['source_parser'] = io.evidence_ref(D / 'source-parser-v1.json', R)
    result['source_fixtures'] = io.evidence_ref(D / 'source-fixtures-v1.json', R)
    if out.exists():
        raise ValueError('Immutable sealed score exists')
    out.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'task_key': prior['task_key'], 'audit': result['six_stage_audit_rewards'], 'workflow': [t['workflow']['reward'] for t in result['trials']]}))
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    run(a.report, a.output)

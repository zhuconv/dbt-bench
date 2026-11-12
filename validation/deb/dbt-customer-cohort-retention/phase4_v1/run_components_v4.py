"""Read-only six-stage audit of immutable phase4 authored submissions."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from collections import Counter
import argparse, importlib.util, json, sys, traceback, csv
from dbt_bench import io
from review_support_v3 import verify_trial, raw_rows, outputs, workflow, STAGES
EXTRA = ('dbt-fix-multi-touch-attribution', 'dbt-loyalty-points-analysis', 'dbt-customer-order-analytics', 'dbt-order-interval-metrics')
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase4-review-v1/customer'))

def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

def load(task):
    old = R / 'validation/deb' / task / 'audit_phase2_v1'
    new = old.parent / 'phase4_v1'
    if task in EXTRA:
        path = Path(__file__).with_name('extra_audits_v1.py')
        a = module(path, 'extra_audits')
        return (a, a, [path, old / 'derive_expected.py'])
    if task in ('dbt-customer-ltv-fix', 'dbt-customer-cltv-forecasting'):
        a = module(new / 'audit_v1.py', 'phase4_audit')
        return (a, a, [new / 'audit_v1.py'])
    ep = new / 'expected_v1.py' if task in ('dbt-fix-repeat-purchase-cohort-revenue', 'dbt-customer-lifetime-value') else old / 'expected_v1.py'
    e = module(ep, 'expected_v1')
    ap = old / ('audit_v3.py' if task == 'dbt-customer-lifetime-value' else 'audit_v1.py')
    a = module(ap, 'phase4_audit')
    return (e, a, [ep, ap])

def expected_for(task, e, a, v):
    raw = raw_rows(v)
    if task in EXTRA:
        return (a.derive_for(task, raw), raw)
    if task == 'dbt-consolidate':
        raw = {}
        for source, item in v['initial']['raw_csv'].items():
            with v['capture'].runtime(item['captured']).open() as f:
                raw[source.removesuffix('ads')] = list(csv.DictReader(f))
        return (e.derive(raw), raw)
    if task == 'dbt-customer-lifetime-value':
        return ({'policies': a.policies(raw)}, raw)
    if task in ('dbt-fix-email-attribution', 'dbt-fix-paid-search-attribution'):
        kind = 'email' if task == 'dbt-fix-email-attribution' else 'paid'
        joined = bool(raw.get('joined')) and 't2_event_value' in raw['joined'][0]
        rows, facts = e.derive(kind, raw, joined_available=joined)
        return ({'rows': rows, 'facts': facts}, raw)
    return (e.derive(raw), raw)

def evaluate(task, a, o, e, w):
    if task in EXTRA:
        return a.evaluate_extra(task, o, e, w)
    if task == 'dbt-customer-ltv-fix':
        return a.evaluate(o['customer_ltv'], e, w)
    if task == 'dbt-customer-cltv-forecasting':
        return a.evaluate(o['rpt_customer_cltv_forecast'], e, w)
    if task in ('dbt-fix-email-attribution', 'dbt-fix-paid-search-attribution'):
        kind = 'email' if task == 'dbt-fix-email-attribution' else 'paid'
        name = 'rpt_email_attribution_fixed' if kind == 'email' else 'rpt_paid_search_attribution_fixed'
        item = o[name]
        return a.evaluate(kind, item.get('rows'), e['rows'], structured=item.get('table'), relation_exists=item.get('exists'), relation_type=item.get('relation_type'), workflow=w)
    if task == 'dbt-consolidate':
        o = {k: o[n] for k, n in a.TABLES.items()}
    return a.evaluate(o, e, w)

def run(task, batch, out):
    e, a, code = load(task)
    manifest = io.read_json(batch / 'manifest.json')
    label = next((k for k, v in manifest['tasks'].items() if v['task_key'] == 'deb__' + task))
    results = []
    trials = []
    for rp in (batch / 'jobs').rglob('result.json'):
        cfg = rp.with_name('config.json')
        if not cfg.exists():
            continue
        value = io.read_json(cfg)
        if Path(value.get('task', {}).get('path', '')).name == label:
            trials.append(rp.parent)
    if len(trials) != 2:
        raise ValueError(f'Expected two terminal trials, found {len(trials)}')
    for trial in sorted(trials):
        v = verify_trial(batch, trial)
        expected, raw = expected_for(task, e, a, v)
        snap, observed = outputs(v, 'agent-submission')
        models = list(observed)
        if task == 'dbt-customer-lifetime-value':
            models = list(a.COLS)
        policy = {'project_root': v['request']['project_root'], 'database': '/app/consolidate.duckdb' if task == 'dbt-consolidate' else '/app/database/retail.duckdb', 'models': models, 'deps_required': task in ('dbt-customer-cohort-retention', 'dbt-order-interval-metrics', 'dbt-fix-repeat-purchase-cohort-revenue', 'dbt-consolidate'), 'tests_required': task == 'dbt-consolidate'}
        if task == 'dbt-fix-repeat-purchase-cohort-revenue':
            policy.update(upstream_models=['int_sales__orders_enriched'], required_paths={'rpt_repeat_purchase_cohort_revenue_fixed': 'models/marts/customer/rpt_repeat_purchase_cohort_revenue_fixed.sql'})
        if task == 'dbt-fix-email-attribution':
            policy.update(upstream_models=['stg_ga__sessions', 'stg_ga__events', 'int_sessions_events_joined'])
        wf = workflow(v, policy)
        w = {'status': 'pass' if wf['reward'] == 1 else 'fail', 'reason': 'Actual locked shell source/build/profile/backend provenance; explicit deps/test obligations where stated', 'details': wf}
        stages = []
        for stage in STAGES:
            sr, os = outputs(v, stage)
            answer = evaluate(task, a, os, expected, w)
            component = evaluate(task, a, os, expected, {'status': 'pass', 'reason': 'Component fixture scope: actual workflow scored separately, never candidate eligibility'})
            stages.append({'stage': stage, 'snapshot': sr, 'audit': answer, 'value_component': component, 'outputs': {k: {'rows': len(x['rows']), 'columns': list(x['table'].columns), 'sql_types': x['table'].sql_types, 'relation_type': x['table'].relation_type, 'row_bag_sha256': io.digest(sorted((json.dumps(r, sort_keys=True, default=str) for r in x['rows'])))} for k, x in os.items() if 'rows' in x}})
        results.append({'trial': str(trial), 'result': io.evidence_ref(trial / 'result.json', R), 'execution': io.evidence_ref(trial / 'agent/audit-rebuild/execution.json', R), 'native_reward': v['result'].get('verifier_result', {}).get('rewards'), 'workflow': wf, 'facts': expected.get('facts', {}), 'stages': stages, 'captured_input_rows': {k: len(x) for k, x in raw.items()}})
    refs = code + [Path(__file__), Path(__file__).with_name('review_support_v3.py'), R / 'scripts/phase4_capture.py', R / 'scripts/phase3_capture.py', R / 'scripts/audit_tables.py', R / 'src/dbt_bench/comparison.py']
    result = {'schema_version': 1, 'task_key': 'deb__' + task, 'batch': io.evidence_ref(batch / 'manifest.json', R), 'label': label, 'checker_code': [io.evidence_ref(p, R) for p in refs], 'model_requests': 0, 'native_rewards_modified': False, 'trials': results, 'six_stage_audit_rewards': [s['audit']['reward'] for t in results for s in t['stages']], 'six_stage_component_rewards': [s['value_component']['reward'] for t in results for s in t['stages']]}
    result['repeat_comparison'] = {'status': 'pass' if results[0]['stages'][0]['outputs'] == results[1]['stages'][0]['outputs'] else 'fail', 'scope': 'Two independently initialized builds, exact full row multisets and all captured columns/types/materialization; no unstable columns omitted.'}
    if out.exists():
        raise ValueError('Immutable output already exists')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str) + '\n')
    return result
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--task', required=True)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    x = p.parse_args()
    v = run(x.task, x.batch, x.output)
    print(json.dumps({k: v[k] for k in ('task_key', 'six_stage_audit_rewards', 'six_stage_component_rewards', 'repeat_comparison')}))

"""Read-only revision scoring of existing exact-identity clean formal trials."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import argparse, importlib.util, json, sys
from copy import deepcopy
from dbt_bench import io
ROOT = Path.cwd()
SHARED = ROOT / 'validation/deb/dbt-customer-cohort-retention/phase4_v1'
sys.path.insert(0, str(SHARED))
from review_support_v4 import verify_trial, raw_rows, outputs, ck, workflow
from seal_candidate_v2 import check_workflow, walk
BASE = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase4-review-v1/customer'))

def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

def load(task):
    here = ROOT / 'validation/deb' / task / 'phase4_v2'
    module(Path(__file__).with_name('common.py'), 'common')
    if task == 'dbt-rfm-customer-segmentation':
        e = module(here.parent / 'audit_phase2_v1/expected_v1.py', 'expected_v1')
    elif task == 'dbt-customer-lifetime-value':
        e = module(here / 'expected_v1.py', 'expected_v1')
    elif task == 'dbt-loyalty-points-analysis':
        e = module(SHARED / 'extra_audits_v4.py', 'revision_extra_deriver')
    else:
        raise ValueError('Task not assigned to this revision')
    a = module(here / ('audit_v2.py' if task == 'dbt-customer-lifetime-value' else 'audit_v1.py'), 'revised_audit')
    return (e, a)

def expected_for(task, e, raw):
    if task == 'dbt-rfm-customer-segmentation':
        return e.derive(raw) | {'raw': raw}
    if task == 'dbt-loyalty-points-analysis':
        return {'derived': e.derive_for(task, raw), 'raw': raw}
    return {'raw': raw}

def revision_workflow(task, v, parser, source_fixture):
    w = check_workflow(task, v, parser, source_fixture)
    if task == 'dbt-rfm-customer-segmentation':
        w['policy']['profile_schema'] = 'rfm_analytics'
        w['checks'].update(workflow(v, w['policy'])['checks'])
    records = parser['tasks'][task]['models']
    if task == 'dbt-rfm-customer-segmentation':
        windows = [n for n in walk(records['rfm_scores']['ast']) if n.get('type') == 'WINDOW_NTILE']
        facts = []
        for n in windows:
            orders = n.get('orders', [])
            five = bool(n.get('children')) and n['children'][0].get('value', {}).get('value') == 5
            secondary = any((x.get('expression', {}).get('column_names', [])[-1:] == ['customer_id'] for x in orders[1:]))
            facts.append({'five': five, 'unique_customer_secondary': secondary, 'order_count': len(orders)})
        w['checks']['deterministic_ntile'] = ck('fail' if len(facts) != 3 or any((not f['five'] or f['order_count'] < 2 for f in facts)) else 'pass' if all((f['unique_customer_secondary'] for f in facts)) else 'gap', 'Actually parsed, current-manifest-bound NTILE windows use 5 buckets and original unique customer IDs as deterministic secondary keys; alternate forms need reviewed uniqueness proof.', windows=facts)
    if task == 'dbt-customer-lifetime-value':
        lag = [n for n in walk(records['int_clv_customer_orders']['ast']) if n.get('type') == 'WINDOW_LAG']
        w['checks']['order_gap_lag'] = ck('pass' if lag else 'fail', 'Actual compiled per-order model contains required LAG; independent original-input rows separately validate its values.', lag_count=len(lag))
    if task in ('dbt-rfm-customer-segmentation', 'dbt-loyalty-points-analysis'):
        manifest = next((c['manifest'] for c in reversed(v['commands']) if c['manifest'] is not None and c['run']['command'][1] in ('run', 'build')))
        expected_sources = {'stg_orders__orders': 'orders', 'stg_customer__customers': 'customers'} if task == 'dbt-rfm-customer-segmentation' else {'stg_lp_transactions': 'loyalty_points_transactions', 'stg_lp_programs': 'loyalty_programs', 'stg_lp_customers': 'customers'}
        source_nodes = manifest['sources']
        models = manifest['nodes']
        details = []

        def reachable(uid, seen=()):
            if uid in seen:
                raise ValueError('Recursive staging graph')
            if uid in source_nodes:
                n = source_nodes[uid]
                return {(n['schema'].lower(), n['identifier'].lower(), n['original_file_path'])}
            n = models[uid]
            return {s for parent in n.get('depends_on', {}).get('nodes', []) for s in reachable(parent, seen + (uid,))}
        for name, source in expected_sources.items():
            candidates = [(uid, n) for uid, n in models.items() if n.get('resource_type') == 'model' and n['name'] == name]
            found = reachable(candidates[0][0]) if len(candidates) == 1 else set()
            match = any((schema == 'main' and identifier == source and (path in v['final_files']) for schema, identifier, path in found))
            details.append({'model': name, 'expected_source': ['main', source], 'reachable_source_declarations': sorted(found), 'matched': match})
        w['checks']['source_declarations'] = ck('pass' if all((x['matched'] for x in details)) else 'fail', 'Fresh dbt manifest graph binds each required staging model to its declared raw source and an actual delivered source YAML file; source values are independently compared in the table audit.', models=details)
    states = [x['status'] for x in w['checks'].values()]
    w['reward'] = 0 if 'fail' in states else None if any((s in states for s in ('gap', 'pending'))) else 1
    w['status'] = 'unknown' if w['reward'] is None else 'scored'
    return w

def run(task, out):
    previous = BASE / task / 'candidate-v1.json'
    prior = io.read_json(previous)
    result = deepcopy(prior)
    e, a = load(task)
    parser = io.read_json(BASE / 'source-parser-v1.json')
    fixture = io.read_json(BASE / 'source-fixtures-v1.json')
    for row in result['trials']:
        v = verify_trial(Path(prior['batch']['path']).parent, row['trial'])
        expected = expected_for(task, e, raw_rows(v))
        wf = revision_workflow(task, v, parser, fixture)
        row['workflow'] = wf
        w = ck('pass' if wf['reward'] == 1 else 'fail' if wf['reward'] == 0 else 'gap', 'Actual existing formal source/build/path/backend plus parsed task-specific SQL obligations', details=wf)
        for stage in row['stages']:
            sr, o = outputs(v, stage['stage'])
            stage['audit'] = a.evaluate(o, expected, w)
            stage['value_component'] = a.evaluate(o, expected, ck('pass', 'Typed fixture component; actual workflow is separately scored'))
    result['six_stage_audit_rewards'] = [s['audit']['reward'] for t in result['trials'] for s in t['stages']]
    result['six_stage_component_rewards'] = [s['value_component']['reward'] for t in result['trials'] for s in t['stages']]
    refs = [Path(__file__), Path(__file__).with_name('common.py'), ROOT / 'validation/deb' / task / 'phase4_v2' / ('audit_v2.py' if task == 'dbt-customer-lifetime-value' else 'audit_v1.py')]
    if task == 'dbt-customer-lifetime-value':
        refs.append(ROOT / 'validation/deb' / task / 'phase4_v2/expected_v1.py')
    result['checker_code'] = prior['checker_code'] + [io.evidence_ref(p, ROOT) for p in refs]
    result.update(previous_candidate=io.evidence_ref(previous, ROOT), revision='phase4-v2', checker_scope='Current unchanged blind submissions, original native scores preserved. Independently defined presentation/default policy; all seven CLV or five RFM/loyalty models covered by declared projections and actual source workflow.')
    if out.exists():
        raise ValueError('Preserve immutable revision output')
    out.parent.mkdir(parents=True, exist_ok=True)
    io.write_json(out, result)
    print(task, result['six_stage_audit_rewards'])
    return result
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--task', required=True)
    p.add_argument('--output', type=Path, required=True)
    x = p.parse_args()
    run(x.task, x.output)

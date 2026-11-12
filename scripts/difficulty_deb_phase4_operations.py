"""Apply nine frozen phase4 audits to actual model delivery, never author SQL.

Pure data always comes from the pre-native snapshot. Source/compiled facts come
from a clean reconstruction of byte-identical delivered sources. Only paired
original Qwen events can attest actions prescribed during the model attempt.
"""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import importlib.util
import hashlib
import json
import sys
from dbt_bench import io
from audit_tables import load_observer_table, decode
from mechanical_observer import encode
from difficulty_deb_binding_v3 import verify
from difficulty_deb_registry_v2 import load
from difficulty_deb_workflow_v3 import spec_workflow, direct_workflow, declared_action_gate
ROOT = Path(__file__).resolve().parents[1]
OPS = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase4-review-v1/operations'))
TASKS = {'dbt-customer-risk-scoring', 'dbt-daily-order-summary', 'dbt-fraud-detection-model', 'dbt-fulfillment-sla', 'dbt-inventory-analysis', 'dbt-product-category-analytics', 'dbt-retail-task-01-channel-revenue', 'dbt-supplier-scorecard', 'dbt-three-way-matching'}
NO_EXPORT = {'dbt-customer-risk-scoring', 'dbt-product-category-analytics', 'dbt-supplier-scorecard'}
FAILURE_REQUIREMENTS = {'dbt-customer-risk-scoring': {'risk_analytics.customer_risk_scores': 'customer_id customer_name late_payment_count return_count chargeback_count risk_score risk_tier'.split()}, 'dbt-daily-order-summary': {'daily_analytics.daily_order_summary': ['order_date', 'order_count', 'total_revenue']}, 'dbt-fraud-detection-model': {'fraud_analytics.fraud_flagged_orders': 'order_id customer_id ordered_at grand_total total_items orders_same_day billing_state shipping_state is_high_value is_bulk_order is_velocity_fraud is_address_mismatch is_first_order_high_value has_chargeback_history fraud_flags_count fraud_risk_level'.split()}, 'dbt-fulfillment-sla': {'main.' + n: [] for n in ['int_shipment_times', 'fulfillment_sla', 'carrier_performance', 'warehouse_performance', 'fulfillment_summary']}, 'dbt-inventory-analysis': {'inventory_analytics.fct_warehouse_inventory': 'warehouse_id warehouse_name warehouse_type city state_province total_quantity inventory_value distinct_product_count avg_unit_cost max_single_item_value inventory_concentration'.split()}, 'dbt-product-category-analytics': {'sales_analytics.int_product_sales': [], 'sales_analytics.fct_category_performance': 'category_id category_name category_level total_orders total_units_sold total_revenue total_cost gross_profit profit_margin avg_order_value'.split()}, 'dbt-retail-task-01-channel-revenue': {'main.ts_sales__channel_revenue_margin_monthly': 'month_start sales_year sales_month channel_key channel_id channel_code channel_name channel_type order_count revenue profit discount_amount margin_rate avg_order_value discount_rate dbt_updated_at'.split()}, 'dbt-supplier-scorecard': {'supplier_analytics.fct_supplier_metrics': 'supplier_id supplier_code supplier_name supplier_type total_pos on_time_pos on_time_delivery_rate avg_days_late total_quantity_received total_quantity_accepted total_quantity_rejected overall_acceptance_rate overall_defect_rate total_po_value total_invoice_value overall_cost_variance overall_cost_variance_pct'.split(), 'supplier_analytics.fct_supplier_scorecard': 'supplier_id supplier_code supplier_name supplier_type on_time_delivery_rate on_time_score acceptance_rate quality_score cost_variance_pct cost_score composite_score performance_tier'.split()}, 'dbt-three-way-matching': {'main.three_way_match': 'match_id po_id po_number po_line_id supplier_id supplier_name supplier_rating variant_id sku po_quantity po_unit_price po_line_total receipt_quantity accepted_quantity rejected_quantity rejection_rate invoice_quantity invoice_unit_price invoice_line_total quantity_variance quantity_variance_pct price_variance price_variance_pct total_variance expected_amount quantity_match_status price_match_status overall_match_status exception_reasons exception_count risk_score risk_category recommended_action days_to_invoice days_receipt_to_invoice receipt_count invoice_count'.split()}}

def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

def save(path, value, root):
    if path.exists():
        raise ValueError('Preserve difficulty evidence: ' + str(path))
    io.write_json(path, json.loads(json.dumps(value, default=encode, allow_nan=False)))
    return io.evidence_ref(path, root)

def actual_outputs(ref, key, root):
    snap = load(ref, root)
    if snap['task_key'] != key or snap['stage'] != 'agent-submission':
        raise ValueError('Wrong actual submission')
    result = {}
    for item in snap['outputs']:
        spec = item['specification']
        name = spec['relation'].split('.', 1)[1].lower()
        if name in result:
            raise ValueError('Ambiguous declared relation name')
        if item.get('error'):
            e = item['error']
            missing = e.get('type') == 'CatalogException' and 'does not exist' in e.get('message', '')
            result[name] = {'exists': False} if missing else {'capture_error': e}
            continue
        t = load_observer_table(ref, task_key=key, database=spec['database'], relation=spec['relation'], stage='agent-submission', root=root)
        if len({c.lower() for c in t.columns}) != len(t.columns):
            raise ValueError('Ambiguous columns')
        t = replace(t, columns=tuple((c.lower() for c in t.columns)), physical_columns=tuple((c.lower() for c in t.physical_columns)), sql_types={k.lower(): v for k, v in t.sql_types.items()}, physical_sql_types={k.lower(): v for k, v in t.physical_sql_types.items()}, rows=[{k.lower(): v for k, v in r.items()} for r in t.rows])
        result[name] = {'rows': t.rows, 'table': t, 'sql_types': t.sql_types, 'relation_type': t.relation_type, 'exists': True}
    return result

def rows(d, item):
    values = [dict(zip([x.lower() for x in item['columns']], map(decode, json.loads(line)))) for line in d['capture'].runtime(item).read_text().splitlines()]
    if len(values) != item['rows']:
        raise ValueError('Capture row count changed')
    return values

def parsed(context, probe):
    d = context['fresh']
    command = next((c for c in reversed(d['commands']) if c['manifest'] is not None and c['run']['command'][1] in ('run', 'build')))
    selected = []
    for r in probe['parsed']:
        if r['manifest']['sha256'] != io.file_hash(command['manifest_path']):
            continue
        node = command['manifest']['nodes'][r['unique_id']]
        if hashlib.sha256(node['compiled_code'].encode()).hexdigest() != r['compiled_sha256']:
            raise ValueError('Parsed source changed')
        selected.append({**r, 'name': node['name'], 'model': node['name']})
    return (selected, command)

def action_gate(task, events, project):
    if task not in NO_EXPORT:
        return declared_action_gate('deb__' + task, events, project)
    flag = events.get('echo_before_first_write')
    reward = 1 if flag is True else 0 if flag is False else None
    return {'reward': reward, 'status': 'unknown' if reward is None else 'scored', 'reason': 'This instruction requires original echo before code, but does not require exporting DBT_PROFILES_DIR. Only actual paired tool evidence establishes chronology.', 'source_logs': events.get('source_logs', [])}

def workflow_facts(context, observed, events, output, root):
    reference = spec_workflow(context, observed, events, output, root)
    f = load(reference, root)
    d = context['fresh']
    f.update(trusted_initial_capture_complete=True, initial_project_exists=d['initial'].get('project_exists'), initial_project_contains_dbt_files='dbt_project.yml' in d['initial_files'])
    if events.get('echo_before_first_write') is True:
        echoes = [{'event': 'live_echo_finished', 'stdout': 'duckdb\n', 'returncode': 0, 'monotonic_ns': x['result_index']} for x in events['echo_calls']]
        writes = [{'event': 'task_model_written', 'monotonic_ns': x['call_index']} for x in events['write_calls']]
        f['trusted_candidate_events'] = echoes + writes
        f['trusted_event_history_complete'] = True
        f['events'] = [{'kind': 'live_backend_echo', 'value': 'duckdb', 'exit_code': 0, 'monotonic_ns': x['result_index']} for x in events['echo_calls']] + [{'kind': 'candidate_code_write', 'monotonic_ns': x['call_index']} for x in events['write_calls']]
    return f

def compiled(context, command):
    d = context['fresh']
    hooks = command['hooks']
    capture = next((h for h in reversed(hooks) if h.get('compiled')), None)
    if capture is None:
        capture = d['final']
    out = {}
    for name, item in capture.get('compiled', {}).items():
        uid = item['unique_id']
        node = command['manifest']['nodes'][uid]
        if d['capture'].runtime(item['sql']).read_text() != node['compiled_code']:
            raise ValueError('Compiled result not current submitted source')
        out[name] = rows(d, item)
    return (out, capture)

def original_gate_check(gate):
    return {'status': 'pass' if gate['reward'] == 1 else 'fail' if gate['reward'] == 0 else 'gap', 'reason': gate['reason'], 'details': gate}

def fixed_input(context, task, root):
    d = context['fresh']
    raw = {name: rows(d, item) for name, item in d['initial']['raw'].items()}
    if task in ('dbt-inventory-analysis', 'dbt-product-category-analytics'):
        p = io.read_json(root / 'validation/deb' / task / 'audit_phase2_v1/policy.json')['independent_inputs']
        fixed = load(p, root)
        if d['execution']['initial_input_hashes'] != [{'path': '/app/database/retail.duckdb', 'sha256': fixed['initial_database_sha256']}]:
            raise ValueError('Fixed raw input version changed')
        return (raw, fixed)
    return (raw, None)

def repeat_table(context, name, observed):
    """Fresh successive build evidence; never a second model attempt/action."""
    d = context['fresh']
    builds = [c for c in d['commands'] if c['manifest'] is not None and c['run']['command'][1] in ('run', 'build')]
    if len(builds) < 2:
        return None
    cap = next((h for h in reversed(builds[-1]['hooks']) if name in h.get('physical_outputs', {})), None)
    if cap is None:
        return None
    item = cap['physical_outputs'][name]
    t = observed.get(name, {}).get('table')
    if t is None:
        return None
    names = tuple((x.lower() for x in item['columns']))
    types = {x[0].lower(): x[1] for x in item['sql_schema']}
    return replace(t, columns=names, physical_columns=names, sql_types=types, physical_sql_types=types, rows=rows(d, item))

def legacy_values(context, task, request, observed, events, probe, output, root, gate):
    folder = root / 'validation/deb' / task
    old = folder / 'audit_phase2_v1'
    sys.path[:0] = [str(folder / 'phase4_v1'), str(old)]
    raw, _ = fixed_input(context, task, root)
    pr, command = parsed(context, probe)
    logical, capture = compiled(context, command)
    f = workflow_facts(context, {'probe': request['probe']}, events, output, root)
    f['parsed_compiled_sql'] = {r['model']: r['ast'] for r in pr}
    f['parser_evidence'] = [request['probe']]
    if task == 'dbt-customer-risk-scoring':
        e = module(old / 'expected.py', 'ops_risk_expected')
        a = module(old / 'audit_v5.py', 'ops_risk_audit')
        w = module(old / 'workflow_v2.py', 'ops_risk_workflow')
        expected, statistics = e.derive(raw)
        f['compiled_staging_rows'] = {n: r for n, r in logical.items() if n.startswith('stg_')}
        wf = w.evaluate('risk', f)
        actual = {n: o['rows'] for n, o in observed.items() if 'rows' in o}
        for uid, node in command['manifest']['nodes'].items():
            if node.get('resource_type') == 'model' and node.get('config', {}).get('materialized') == 'ephemeral' and node['name'].startswith('stg_') and (node['name'] in logical):
                actual[node['name']] = logical[node['name']]
        repeated = {n: t.rows for n in actual if (t := repeat_table(context, n, observed)) is not None}
        result = a.evaluate('risk', actual, expected, structured={n: o['table'] for n, o in observed.items() if 'table' in o}, workflow=wf, repeated=repeated or None)
        profile = module(folder / 'phase4_v1/profile_runtime_v2.py', 'ops_profile')
        result = profile.extend(result, f, 'risk')
    elif task == 'dbt-daily-order-summary':
        e = module(old / 'expected.py', 'ops_daily_expected')
        a = module(old / 'audit_v3.py', 'ops_daily_audit')
        expected, statistics = e.derive(raw['orders'])
        name = 'daily_order_summary'
        result = a.evaluate(observed.get(name, {}).get('table'), expected, workflow=f, ordered_result=logical.get(name), repeated=repeat_table(context, name, observed))
        profile = module(root / 'validation/deb/dbt-customer-risk-scoring/phase4_v1/profile_runtime_v2.py', 'ops_profile')
        result = profile.extend(result, f, 'daily')
    else:
        e = module(old / 'expected_v2.py', 'ops_fraud_expected')
        a = module(old / 'audit_v2.py', 'ops_fraud_audit')
        w = module(folder / 'phase4_v1/run_audit_v1.py', 'ops_fraud_workflow')
        expected, statistics = e.build(raw)
        wf = w.workflow('fraud', f)
        result = a.evaluate('fraud', observed, [expected], workflow=wf, compiled=logical)
    result.update(statistics=statistics, actual_workflow_facts=save(output / 'source-workflow-facts.json', f, root))
    return result

def request_values(context, task, request, events, output, root):
    d = context['fresh']
    _, fixed = fixed_input(context, task, root)
    folder = root / 'validation/deb' / task / 'phase4_v1'
    sys.path[:0] = [str(folder), str(folder.parent / 'audit_phase2_v1')]
    wf = direct_workflow(context, request['snapshot'], events, output, root)
    spec = {'task_key': request['task_key'], 'initial_database_sha256': fixed['initial_database_sha256'], 'snapshot': request['snapshot'], 'stage': 'agent-submission', 'workflow': wf}
    if task == 'dbt-product-category-analytics':
        manifest = next((c['manifest'] for c in reversed(d['commands']) if c['manifest'] is not None))
        names = {n['name'] for n in manifest['nodes'].values() if n.get('resource_type') == 'model'}
        choices = names & {'stg_categories', 'stg_product_categories'}
        if len(choices) == 1:
            spec['category_stage_models'] = {'lines': 'stg_order_lines', 'products': 'stg_products', 'categories': choices.pop(), 'orders': 'stg_orders', 'intermediate': 'int_product_sales', 'final': 'fct_category_performance'}
        o = actual_outputs(request['snapshot'], request['task_key'], root).get('int_product_sales', {})
        cols = set(o.get('table').columns) if o.get('table') else set()
        revenue = cols & {'revenue', 'line_revenue'}
        cost = cols & {'cost', 'line_cost'}
        if len(revenue) == len(cost) == 1:
            spec['category_intermediate_columns'] = {'product_id': 'product_id', 'category_id': 'category_id', 'revenue': revenue.pop(), 'cost': cost.pop()}
    checker = module(context['qualified']['checker'], 'ops_request_checker')
    result = checker.run(spec, root)
    result['current_checker_request'] = save(output / 'checker-input.json', spec, root)
    return result

def custom_values(context, task, request, observed, events, probe, output, root, gate):
    d = context['fresh']
    raw, _ = fixed_input(context, task, root)
    pr, command = parsed(context, probe)
    folder = root / 'validation/deb' / task / 'phase4_v1'
    sys.path.insert(0, str(folder))
    files = {n: p.read_text() for n, p in d['final_files'].items() if p.suffix in ('.sql', '.yaml', '.yml')}
    if task == 'dbt-fulfillment-sla':
        support = module(root / 'validation/deb/dbt-customer-cohort-retention/phase4_v1/review_support_v3.py', 'ops_support')
        names = ['int_shipment_times', 'fulfillment_sla', 'carrier_performance', 'warehouse_performance', 'fulfillment_summary']
        policy = {'project_root': '/app/dbt_models_duckdb', 'database': '/app/database/retail.duckdb', 'profile': 'retail_dw_master', 'models': names, 'required_paths': {n: ('models/intermediate/' if n.startswith('int_') else 'models/marts/analytics/') + n + '.sql' for n in names}}
        wf = support.workflow(d, policy)
        wf['checks']['backend'] = original_gate_check(gate)
        try:
            inspect = module(folder / 'workflow_v1.py', 'ops_fulfillment_form').inspect([r for r in pr if r['model'] in names], command['manifest'], observed)
        except (KeyError, ValueError, AssertionError, TypeError) as exc:
            inspect = {'status': 'gap', 'reason': 'Actual source-form evidence cannot be resolved: ' + str(exc)}
        wf['checks']['parsed_semantics'] = inspect
        states = [x['status'] for x in wf['checks'].values()]
        w = {'status': 'fail' if 'fail' in states else 'gap' if any((x in states for x in ('gap', 'pending'))) else 'pass', 'details': wf}
        expected, statistics = module(folder / 'expected_v5.py', 'ops_fulfillment_expected').build_variants(raw)
        a = module(context['qualified']['checker'], 'ops_fulfillment_audit')
        logical, _ = compiled(context, command)
        result = a.evaluate('fulfillment', observed, expected, workflow=w, compiled=logical)
    else:
        sys.path.insert(0, str(OPS))
        wmod = module(OPS / 'custom_workflow_v1.py', 'ops_custom_workflow')
        initial = {t['relative_path'] + '/' + x['path']: x['sha256'] for t in d['execution']['initial_trees'] for x in t['files']}
        final = {t['relative_path'] + '/' + x['path']: x['sha256'] for t in d['execution']['final_trees'] for x in t['files']}
        changed = [n for n, h in initial.items() if final.get(n) != h]
        cfgsame = 'dbt_project.yml' not in d['initial_files'] or io.file_hash(d['initial_files']['dbt_project.yml']) == io.file_hash(d['final_files']['dbt_project.yml'])
        facts = {'files': files, 'manifest': command['manifest'], 'run_results': command['run_results'], 'environment': d['execution']['environment'], 'process': command['run'], 'fresh_bound': True, 'echo': None, 'started_at_utc': d['execution']['started_at_utc'], 'parsed_records': pr, 'all_initial_files_unchanged': not changed and cfgsame, 'initial_changes': changed, 'initial_target_absent': 'models/marts/three_way_match.sql' not in initial}
        try:
            w = wmod.evaluate(task, facts)
        except (KeyError, ValueError, AssertionError, StopIteration, TypeError) as exc:
            w = {'status': 'gap', 'details': {'source_form': {'status': 'gap', 'reason': str(exc)}, 'backend': {'status': 'gap', 'reason': 'No original action substituted'}}}
        unavailable = w['details']['backend']
        w['details']['backend'] = original_gate_check(gate)
        w['unavailable_driver_backend_component'] = unavailable
        states = [x['status'] for x in w['details'].values()]
        w['status'] = 'fail' if 'fail' in states else 'gap' if any((s in states for s in ('pending', 'gap'))) else 'pass'
        channel = task == 'dbt-retail-task-01-channel-revenue'
        filename = 'expected_v3.py' if task == 'dbt-three-way-matching' else 'expected_v1.py'
        e = module(folder / filename, 'ops_custom_expected')
        expected = e.from_captured(raw) if channel else e.derive(raw)
        statistics = expected[1] if channel else expected.get('facts', {})
        a = module(context['qualified']['checker'], 'ops_custom_audit')
        snap = load(request['snapshot'], root)
        result = a.evaluate(observed, expected, workflow=w, **{'timestamp_window': {'start': snap['started_at_utc'], 'end': snap['finished_at_utc']}} if channel else {})
    result.update(statistics=statistics, actual_workflow=save(output / 'actual-workflow.json', w, root))
    return result

def run(request, root=ROOT):
    root = Path(root)
    context = verify(request['audit_dir'], root)
    key = request['task_key']
    task = key.removeprefix('deb__')
    if task not in TASKS or context['binding']['task_key'] != key or request.get('stage') != 'agent-submission':
        raise ValueError('Wrong operations task/snapshot stage')
    output = Path(request['output_dir'])
    observed = actual_outputs(request['snapshot'], key, root)
    events = load(request['original_events'], root)
    probe = load(request['probe'], root)
    gate = action_gate(task, events, context['fresh']['request']['project_root'])
    if task in ('dbt-customer-risk-scoring', 'dbt-daily-order-summary', 'dbt-fraud-detection-model'):
        result = legacy_values(context, task, request, observed, events, probe, output, root, gate)
    elif task in ('dbt-inventory-analysis', 'dbt-product-category-analytics'):
        result = request_values(context, task, request, events, output, root)
    else:
        result = custom_values(context, task, request, observed, events, probe, output, root, gate)
    deps = [Path(__file__), root / 'scripts/difficulty_deb_operations_collect.py', root / 'scripts/difficulty_deb_workflow_v3.py']
    if task in ('dbt-supplier-scorecard', 'dbt-three-way-matching', 'dbt-retail-task-01-channel-revenue'):
        deps += [OPS / 'custom_workflow_v1.py', OPS / 'actual_core_v2.py']
    if task == 'dbt-fulfillment-sla':
        deps += [root / 'validation/deb/dbt-customer-cohort-retention/phase4_v1/review_support_v3.py']
    result.update(original_action_gate=gate, actual_snapshot=request['snapshot'], original_events=request['original_events'], source_binding=context['manifest']['source_binding'], adapter_code_refs=[io.evidence_ref(p, root) for p in deps], scope='Frozen task semantics on actual pre-native rows. Byte-bound delivered source is reconstructed independently; model action obligations use paired original Qwen logs only. Missing evidence is not a pass; reconstruction never replaces an incorrect submitted table.')
    return result

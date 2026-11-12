"""Actual submitted-output audits for the three qualified churn/RFM tasks.

The immutable phase4 recomputation supplies business semantics. A clean build
supplies trusted source artifacts, never evidence of the model's actions.
"""
from collections import Counter
from dataclasses import replace
from pathlib import Path
import json
import posixpath
import re
import shlex
import sys
import yaml

from dbt_bench import io
from audit_tables import load_observer_table
from difficulty_deb_binding_v3 import verify
from difficulty_deb_registry_v2 import load as checked_json
from difficulty_deb_events import shell_result
from difficulty_deb_workflow_v3 import declared_action_gate
from phase4_deb_recompute_v2 import warning, cohorts, rfm2, compare_outputs
from phase4_customer4_review_v3 import policy

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'validation/deb/dbt-customer-cohort-retention/phase4_v1'
sys.path.insert(0, str(SHARED))
from review_support_v3 import raw_rows, workflow, ck, env_value

# Only explicit final deliverables from the qualified contract/recomputation.
# In particular, these are not guesses based on reference helper model names.
FAILURE_REQUIREMENTS = {
    'dbt-customer-churn-early-warning': {
        'analytics.rpt_customer_churn_early_warning_fixed': [
            'customer_id', 'week_start', 'first_order_date', 'last_order_date',
            'days_since_last_order', 'order_count_90d', 'lifetime_orders',
            'lifetime_revenue', 'churn_risk_tier']},
    'dbt-customer-churn-cohorts': {
        'main.customer_cohorts': ['customer_id', 'cohort_month', 'first_transaction_date',
            'first_transaction_amount', 'signup_to_first_purchase_days', 'acquisition_channel'],
        'main.cohort_retention': ['cohort_month', 'period_number', 'period_month',
            'cohort_size', 'active_customers', 'retained_customers', 'retention_rate',
            'period_revenue', 'cumulative_revenue'],
        'main.customer_clv': ['customer_id', 'cohort_month', 'total_transactions',
            'total_revenue', 'avg_transaction_value', 'customer_lifespan_months',
            'monthly_revenue_rate', 'months_since_last_transaction', 'churn_probability',
            'predicted_clv_12m', 'customer_tier']},
    'dbt-rfm-customer-segmentation-2': {
        'main_rfm_analytics.rfm_segments': ['customer_id', 'first_order_date',
            'last_order_date', 'customer_tenure_days', 'recency_days', 'total_orders',
            'total_spent', 'avg_order_value', 'avg_days_between_orders',
            'orders_q1_2024', 'orders_q2_2024', 'orders_q3_2024', 'orders_q4_2024',
            'quarterly_trend', 'spending_velocity', 'recency_score', 'recency_percentile',
            'frequency_score', 'frequency_percentile', 'monetary_score',
            'monetary_percentile', 'rfm_score', 'rfm_segment', 'churn_probability',
            'lifetime_value_estimate'],
        'main_rfm_analytics.rpt_segment_summary': ['rfm_segment', 'customer_count',
            'pct_of_total', 'total_revenue', 'avg_revenue_per_customer',
            'avg_churn_probability', 'total_lifetime_value', 'avg_lifetime_value',
            'churning_customers', 'accelerating_customers'],
        'main_rfm_analytics.rpt_customer_recommendations': ['customer_id', 'rfm_segment',
            'rfm_score', 'quarterly_trend', 'churn_probability', 'risk_level',
            'intervention_type', 'recommended_action', 'estimated_value',
            'urgency_days', 'priority_score'],
        'main_rfm_analytics.rfm_cohort_retention': ['cohort_month', 'months_since_first',
            'cohort_size', 'retained_customers', 'retention_rate', 'cohort_revenue',
            'avg_order_value']},
}
TASKS = set(FAILURE_REQUIREMENTS)
REPEAT_REQUIRED = {'dbt-customer-churn-cohorts', 'dbt-customer-churn-early-warning'}


def actual_outputs(ref, key, root):
    snapshot = checked_json(ref, root)
    if snapshot['task_key'] != key or snapshot['stage'] != 'agent-submission':
        raise ValueError('Wrong actual submission snapshot')
    observed, unresolved = {}, {}
    required = FAILURE_REQUIREMENTS[key.removeprefix('deb__')]
    seen = set()
    for item in snapshot['outputs']:
        spec = item['specification']; relation = spec['relation'].lower()
        if relation not in required:
            continue
        if relation in seen:
            raise ValueError('Duplicate declared output observation')
        seen.add(relation)
        name = relation.split('.', 1)[1]
        if item.get('error'):
            # An observer error does not prove that a deliverable is absent.
            # The separate qualified failure evaluator probes actual schema.
            unresolved[name] = item['error']; continue
        table = load_observer_table(ref, task_key=key, database=spec['database'],
            relation=spec['relation'], stage='agent-submission', root=root)
        if len({x.lower() for x in table.columns}) != len(table.columns):
            raise ValueError('Ambiguous actual output columns')
        table = replace(table, columns=tuple(x.lower() for x in table.columns),
            physical_columns=tuple(x.lower() for x in table.physical_columns),
            sql_types={k.lower(): v for k, v in table.sql_types.items()},
            physical_sql_types={k.lower(): v for k, v in table.physical_sql_types.items()},
            rows=[{k.lower(): v for k, v in row.items()} for row in table.rows])
        observed[name] = {'rows': table.rows, 'table': table, 'relation_type': table.relation_type}
    for relation in required.keys() - seen:
        unresolved[relation.split('.', 1)[1]] = {'reason': 'Declared relation not captured'}
    return observed, unresolved


def upstream_commands(command, cwd):
    """Recognize a restricted successful shell chain without executing it.

    A returned True means a dbt invocation in the reference project explicitly
    included the enriched model. Its actual successful log is checked separately.
    Unsupported shell remains unresolved, including invoking an opaque script.
    """
    if not isinstance(command, str) or any(x in command for x in ('\n', ';', '|', '`', '$(', '>', '<')):
        return None
    try:
        stages = [shlex.split(part.strip()) for part in command.split('&&')]
    except ValueError:
        return None
    location = cwd if isinstance(cwd, str) and cwd.startswith('/') else None
    recognized = False
    for argv in stages:
        if not argv or any('&' in x for x in argv):
            return None
        if argv[0] == 'cd' and len(argv) == 2:
            if '$' in argv[1] or '~' in argv[1]: return None
            location = argv[1] if argv[1].startswith('/') else posixpath.normpath(posixpath.join(location, argv[1])) if location else None
            continue
        if argv[0] == 'export':
            if len(argv) != 2 or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*=[^$`]*', argv[1]): return None
            continue
        while argv and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*=.*', argv[0]): argv.pop(0)
        if not argv or Path(argv[0]).name != 'dbt' or len(argv) < 2 or argv[1] not in ('run', 'build'):
            return None
        if any('$' in a or '~' in a for a in argv) or '--exclude' in argv or '--selector' in argv:
            return None
        project = location
        if '--project-dir' in argv:
            i = argv.index('--project-dir')
            if i + 1 == len(argv): return None
            value = argv[i + 1]
            project = value if value.startswith('/') else posixpath.normpath(posixpath.join(location, value)) if location else None
        selectors = []
        for flag in ('--select', '-s', '--models', '-m'):
            if flag in argv:
                i = argv.index(flag) + 1
                while i < len(argv) and not argv[i].startswith('-'):
                    selectors.append(argv[i]); i += 1
        contains = not selectors or any(x in ('int_sales__orders_enriched', '+int_sales__orders_enriched',
                                              'int_sales__orders_enriched+', '+int_sales__orders_enriched+') for x in selectors)
        if project == '/app/dbt_models_duckdb' and contains:
            recognized = True
    return recognized


def warning_upstream_gate(events):
    final_calls = [r for r in events.get('profile_build_calls', []) if '/app/dbt_project' in r['profile_directories']]
    successes = []
    for row in events.get('pairs', []):
        if row.get('name') != 'run_shell_command': continue
        args = row.get('args', {}); response = shell_result(row.get('response', {}))
        if not response or response[0] != 0: continue
        if upstream_commands(args.get('command'), args.get('directory', args.get('cwd'))) is not True: continue
        # A successful shell that selected nothing is not a successful model.
        stdout = re.sub(r'\x1b\[[0-9;]*m', '', response[1])
        if re.search(r'OK created\b[^\n]*\bmodel\s+main\.int_sales__orders_enriched\b', stdout):
            successes.append(row)
    earliest = min((x['call_index'] for x in final_calls), default=None)
    ordered = [x for x in successes if earliest is not None and x['result_index'] < earliest]
    return {'reward': 1 if ordered else None, 'status': 'pass' if ordered else 'unknown',
        'reason': 'Original successful paired dbt enriched-model build must finish before the original final-project build; fresh reconstruction is separate.',
        'successful_upstream_calls': [x['call_id'] for x in successes],
        'ordered_upstream_calls': [x['call_id'] for x in ordered],
        'first_final_call_index': earliest, 'source_logs': events.get('source_logs', [])}


def same_build(argv):
    result = []; index = 0
    while index < len(argv):
        if argv[index] in ('--target-path', '--log-path'):
            index += 2
        else:
            result.append(argv[index]); index += 1
    return result


def profile_resolution_gap(verified, declared):
    """A bounded static profile reader cannot reject unrecognized valid Jinja."""
    files = verified['final_files']; env = verified['execution']['environment']
    try:
        config = yaml.safe_load(files['dbt_project.yml'].read_text())
        profiles = yaml.safe_load(files['profiles.yml'].read_text())
        name = config['profile']
        if isinstance(name, str) and '{{' in name:
            return 'Dynamic project profile name is outside the frozen static workflow parser.'
        selected = profiles[name]
        active = selected['outputs'][env_value(selected['target'], env)]
        env_value(active['path'], env)
        schema = env_value(active.get('schema', 'main'), env)
        wanted = declared.get('profile_schema')
        if wanted and isinstance(schema, str) and schema != wanted and schema.lower() == wanted.lower():
            return 'Case-only DuckDB schema representation is not a demonstrated contract violation; frozen profile equality requires review.'
    except ValueError:
        return 'Selected profile expression is outside the bounded static reader; successful reconstruction does not make it invalid.'
    except (KeyError, TypeError, yaml.YAMLError):
        # Definite missing/invalid local configuration is still evaluated by
        # the frozen workflow. Do not convert every profile failure to unknown.
        return None
    return None


def typed_projection(cap, item, fields):
    columns = [x.lower() for x in item['columns']]
    if len(columns) != len(set(columns)) or not set(fields) <= set(columns):
        raise ValueError('Repeated output columns incomplete/ambiguous')
    indexes = [columns.index(x) for x in fields]
    schema = {row[0].lower(): row[1] for row in item['sql_schema']}
    rows = [json.loads(line) for line in cap.runtime(item).read_text().splitlines()]
    if len(rows) != item['rows']:
        raise ValueError('Repeated output row count mismatch')
    bag = Counter(json.dumps([row[i] for i in indexes], sort_keys=True, separators=(',', ':')) for row in rows)
    return {'types': {f: schema[f] for f in fields}, 'bag': bag}


def idempotence(task, verified):
    if task not in REPEAT_REQUIRED:
        return ck('pass', 'This contract does not impose same-workspace rerun idempotence; no additional run obligation is introduced.', required=False)
    builds = [x for x in verified['commands'] if x['run']['command'][:2] in (['dbt', 'run'], ['dbt', 'build'])]
    final = builds[-1] if builds else None
    same = [x for x in builds if same_build(x['run']['command']) == same_build(final['run']['command'])] if final else []
    if len(same) < 2:
        return ck('gap', 'Required same-source idempotence lacks two actual final-project captures.', required=True)
    checks = {}; evidence = []
    try:
        before, after = same[-2:]
        if len(before['hooks']) != 1 or len(after['hooks']) != 1:
            raise ValueError('Expected one actual physical-output hook per repeated build')
        a, z = before['hooks'][0]['physical_outputs'], after['hooks'][0]['physical_outputs']
        for relation, fields in FAILURE_REQUIREMENTS[task].items():
            name = relation.split('.', 1)[1]
            # The common collector keys physical_outputs by the table name.
            left, right = typed_projection(verified['capture'], a[name], fields), typed_projection(verified['capture'], z[name], fields)
            checks[relation] = left == right
            evidence.extend([a[name], z[name]])
    except (KeyError, ValueError) as exc:
        return ck('gap', 'Actual repeat observation incomplete; not inferred from two independent reference runs.', error=str(exc), checks=checks, required=True)
    return ck('pass' if all(checks.values()) else 'fail',
        'Actual consecutive same-project builds compared on every declared column, exact typed multisets and SQL types.',
        checks=checks, required=True, evidence=evidence,
        scope='Property of the exact delivered source; never a claim that the model ran these audit commands.')


def run(request, root=ROOT):
    root = Path(root); context = verify(request['audit_dir'], root)
    key = request['task_key']; task = key.removeprefix('deb__')
    if task not in TASKS or context['binding']['task_key'] != key or request['stage'] != 'agent-submission':
        raise ValueError('Wrong qualified churn/RFM submission')
    d = context['fresh']; raw = raw_rows(d)
    observed, unresolved = actual_outputs(request['snapshot'], key, root)
    events = checked_json(request['original_events'], root)
    expected, facts = {'dbt-customer-churn-early-warning': warning,
        'dbt-customer-churn-cohorts': cohorts, 'dbt-rfm-customer-segmentation-2': rfm2}[task](raw, {n: x['rows'] for n, x in observed.items()})
    # Verify this adapter's absence policy exactly matches the frozen output rules.
    for relation, fields in FAILURE_REQUIREMENTS[task].items():
        rows = expected[relation.split('.', 1)[1]]
        if not rows or set(rows[0]) != set(fields):
            raise ValueError('Frozen expected fields differ from declared absence rules')
    result = compare_outputs(observed, {n: rows for n, rows in expected.items() if n in observed})
    if unresolved and result['reward'] == 1:
        result.update(reward=None, status='unknown')
    result['unresolved_outputs'] = unresolved
    gate = declared_action_gate(key, events, d['request']['project_root'])
    declared = policy(task)
    wf = workflow(d, declared)
    gap = profile_resolution_gap(d, declared)
    if gap:
        wf['checks']['profile'] = ck('gap', gap, frozen_static_result=wf['checks']['profile'])
    wf['checks']['backend'] = ck('pass' if gate['reward'] == 1 else 'fail' if gate['reward'] == 0 else 'gap',
        'Actual original paired model actions, separately from fresh reconstruction context.', details=gate)
    if task.endswith('warning'):
        upstream = warning_upstream_gate(events)
        wf['checks']['upstream']['reason'] = 'Qualified upstream source can actually build during exact-source reconstruction; original action is checked separately.'
        wf['checks']['original_upstream_order'] = ck('pass' if upstream['reward'] == 1 else 'gap', upstream['reason'], details=upstream)
        if gate['reward'] == 1 and upstream['reward'] != 1:
            gate = {'reward': None, 'status': 'unknown', 'reason': 'Original upstream-before-final action is unresolved.', 'backend': gate, 'upstream': upstream}
    wf['checks']['idempotence'] = idempotence(task, d)
    states = [x['status'] for x in wf['checks'].values()]
    wf['reward'] = 0 if 'fail' in states else None if any(x in states for x in ('gap', 'pending')) else 1
    wf['status'] = 'unknown' if wf['reward'] is None else 'scored'
    if wf['reward'] == 0:
        result.update(reward=0, status='scored')
    elif wf['reward'] is None and result['reward'] != 0:
        result.update(reward=None, status='unknown')
    result.update(original_action_gate=gate, actual_workflow=wf, independent_facts=facts,
        actual_snapshot=request['snapshot'], original_events=request['original_events'],
        source_binding=context['manifest']['source_binding'],
        adapter_code_refs=[io.evidence_ref(p, root) for p in [Path(__file__),
            ROOT/'scripts/phase4_customer4_review_v3.py', ROOT/'scripts/phase4_deb_recompute_v2.py', SHARED/'review_support_v3.py']],
        scope='Qualified business rules recomputed from fixed input and compared to original pre-native outputs. Fresh manifest is exact-source provenance only. Original prescribed actions require paired Qwen events.')
    return result

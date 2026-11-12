"""Independent F1 aggregate audit over trusted capture and clean dbt workflow."""
import argparse
from dataclasses import replace
from decimal import Decimal
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

HERE = Path(__file__).parent
TASK = 'ade__' + HERE.parent.name
AGE = TASK.endswith('f1009')
MODEL = 'analysis__drivers_current_age' if AGE else 'analysis__lap_times'
INT_TYPES = ('TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT', 'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT', 'UHUGEINT')


def module(name):
    spec = importlib.util.spec_from_file_location(TASK + '_' + name, HERE / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def integral(value, bounds):
    """Validate exact finite integral semantics before converting bounded keys.

    Integers never travel through float. This is a declared task-specific
    representation allowance only for fields without a mandated SQL type.
    """
    if type(value) not in (int, float, Decimal):
        raise ValueError('Not a numeric integer representation')
    if type(value) is float and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError('Nonfinite or fractional integer representation')
    if type(value) is Decimal and (not value.is_finite() or value != value.to_integral_value()):
        raise ValueError('Nonfinite or fractional integer representation')
    if value < bounds[0] or value > bounds[1]:
        raise ValueError('Integer value outside original-input semantic bounds')
    return int(value)


def numeric_type(kind):
    return kind in INT_TYPES + ('FLOAT', 'DOUBLE') or bool(re.fullmatch(r'DECIMAL\((?:[1-9]|[12][0-9]|3[0-8]),(?:[0-9]|[12][0-9]|3[0-8])\)', kind))


def clock_date(request, root, read_ref):
    context = json.loads(read_ref(request['clock_context'], root))
    if context.get('producer') not in ('task_image_selftest_clean_build', 'locked_qwen_clean_rebuild'):
        raise ValueError('Untrusted SQL clock context')
    if context.get('dbt_session_timezone_matches') is not True:
        raise ValueError('Read-only clock and actual dbt session timezone not established')
    before, after = [json.loads(read_ref(context[key], root)) for key in ('before', 'after')]
    for item in (before, after):
        if item.get('error') or item.get('input_unchanged') is not True:
            raise ValueError('SQL clock probe failed or mutated its database')
        if item['database'] != context['actual_database'] or item['sql'] != "SELECT CURRENT_DATE, CURRENT_TIMESTAMP, current_setting('TimeZone')":
            raise ValueError('Wrong SQL clock/database capture')
    if (before['current_date'], before['timezone']) != (after['current_date'], after['timezone']):
        raise ValueError('Native date/timezone changed during build; do not freeze or discard clock-sensitive values')
    # Context review binds its source/profile evidence, not a permitted SQL hash.
    for evidence in context['session_evidence']:
        read_ref(evidence, root)
    if not context['session_evidence']:
        raise ValueError('No actual dbt session configuration evidence')
    return before['current_date'], context


def run(request, root):
    sys.path[:0] = [str(root / 'scripts'), str(root / 'src')]
    from audit_tables import TablePolicy, load_observer_table, audit_table
    from dbt_bench.comparison import Column, TableContract
    workflow = module('workflow'); derive = module('derive_expected')
    if request['task_key'] != TASK:
        raise ValueError('Wrong task identity')
    inputs = json.loads((HERE / 'expected_inputs.json').read_text())
    if request.get('initial_database_sha256') != inputs['initial_database_sha256']:
        raise ValueError('Original current-image input binding not established')
    components = {}
    try:
        candidate = load_observer_table(request['snapshot'], task_key=TASK,
                    database='/app/f1.duckdb', relation='main.'+MODEL,
                    stage=request['stage'], root=root)
        original_types = dict(candidate.physical_sql_types)
        if len({n.casefold() for n in candidate.physical_columns}) == len(candidate.physical_columns):
            candidate = replace(candidate, columns=tuple(n.casefold() for n in candidate.columns),
                physical_columns=tuple(n.casefold() for n in candidate.physical_columns),
                sql_types={n.casefold(): t for n, t in candidate.sql_types.items()},
                physical_sql_types={n.casefold(): t for n, t in candidate.physical_sql_types.items()},
                rows=[{n.casefold(): v for n, v in row.items()} for row in candidate.rows])
        if AGE:
            day, context = clock_date(request, root, workflow.read_ref)
            derived = derive.derive(inputs['drivers'], day)
            expected = derived['rows']
            columns = ('current_age_in_years', 'drivers'); keys = ('current_age_in_years',)
            numeric = {'current_age_in_years': derived['age_range'], 'drivers': (1, derived['driver_rows'])}
            types = {name: (candidate.physical_sql_types.get(name),) if numeric_type(candidate.physical_sql_types.get(name, '')) else INT_TYPES for name in columns}
        else:
            expected = derive.rows(inputs['groups'])
            columns = ('circuit_name', 'race_year', 'avg_lap_time_in_ms'); keys = ('circuit_name', 'race_year')
            years = [g['race_year'] for g in inputs['groups']]
            numeric = {'race_year': (min(years), max(years))}
            year_type = candidate.physical_sql_types.get('race_year', '')
            types = {'circuit_name': ('VARCHAR',), 'race_year': (year_type,) if numeric_type(year_type) else INT_TYPES, 'avg_lap_time_in_ms': INT_TYPES}
        policy = TablePolicy(TableContract(tuple(Column(n, 'text' if n == 'circuit_name' else 'integer') for n in columns), keys=keys),
                             sql_types=types, not_null=columns, relation_types=('BASE TABLE', 'VIEW'))
        try:
            normalized = []
            for row in candidate.rows:
                row = dict(row)
                for name, bounds in numeric.items():
                    if name in row:
                        row[name] = integral(row[name], bounds)
                normalized.append(row)
            candidate = replace(candidate, rows=normalized)
        except ValueError as exc:
            components['table'] = {'status': 'scored', 'reward': 0.0,
                                   'reason': str(exc), 'checks': {'exact_bounded_integral_semantics': False}}
        if 'table' not in components:
            interface = audit_table(candidate, policy)
            table = audit_table(candidate, policy, expected)
            if not AGE and table['reward'] == 0.0 and interface['reward'] == 1.0:
                alternatives = [name for name in ('floor', 'ceil', 'half_even')
                                if audit_table(candidate, policy, derive.rows(inputs['groups'], name))['reward'] == 1.0]
                if alternatives:
                    table = dict(table, status='unknown', reward=None,
                        reason='Coherent alternate whole-millisecond convention remains unadjudicated: '+','.join(alternatives))
            table['original_physical_sql_types'] = original_types
            table['numeric_representation_policy'] = 'Only explicitly listed non-type-mandated fields: finite, exact integral, original-input bounds, then int; never int-to-float; INTEGER mean schema stays strict'
            components['table'] = table
    except Exception as exc:
        components['table'] = {'status': 'unknown', 'reward': None, 'reason': str(exc)}
    try:
        components['workflow'] = workflow.workflow_check(request['workflow'], request['snapshot'], TASK, root, MODEL)
    except Exception as exc:
        components['workflow'] = {'status': 'unknown', 'reward': None, 'reason': str(exc)}
    rewards = [v['reward'] for v in components.values()]
    reward = 0.0 if 0.0 in rewards else None if None in rewards else 1.0
    paths = [HERE / n for n in ('check.py', 'workflow.py', 'derive_expected.py', 'expected_inputs.json')]
    paths += [root / 'scripts/audit_tables.py', root / 'src/dbt_bench/comparison.py']
    return {'schema_version': 1, 'task_key': TASK, 'target': 'audit',
            'status': 'unknown' if reward is None else 'scored', 'reward': reward,
            'components': components, 'code_refs': [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
            'evidence': request, 'scope': 'Original inputs and recorded actual SQL date where applicable; independent aggregate values, full table interface/grain and trusted dbt workflow; native untouched'}


def main():
    p = argparse.ArgumentParser(); p.add_argument('--request', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--repo-root', type=Path, required=True)
    a = p.parse_args()
    try: result = run(json.loads(a.request.read_text()), a.repo_root)
    except Exception as exc: result = {'target':'audit','status':'unknown','reward':None,'reason':str(exc)}
    a.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()

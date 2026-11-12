"""Executed coupon comparator fixtures; no benchmark or model invocation."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import argparse
import json
import sys
HERE = Path(__file__).parent
ROOT = HERE.resolve().parents[3]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from dbt_bench.comparison import Column, TableContract, compare_tables
from audit_tables import load_observer_table, decode
from run_audit_v2 import run, dec
from audit_v2 import evaluate, policies, COLS
from workflow_v2 import evaluate as workflow_evaluate, resolve
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/deb'))
KEY = 'deb__dbt-coupon-effectiveness'

def checked(ref):
    if not io.evidence_ok(ref, ROOT):
        raise ValueError('Input evidence changed')
    return ROOT / ref['path']

def main(output):
    fresh = io.read_json(D / 'coupon-fresh-v2/review.json')['records'][0]
    spec = io.read_json(checked(fresh['stages'][0]['spec']))
    raw = dec(io.read_json(checked(spec['original_input']))['rows'])
    expected = {'policies': policies(raw)}
    workflow = io.read_json(checked(spec['trusted_workflow']))
    lawful_workflow = workflow_evaluate(workflow)
    outputs = {}
    for name in COLS:
        t = load_observer_table(spec['candidate_snapshot'], task_key=KEY, database='/app/database/retail.duckdb', relation='main.' + name, stage=spec['candidate_stage'], root=ROOT)
        rows = [{k.lower(): v for k, v in row.items()} for row in t.rows]
        if name == 'int_coupon_redemptions':
            for row in rows:
                if 'usage_limit' not in row and 'coupon_usage_limit' in row:
                    row['usage_limit'] = row['coupon_usage_limit']
        t = replace(t, columns=tuple((c.lower() for c in t.columns)), physical_columns=tuple((c.lower() for c in t.physical_columns)), sql_types={k.lower(): v for k, v in t.sql_types.items()}, physical_sql_types={k.lower(): v for k, v in t.physical_sql_types.items()}, rows=rows)
        co = spec['compiled_outputs'][name]
        compiled = [dict(zip([c.lower() for c in co['columns']], map(decode, json.loads(line)))) for line in checked(co['data']).read_text().splitlines()]
        outputs[name] = {'table': t, 'rows': rows, 'compiled_rows': compiled}
    results = []

    def test(name, value):
        results.append({'name': name, 'pass': bool(value)})
    test('actual_complete_reference', run(spec, ROOT)['reward'] == 1)
    test('actual_untouched_rejected', run(io.read_json(checked(fresh['initial_nop']['spec'])), ROOT)['reward'] == 0)
    for kind in ('task', 'stage', 'hash'):
        changed = deepcopy(spec)
        if kind == 'task':
            changed['task_key'] = 'different-task'
        elif kind == 'stage':
            changed['candidate_stage'] = 'verifier-after'
        else:
            changed['candidate_snapshot']['sha256'] = '0' * 64
        test('reject_changed_' + kind, run(changed, ROOT)['reward'] is None)
    test('whole_value_interface_workflow', evaluate(outputs, expected, lawful_workflow)['reward'] == 1)
    for kind in ('missing-row', 'duplicate-row', 'wrong-association', 'wrong-key', 'numeric-cent', 'fractional-count', 'null-as-zero', 'missing-column', 'wrong-sql-type', 'wrong-materialization', 'compiled-order', 'nan'):
        current = deepcopy(outputs)
        e = current['coupon_effectiveness']
        rows = e['rows']
        if kind == 'missing-row':
            rows.pop()
        elif kind == 'duplicate-row':
            rows.append(deepcopy(rows[0]))
        elif kind == 'wrong-association':
            assert rows[0]['promotion_name'] != rows[1]['promotion_name']
            rows[0]['promotion_name'], rows[1]['promotion_name'] = (rows[1]['promotion_name'], rows[0]['promotion_name'])
        elif kind == 'wrong-key':
            rows[0]['promotion_id'] = 'other-promotion'
        elif kind == 'numeric-cent':
            rows[0]['total_discount_given'] += Decimal('.01')
        elif kind == 'fractional-count':
            rows[0]['total_redemptions'] = Decimal('1.5')
        elif kind == 'null-as-zero':
            row = next((row for row in rows if row['total_usage_limit'] is None))
            row['total_usage_limit'] = 0
        elif kind == 'missing-column':
            e['missing_required_columns'] = ['promotion_name']
            for row in rows:
                row.pop('promotion_name')
        elif kind == 'wrong-sql-type':
            types = dict(e['table'].physical_sql_types)
            types['total_redemptions'] = 'VARCHAR'
            e['table'] = replace(e['table'], physical_sql_types=types)
        elif kind == 'wrong-materialization':
            e['table'] = replace(e['table'], relation_type='VIEW')
        elif kind == 'compiled-order':
            e['compiled_rows'].reverse()
        elif kind == 'nan':
            rows[0]['total_order_value'] = Decimal('NaN')
        test(kind, evaluate(current, expected, lawful_workflow)['reward'] == 0)
    for kind in ('physical-row-order', 'physical-column-order', 'extra-columns'):
        current = deepcopy(outputs)
        for item in current.values():
            if kind == 'physical-row-order':
                item['rows'].reverse()
            elif kind == 'physical-column-order':
                item['table'] = replace(item['table'], columns=tuple(reversed(item['table'].columns)), physical_columns=tuple(reversed(item['table'].physical_columns)))
            else:
                for row in item['rows']:
                    row['extra_legal_value'] = 'allowed'
        test(kind, evaluate(current, expected, lawful_workflow)['reward'] == 1)
    current = deepcopy(outputs)
    tie = None
    name, column = ('coupon_effectiveness', 'early_avg_discount')
    a, b = [p['relations'][name] for p in expected['policies']]
    pid_index, value_index = (a['columns'].index('promotion_id'), a['columns'].index(column))
    bm = {r[pid_index]: r[value_index] for r in b['rows']}
    for row in a['rows']:
        if row[value_index] != bm[row[pid_index]]:
            tie = (row[pid_index], row[value_index], bm[row[pid_index]])
            break
    assert tie is not None
    observed = next((r for r in current[name]['rows'] if r['promotion_id'] == tie[0]))
    observed[column] = tie[1] if observed[column] == tie[2] else tie[2]
    test('exact_nearest_half_cent_alternative', evaluate(current, expected, lawful_workflow)['reward'] == 1)
    integer = TableContract((Column('id', 'integer'), Column('label', 'text')), keys=('id',))
    test('integer_id_exact', not compare_tables([{'id': 10000, 'label': 'A'}], [{'id': 10001, 'label': 'A'}], integer).equal)
    test('integer_above_float_exactness', not compare_tables([{'id': 9007199254740992, 'label': 'A'}], [{'id': 9007199254740993, 'label': 'A'}], integer).equal)
    tolerant = TableContract((Column('v', 'numeric', '0.01', '0'), Column('id', 'text')), keys=('id',))
    test('keyed_tolerance_not_sort_zip', compare_tables([{'v': 0.004, 'id': 'A'}, {'v': 0.006, 'id': 'B'}], [{'v': 0.006, 'id': 'A'}, {'v': 0.004, 'id': 'B'}], tolerant).equal)
    test('explicit_profile_env', resolve("{{ env_var('DUCKDB_PATH', 'fallback') }}", {'DUCKDB_PATH': '/app/database/retail.duckdb'}) == '/app/database/retail.duckdb')
    try:
        resolve("{{ env_var('UNRECORDED') }}", {})
        test('unobserved_profile_env_not_guessed', False)
    except ValueError:
        test('unobserved_profile_env_not_guessed', True)
    changed = deepcopy(workflow)
    changed['files'].pop('models/marts/analytics/coupon_effectiveness.sql')
    test('actual_source_required', workflow_evaluate(changed)['details']['model.coupon_effectiveness']['status'] == 'fail')
    result = {'task_key': KEY, 'passed': sum((r['pass'] for r in results)), 'failed': sum((not r['pass'] for r in results)), 'tests': results, 'source': io.evidence_ref(Path(__file__), ROOT), 'actual_reference_spec': fresh['stages'][0]['spec'], 'nearest_tie_fixture': {'promotion_id': tie[0], 'column': column, 'legal_values': list(map(str, tie[1:]))}, 'model_requests': 0, 'scope': 'Constructed comparator/workflow regressions around an actual input-built submission; no new benchmark trial or general unseen-data proof.'}
    assert not output.exists()
    io.write_json(output, result)
    print({'passed': result['passed'], 'failed': result['failed']})
    assert result['failed'] == 0
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)

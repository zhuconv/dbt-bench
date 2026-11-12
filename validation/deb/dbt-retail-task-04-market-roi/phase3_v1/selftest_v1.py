"""Independent comparator and portable-date arithmetic regression fixtures."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
from datetime import date
from decimal import Decimal
import argparse
import json
import sys
HERE = Path(__file__).parent
ROOT = HERE.resolve().parents[3]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from derive_expected import COLUMNS, INTS, MONEY, RATIOS, compare_output
from portable_dateadd import portable_dateadd

def run(output):
    result = []

    def test(name, value):
        result.append({'name': name, 'pass': bool(value)})
    row = dict.fromkeys(COLUMNS)
    row.update({k: 1 for k in INTS})
    row.update({k: Decimal('1.0') for k in MONEY | RATIOS})
    row.update(month_start=date(2024, 1, 1), channel_key=10000, channel_name='A', channel_type='email')
    other = {**row, 'channel_key': 10001, 'channel_name': 'B', 'ctr': Decimal('0.004')}
    left = [row, other]

    def cmp(label, right, expected, gold=left):
        test(label, compare_output(gold, right)['equal'] is expected)
    cmp('unrestricted_row_order', list(reversed(left)), True)
    cmp('same_values_wrong_key_association', [{**row, 'channel_name': 'B'}, {**other, 'channel_name': 'A'}], False)
    cmp('identifier_10000_10001', [{**row, 'channel_key': 10001}], False, [row])
    large = {**row, 'channel_key': 9007199254740992}
    cmp('large_integer_exact', [{**large, 'channel_key': 9007199254740993}], False, [large])
    cmp('duplicate_nullable_business_key', left + [row], False)
    cmp('missing_row', [row], False)
    cmp('extra_key', left + [{**row, 'channel_key': 10002}], False)
    cmp('missing_column', [{k: v for k, v in row.items() if k != 'ctr'}, other], False)
    cmp('legal_extra_column', [{**r, 'extra': 0} for r in left], True)
    cmp('nullable_segment_not_sentinel', [{**row, 'segment_name': 'NULL'}, other], False)
    cmp('keyed_small_numeric_tolerance', [{**other, 'ctr': Decimal('0.0040001')}, {**row, 'ctr': Decimal('1.00001')}], True)
    cmp('numeric_outside_column_tolerance', [{**row, 'ctr': Decimal('1.1')}, other], False)
    cmp('boolean_not_integer', [{**row, 'clicks': True}, other], False)
    cmp('fractional_count', [{**row, 'clicks': Decimal('1.5')}, other], False)
    cmp('null_not_zero', [{**row, 'ctr': None}, other], False)
    cmp('nan_rejected', [{**row, 'ctr': Decimal('NaN')}, other], False)
    cmp('infinity_rejected', [{**row, 'ctr': Decimal('Infinity')}, other], False)
    fresh = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/deb/root-three-v1/roi-fresh-v1/review.json'))
    request = io.read_json(Path(io.read_json(fresh)['records'][0]['stages'][1]['request']['path']))
    workflow = io.read_json(Path(request['workflow']['path']))
    manifest = io.read_json(Path(workflow['manifest']['path']))
    source = "select {{ dbt.dateadd('day', 90, 'p.first_purchase_date') }}"
    test('actual_frozen_portable_dateadd', portable_dateadd(source, manifest))
    test('comment_does_not_fake_macro', not portable_dateadd("select 1 {# dbt.dateadd('day',90,'x') #}", manifest))
    test('literal_business_interval_not_waived', not portable_dateadd(source + ", x + interval '1 day'", manifest))
    changed = deepcopy(manifest)
    macro = next((v for v in changed['macros'].values() if v['name'] == 'duckdb__dateadd'))
    macro['macro_sql'] += ' -- changed'
    for name, candidate_source, candidate_manifest in [('drifted_adapter', source, changed), ('dynamic_expression_requires_review', "select {{ dbt.dateadd('day', 90, 'x + 1') }}", manifest), ('other_datepart_requires_review', "select {{ dbt.dateadd('month', 90, 'x') }}", manifest)]:
        try:
            portable_dateadd(candidate_source, candidate_manifest)
            test(name, False)
        except ValueError:
            test(name, True)
    value = {'task_key': 'deb__dbt-retail-task-04-market-roi', 'passed': sum((r['pass'] for r in result)), 'failed': sum((not r['pass'] for r in result)), 'tests': result, 'code': io.evidence_ref(Path(__file__), ROOT), 'scope': 'Synthetic comparator/source-form fixtures; not new benchmark trials.'}
    assert not output.exists()
    io.write_json(output, value)
    print({'passed': value['passed'], 'failed': value['failed']})
    assert value['failed'] == 0
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    run(p.parse_args().output)

"""Case payload copied into reviewed validation-only tasks, never a Qwen tool."""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

import duckdb

OUT = Path('/logs/agent')
MODEL = Path('/app/dbt_models_duckdb/models/marts/marketing/rpt_channel_roi_payback_monthly.sql')
KEY = ('month_start', 'channel_key', 'segment_name', 'tier_name')
INPUTS = {
    'marketing': ('stg_fact_marketing_spend', 'date_key, channel_key, impressions, clicks, conversions, spend_amount, revenue_attributed'),
    'dates': ('stg_analytics__dim_date', 'date_key, full_date'),
    'sales': ('stg_analytics__fact_sales', 'customer_key, channel_key, date_key, order_id, total_amount, profit_amount'),
    'customers': ('stg_analytics__dim_customer', 'customer_key, segment_name, tier_name'),
    'channels': ('stg_analytics__dim_channel', 'channel_key, channel_name, channel_type')}


def canonical(row):
    return json.dumps(row, sort_keys=True, separators=(',', ':'), default=str)


def rows(connection, table, columns='*'):
    q = connection.execute('SELECT ' + columns + ' FROM main.' + table)
    names = [r[0].lower() for r in q.description]
    return json.loads(json.dumps([dict(zip(names, r)) for r in q.fetchall()], default=str))


def input_hash(data):
    return hashlib.sha256('\n'.join(sorted(canonical(r) for r in data)).encode()).hexdigest()


def ref(path):
    path = Path(path)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


case = json.loads((Path(__file__).parent / 'case.json').read_text())
mutation = case['mutation']
if sys.argv[1] == 'submit':
    c = duckdb.connect('/app/database/retail.duckdb', read_only=True)
    for name, (table, columns) in INPUTS.items():
        data = rows(c, table, columns)
        assert input_hash(data) == case['reviewed_input_hashes'][name], 'Inputs differ from independently reviewed data'
        (OUT / (name + '.json')).write_text(json.dumps(data, sort_keys=True))
    before = rows(c, 'rpt_channel_roi_payback_monthly')
    c.close()
    (OUT / 'before.json').write_text(json.dumps(before, sort_keys=True))
    text = MODEL.read_text()
    assert hashlib.sha256(text.encode()).hexdigest() == case['reviewed_reference_sql_sha256']
    (OUT / 'before.sql').write_text(text)
    if mutation == 'fractional-impressions':
        old = '    m.impressions,'
        assert text.count(old) == 1
        text = text.replace(old, '    m.impressions + 0.25 AS impressions,')
    else:
        end = text.index('}}') + 2
        config, body = text[:end], text[end:].rstrip().rstrip(';')
        if mutation == 'duplicate-key':
            query = 'SELECT * FROM original_answer UNION ALL (SELECT * FROM original_answer ORDER BY month_start, channel_key, segment_name, tier_name LIMIT 1)'
        elif mutation == 'reorder-extra-column':
            query = 'SELECT *, 0 AS harmless_extra FROM original_answer ORDER BY month_start DESC, channel_key DESC, segment_name DESC, tier_name DESC'
        else:
            raise ValueError('Unknown reviewed case')
        text = config + '\nWITH original_answer AS (\n' + body + '\n)\n' + query + '\n'
    MODEL.write_text(text)
elif sys.argv[1] == 'proof':
    c = duckdb.connect('/app/database/retail.duckdb', read_only=True)
    after = rows(c, 'rpt_channel_roi_payback_monthly')
    for name, (table, columns) in INPUTS.items():
        assert input_hash(rows(c, table, columns)) == case['reviewed_input_hashes'][name]
    c.close()
    before = json.loads((OUT / 'before.json').read_text())
    before_count = Counter(canonical(r) for r in before)
    facts = {'before_rows': len(before), 'after_rows': len(after)}
    if mutation == 'duplicate-key':
        after_count = Counter(canonical(r) for r in after)
        assert not (before_count - after_count)
        difference = after_count - before_count
        assert sum(difference.values()) == 1 and next(iter(difference)) in before_count
        facts['exact_extra_duplicate_rows'] = 1
        validity = 'business_error'
    elif mutation == 'fractional-impressions':
        expected = {tuple(r[k] for k in KEY): r for r in before}
        assert len(before) == len(after) == len(expected)
        for row in after:
            original = expected[tuple(row[k] for k in KEY)]
            assert Decimal(str(row['impressions'])) == Decimal(str(original['impressions'])) + Decimal('0.25')
            assert Decimal(str(row['impressions'])) % 1 != 0
            assert {k: v for k, v in row.items() if k != 'impressions'} == {k: v for k, v in original.items() if k != 'impressions'}
        facts['nonintegral_impression_rows'] = len(after)
        validity = 'business_error'
    elif mutation == 'reorder-extra-column':
        assert all(r['harmless_extra'] == 0 for r in after)
        projected = [{k: v for k, v in row.items() if k != 'harmless_extra'} for row in after]
        assert before_count == Counter(canonical(r) for r in projected)
        facts.update(required_values_and_multiplicity_unchanged=True, allowed_additional_column='harmless_extra')
        validity = 'legal_equivalent'
    else:
        raise ValueError('Unknown reviewed case')
    (OUT / 'after.json').write_text(json.dumps(after, sort_keys=True))
    (OUT / 'submission.sql').write_text(MODEL.read_text())
    proof = {'validity': validity, 'derivation': case['independent_derivation'],
             'requirement_ids': case['requirement_ids'], 'facts': facts,
             'baseline_review': case['baseline_review'],
             'input_artifacts': [ref(OUT / (name + '.json')) for name in INPUTS] + [ref(OUT / 'before.json'), ref(OUT / 'before.sql')],
             'submission_artifacts': [ref(MODEL), ref(OUT / 'submission.sql'), ref(OUT / 'after.json')],
             'tested_checker_invoked_to_define_validity': False}
    (OUT / 'case-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps(facts, indent=2))
else:
    raise ValueError('Expected submit or proof')

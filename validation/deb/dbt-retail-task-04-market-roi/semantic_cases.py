"""Case payload copied into reviewed validation-only tasks, never a Qwen tool."""
from collections import Counter
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import shutil

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
    replacements = {
        'include-day-90': ("interval '90 day'", "interval '91 day'"),
        'exclude-day-89': ("interval '90 day'", "interval '89 day'"),
        'count-order-rows': ('count(distinct sf.order_id) as order_count', 'count(*) as order_count'),
        'null-share-to-zero': ('    smix.new_customer_share,', '    coalesce(smix.new_customer_share, 0) AS new_customer_share,')}
    if mutation in replacements:
        old, new = replacements[mutation]
        assert text.count(old) == 1
        text = text.replace(old, new)
    elif mutation == 'remove-tests-yaml':
        MODEL.with_suffix('.yml').unlink()
    elif mutation == 'fractional-impressions':
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
        elif mutation == 'drop-no-new-customer':
            query = 'SELECT * FROM original_answer WHERE new_customers IS NOT NULL'
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
    baseline = {tuple(r[k] for k in KEY): r for r in before}
    actual = {tuple(r[k] for k in KEY): r for r in after}
    if mutation in {'include-day-90', 'exclude-day-89', 'count-order-rows', 'null-share-to-zero'}:
        assert len(before) == len(after) == len(baseline) == len(actual)
        assert baseline.keys() == actual.keys()
    if mutation in {'include-day-90', 'exclude-day-89'}:
        # Derive only the added/removed boundary contribution from the raw
        # inputs. This never invokes the proposed audit or native comparator.
        sales = json.loads((OUT / 'sales.json').read_text())
        dates = defaultdict(list)
        customers = defaultdict(list)
        for r in json.loads((OUT / 'dates.json').read_text()):
            dates[r['date_key']].append(datetime.fromisoformat(r['full_date']).date())
        for r in json.loads((OUT / 'customers.json').read_text()):
            customers[r['customer_key']].append(r)
        by_customer = defaultdict(list)
        for r in sales:
            if r['customer_key'] is not None:
                by_customer[r['customer_key']].append(r)
        delta = defaultdict(lambda: [Decimal(0), Decimal(0)])
        boundary = 90 if mutation == 'include-day-90' else 89
        sign = 1 if boundary == 90 else -1
        for cid, purchases in by_customer.items():
            first = min(r['date_key'] for r in purchases if r['date_key'] is not None)
            channel = min(r['channel_key'] for r in purchases if r['date_key'] == first and r['channel_key'] is not None)
            for first_day in set(dates[first]):
                for customer in customers[cid]:
                    key = (str(first_day.replace(day=1)), channel,
                           customer['segment_name'] if customer['segment_name'] is not None else 'Unknown',
                           customer['tier_name'] if customer['tier_name'] is not None else 'Unknown')
                    for purchase in purchases:
                        for purchase_day in dates[purchase['date_key']]:
                            if (purchase_day-first_day).days == boundary:
                                for idx, field in enumerate(('total_amount', 'profit_amount')):
                                    if purchase[field] is not None:
                                        delta[key][idx] += sign * Decimal(str(purchase[field]))
        affected = []
        allowed = {'ltv_90d_revenue_per_customer', 'ltv_90d_profit_per_customer', 'payback_90d_profit_ratio'}
        for key, original in baseline.items():
            row = actual[key]
            assert {k:v for k,v in row.items() if k not in allowed} == {k:v for k,v in original.items() if k not in allowed}
            contribution = delta.get(key, (Decimal(0), Decimal(0)))
            if contribution == (Decimal(0), Decimal(0)) or contribution == [Decimal(0), Decimal(0)]:
                assert all(row[k] == original[k] for k in allowed)
                continue
            assert original['new_customers'] > 0
            for index, field in enumerate(('ltv_90d_revenue_per_customer', 'ltv_90d_profit_per_customer')):
                observed = Decimal(str(row[field]))-Decimal(str(original[field]))
                derived = contribution[index]/Decimal(original['new_customers'])
                assert abs(observed-derived) <= Decimal('0.00000001') * max(abs(derived), Decimal(1))
            affected.append({'key': key, 'boundary_day': boundary, 'revenue_delta': str(contribution[0]), 'profit_delta': str(contribution[1])})
        assert affected, 'No distinguishing boundary output on original data'
        facts['independently_derived_affected_groups'] = affected
        validity = 'business_error'
    elif mutation == 'count-order-rows':
        dates = defaultdict(list)
        for r in json.loads((OUT / 'dates.json').read_text()):
            dates[r['date_key']].append(str(datetime.fromisoformat(r['full_date']).date().replace(day=1)))
        counts, distinct = Counter(), defaultdict(set)
        for r in json.loads((OUT / 'sales.json').read_text()):
            for month in dates[r['date_key']]:
                key = (month, r['channel_key'])
                counts[key] += 1
                if r['order_id'] is not None:
                    distinct[key].add(r['order_id'])
        changed = 0
        for key, original in baseline.items():
            row = actual[key]
            allowed = {'order_count', 'order_conversion_rate'}
            assert {k:v for k,v in row.items() if k not in allowed} == {k:v for k,v in original.items() if k not in allowed}
            if key[:2] in counts:
                assert original['order_count'] == len(distinct[key[:2]])
                assert row['order_count'] == counts[key[:2]]
            changed += row['order_count'] != original['order_count']
        assert changed, 'COUNT(*) equivalent on these inputs'
        facts['wrong_order_count_rows'] = changed
        validity = 'business_error'
    elif mutation == 'null-share-to-zero':
        changed = 0
        for key, original in baseline.items():
            row = actual[key]
            assert {k:v for k,v in row.items() if k != 'new_customer_share'} == {k:v for k,v in original.items() if k != 'new_customer_share'}
            if original['new_customer_share'] is None:
                assert original['new_customers'] is None and row['new_customer_share'] == 0
                changed += 1
            else:
                assert row['new_customer_share'] == original['new_customer_share']
        assert changed
        facts['required_null_replaced_by_zero_rows'] = changed
        validity = 'business_error'
    elif mutation == 'drop-no-new-customer':
        retained = [r for r in before if r['new_customers'] is not None]
        assert Counter(canonical(r) for r in retained) == Counter(canonical(r) for r in after)
        assert 0 < len(after) < len(before)
        facts['missing_required_base_rows'] = len(before)-len(after)
        validity = 'business_error'
    elif mutation == 'remove-tests-yaml':
        assert not MODEL.with_suffix('.yml').exists()
        assert before_count == Counter(canonical(r) for r in after)
        facts['missing_required_file'] = str(MODEL.with_suffix('.yml'))
        (OUT / 'missing-file.json').write_text(json.dumps(facts))
        validity = 'business_error'
    elif mutation == 'duplicate-key':
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
    for source, name in ((MODEL.with_suffix('.yml'), 'submission.yml'), (MODEL.parents[3] / 'target/manifest.json', 'dbt-manifest.json')):
        if source.exists():
            shutil.copy2(source, OUT / name)
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

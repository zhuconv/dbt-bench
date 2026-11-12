"""Independent Decimal/stdlib interpretation of the written attribution contract.

Only DuckDB parquet reads are used; no native grader or reference SQL is imported.
Run on the immutable input snapshots exported before the negative case.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
import json
from pathlib import Path
import sys

import duckdb


def rows(path):
    with duckdb.connect() as conn:
        result = conn.execute("SELECT * FROM read_parquet(?)", [str(path)])
        columns = [col[0].lower() for col in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]


def channel(session):
    medium = (session['utm_medium'] or '').lower()
    referrer = (session['referrer'] or '').lower()
    if medium in {'cpc', 'ppc'}:
        return 'Paid Search'
    if medium == 'paid_social' or ('facebook' in referrer and session['utm_source'] not in (None, '')):
        return 'Paid Social'
    if any(s in referrer for s in ('google', 'bing', 'yahoo')):
        return 'Organic Search'
    if any(s in referrer for s in ('facebook', 'twitter', 'linkedin', 'instagram')):
        return 'Organic Social'
    if medium == 'email' or 'mail' in referrer:
        return 'Email'
    return 'Direct' if not referrer else 'Referral'


def calculate(sessions, sales):
    revenue = defaultdict(Decimal)
    quality = defaultdict(list)
    coverage = Counter()
    for sale in sales:
        if sale['order_id'] is not None and sale['total_amount'] is not None:
            revenue[sale['order_id']] += Decimal(str(sale['total_amount']))
    for session in sessions:
        if session['duration_seconds'] is None or session['page_views'] is None:
            coverage['null_quality_metrics'] += 1
            continue
        if session['duration_seconds'] < 5 or session['page_views'] < 2:
            coverage['filtered_low_quality'] += 1
            continue
        if (session['device_type'] or '').lower() == 'bot':
            coverage['filtered_bot'] += 1
            continue
        if session['customer_id'] is not None:
            quality[session['customer_id']].append(session)
    totals = defaultdict(lambda: [Decimal(0), Decimal(0)])
    paths = []
    for conversion in sessions:
        if conversion['is_converted'] is not True or conversion['order_id'] not in revenue:
            continue
        coverage['matching_conversions'] += 1
        stamp = conversion['session_start']
        touches = [s for s in quality.get(conversion['customer_id'], [])
                   if stamp - timedelta(days=14) <= s['session_start'] < stamp]
        if not touches:
            coverage['zero_touch'] += 1
            continue
        # Ties are explicitly reported; the instruction has no secondary tie key.
        touches.sort(key=lambda s: s['session_start'])
        n = len(touches)
        coverage['single_touch' if n == 1 else 'two_touch' if n == 2 else 'multi_touch'] += 1
        coverage['tie_paths'] += int(len({s['session_start'] for s in touches}) < n)
        coverage['exact_14_day_boundary'] += sum(s['session_start'] == stamp - timedelta(days=14) for s in touches)
        channels = [channel(s) for s in touches]
        coverage['bonus_paths'] += int(len(set(channels)) >= 3)
        weights = []
        with localcontext() as context:
            context.prec = 40
            for index, session in enumerate(touches):
                dt = stamp - session['session_start']
                seconds = Decimal(dt.days * 86400 + dt.seconds) + Decimal(dt.microseconds) / Decimal(1000000)
                position = Decimal(1) if n == 1 else Decimal('1.5') if index == 0 else Decimal('1.3') if index == n - 1 else Decimal(1)
                weights.append(position * (-seconds / Decimal(7 * 86400)).exp())
            denominator = sum(weights)
            adjusted = revenue[conversion['order_id']] * (Decimal('1.1') if len(set(channels)) >= 3 else Decimal(1))
            for label, weight in zip(channels, weights):
                credit = weight / denominator
                totals[label][0] += credit
                totals[label][1] += credit * adjusted
        paths.append({'order_id': conversion['order_id'], 'touches': n, 'channels': channels})
    rounded = [[label, *[str(v.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)) for v in values]]
               for label, values in sorted(totals.items())]
    return {'expected': rounded, 'coverage': dict(coverage), 'attributed_paths': paths,
            'scope': 'Independent interpretation on original input snapshot; absent input branches remain unexercised',
            'rounding': 'Decimal half-up at final two decimals; half-cent ambiguity remains a contract review item'}


if __name__ == '__main__':
    directory = Path(sys.argv[1])
    print(json.dumps(calculate(rows(directory / 'input-sessions.parquet'), rows(directory / 'input-sales.parquet')), indent=2))

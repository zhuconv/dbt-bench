"""Reuse reviewed independent Decimal reconstruction core. Unknown inputs remain explicit; no native grader or solution imports."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from decimal import Decimal,localcontext,ROUND_HALF_UP
REQUIRED=("channel","total_conversions","total_revenue")

def decimal(value):
    if type(value) not in {str, int, float, Decimal}:
        raise ValueError('Non-numeric value')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Non-finite numeric value')
    return result

def classify(row):
    medium = (row['utm_medium'] or '').lower()
    referral = (row['referrer'] or '').lower()
    predicates = [
        ('Paid Search', medium in {'cpc', 'ppc'}),
        ('Paid Social', medium == 'paid_social' or 'facebook' in referral and row['utm_source'] not in {None, ''}),
        ('Organic Search', any(s in referral for s in ['google', 'bing', 'yahoo'])),
        ('Organic Social', any(s in referral for s in ['facebook', 'twitter', 'linkedin', 'instagram'])),
        ('Email', medium == 'email' or 'mail' in referral),
        ('Direct', referral == ''),
        ('Referral', True),
    ]
    return next(label for label, matches in predicates if matches)

def recompute(inputs):
    sessions = inputs['sessions']
    stamps, candidates = {}, defaultdict(list)
    revenue = defaultdict(Decimal)
    uncertain, counts = [], Counter()
    for s in sessions:
        key = s['session_id']
        if type(key) is not str or key in stamps or type(s['is_converted']) is not bool:
            raise ValueError('Unexpected session key/boolean representation')
        stamps[key] = datetime.fromisoformat(s['session_start'])
        for col in ['customer_id', 'order_id']:
            if s[col] is not None and type(s[col]) is not str:
                raise ValueError('Input identifiers must remain exact strings')
        if s['duration_seconds'] is None or s['page_views'] is None:
            uncertain.append({'kind': 'null_quality_metric', 'session_id': key})
            continue
        if s['duration_seconds'] < 5 or s['page_views'] < 2 or (s['device_type'] or '').lower() == 'bot':
            continue
        if s['device_type'] is None:
            uncertain.append({'kind': 'null_device', 'session_id': key})
        if s['customer_id'] is not None:
            candidates[s['customer_id']].append(s)
    with localcontext() as context:
        context.prec = 80
        for sale in inputs['sales']:
            if sale['order_id'] is None:
                continue
            if type(sale['order_id']) is not str:
                raise ValueError('Sales order ID type differs')
            if sale['total_amount'] is None:
                uncertain.append({'kind': 'null_sale_amount', 'order_id': sale['order_id']})
            else:
                revenue[sale['order_id']] += decimal(sale['total_amount'])
        conversions = [s for s in sessions if s['is_converted'] and s['order_id'] in revenue]
        for order, n in Counter(s['order_id'] for s in conversions).items():
            if n > 1:
                uncertain.append({'kind': 'repeated_converting_order', 'order_id': order})
        sums = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for conversion in conversions:
            end = stamps[conversion['session_id']]
            eligible = sorted((s for s in candidates.get(conversion['customer_id'], [])
                               if end - timedelta(days=14) <= stamps[s['session_id']] < end),
                              key=lambda s: stamps[s['session_id']])
            counts['conversions'] += 1
            counts['touches_' + str(len(eligible))] += 1
            if not eligible:
                continue
            if len({stamps[s['session_id']] for s in eligible}) != len(eligible):
                uncertain.append({'kind': 'tied_touch', 'conversion_session': conversion['session_id']})
            anchor = stamps[eligible[-1]['session_id']]
            by_channel = defaultdict(Decimal)
            for i, s in enumerate(eligible):
                age = anchor - stamps[s['session_id']]
                micros = (age.days * 86400 + age.seconds) * 1000000 + age.microseconds
                coefficient = Decimal(1)
                if len(eligible) > 1:
                    coefficient = Decimal('1.5') if i == 0 else Decimal('1.3') if i == len(eligible) - 1 else Decimal(1)
                by_channel[classify(s)] += coefficient * (-Decimal(micros) / Decimal(604800000000)).exp()
            denominator = sum(by_channel.values())
            amount = revenue[conversion['order_id']] * (Decimal('1.1') if len(by_channel) >= 3 else Decimal(1))
            counts['bonus_paths'] += int(len(by_channel) >= 3)
            for channel, weight in by_channel.items():
                sums[channel][0] += weight / denominator
                sums[channel][1] += amount * weight / denominator
        expected = []
        for channel, values in sorted(sums.items()):
            for column, value in zip(REQUIRED[1:], values):
                if abs(abs(value) % Decimal('.01') - Decimal('.005')) < Decimal('1e-60'):
                    uncertain.append({'kind': 'half_cent_rounding_unspecified', 'channel': channel, 'column': column})
            expected.append(dict(channel=channel, **{c: str(v.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
                                                    for c, v in zip(REQUIRED[1:], values)}))
    return {'expected': expected, 'unknown': uncertain, 'coverage': dict(counts)}

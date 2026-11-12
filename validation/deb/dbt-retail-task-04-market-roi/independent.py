"""Independent ROI recomputation using Python groups and Decimal, not grader SQL.

The five inputs are explicitly declared by the task. SQL is used only by the
separate snapshot step to load them. Joins retain input multiplicity; NULL join
keys do not match. This module alone does not certify the task or its gold.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext


KEY = ('month_start', 'channel_key', 'segment_name', 'tier_name')
INTS = {'channel_key', 'impressions', 'clicks', 'conversions', 'order_count', 'new_customers', 'total_new_customers'}
TEXT = {'channel_name', 'channel_type', 'segment_name', 'tier_name'}
RATIOS = {'ctr', 'conversion_rate', 'roas', 'order_conversion_rate', 'new_customer_share', 'blended_cac',
          'ltv_90d_revenue_per_customer', 'ltv_90d_profit_per_customer', 'payback_90d_profit_ratio'}
MONEY = {'spend_amount', 'revenue_attributed', 'sales_revenue', 'sales_profit'}
COLUMNS = set(KEY) | INTS | TEXT | RATIOS | MONEY


def day(value):
    if value is None:
        return None
    if type(value) is datetime:
        if value.time().isoformat() != '00:00:00':
            raise ValueError('Expected midnight date representation')
        return value.date()
    if type(value) is date:
        return value
    parsed = datetime.fromisoformat(str(value))
    return day(parsed)


def month(value):
    value = day(value)
    return value.replace(day=1) if value is not None else None


def dec(value):
    return None if value is None else Decimal(str(value))


def add(left, right):
    return left if right is None else dec(right) if left is None else left + dec(right)


def ratio(top, bottom):
    return None if top is None or bottom is None or bottom == 0 else dec(top) / dec(bottom)


def index(rows, field):
    result = defaultdict(list)
    for row in rows:
        if row[field] is not None:
            result[row[field]].append(row)
    return result


def recompute(inputs):
    with localcontext() as context:
        context.prec = 40
        return _recompute(inputs)


def _recompute(inputs):
    dates = index(inputs['dates'], 'date_key')
    customers = index(inputs['customers'], 'customer_key')
    channels = index(inputs['channels'], 'channel_key')
    sales_by_customer = index(inputs['sales'], 'customer_key')
    marketing = {}
    fields = ('impressions', 'clicks', 'conversions', 'spend_amount', 'revenue_attributed')
    for row in inputs['marketing']:
        for d in dates.get(row['date_key'], []):
            key = (month(d['full_date']), row['channel_key'])
            group = marketing.setdefault(key, dict.fromkeys(fields))
            for field in fields:
                group[field] = add(group[field], row[field])

    sales_monthly = {}
    for row in inputs['sales']:
        for d in dates.get(row['date_key'], []):
            key = (month(d['full_date']), row['channel_key'])
            group = sales_monthly.setdefault(key, {'sales_revenue': None, 'sales_profit': None, 'orders': set()})
            group['sales_revenue'] = add(group['sales_revenue'], row['total_amount'])
            group['sales_profit'] = add(group['sales_profit'], row['profit_amount'])
            if row['order_id'] is not None:
                group['orders'].add(row['order_id'])

    # The SQL grouping first selects the minimum date_key, then the minimum
    # channel on that date. Repeated rows do not change either minimum.
    first = []
    tie_customers = 0
    for customer, rows in sales_by_customer.items():
        date_keys = [r['date_key'] for r in rows if r['date_key'] is not None]
        if not date_keys:
            continue
        first_key = min(date_keys)
        first_channels = {r['channel_key'] for r in rows if r['date_key'] == first_key and r['channel_key'] is not None}
        channel = min(first_channels) if first_channels else None
        tie_customers += len(first_channels) > 1
        # first_purchase_detail groups by the joined full_date, so duplicate
        # identical date-dimension entries do not duplicate first-purchase rows.
        for first_date in {day(d['full_date']) for d in dates.get(first_key, [])}:
            first.append((customer, channel, first_date))

    new_customers = defaultdict(set)
    ltv = {}
    boundary_89 = boundary_90 = 0
    for customer, channel, first_date in first:
        for dimension in customers.get(customer, []):
            segment = dimension['segment_name'] if dimension['segment_name'] is not None else 'Unknown'
            tier = dimension['tier_name'] if dimension['tier_name'] is not None else 'Unknown'
            key = (month(first_date), channel, segment, tier)
            new_customers[key].add(customer)
            if first_date is None:
                continue
            for sale in sales_by_customer[customer]:
                for d in dates.get(sale['date_key'], []):
                    sale_date = day(d['full_date'])
                    if sale_date is None:
                        continue
                    elapsed = (sale_date-first_date).days
                    boundary_89 += elapsed == 89
                    boundary_90 += elapsed == 90
                    if 0 <= elapsed < 90:
                        group = ltv.setdefault(key, {'revenue': None, 'profit': None})
                        group['revenue'] = add(group['revenue'], sale['total_amount'])
                        group['profit'] = add(group['profit'], sale['profit_amount'])

    totals = defaultdict(int)
    mixes = defaultdict(list)
    for key, customers_in_segment in new_customers.items():
        totals[key[:2]] += len(customers_in_segment)
        mixes[key[:2]].append(key)
    output = []
    without_new_customers = 0
    for key, m in marketing.items():
        # Equality joins do not match NULL month/channel keys.
        can_join = all(value is not None for value in key)
        segments = mixes.get(key, []) if can_join else []
        if not segments:
            without_new_customers += 1
        for segment_key in segments or [None]:
            count = len(new_customers[segment_key]) if segment_key is not None else None
            total = totals[key] or None if segment_key is not None else None
            share = ratio(count, total)
            sales = sales_monthly.get(key) if can_join else None
            lifetime = ltv.get(segment_key) if segment_key is not None and can_join else None
            for channel in channels.get(key[1], []) or [None]:
                row = {'month_start': key[0], 'channel_key': key[1],
                       'channel_name': channel['channel_name'] if channel else None,
                       'channel_type': channel['channel_type'] if channel else None,
                       'segment_name': segment_key[2] if segment_key is not None else None,
                       'tier_name': segment_key[3] if segment_key is not None else None,
                       **m, 'ctr': ratio(m['clicks'], m['impressions']),
                       'conversion_rate': ratio(m['conversions'], m['clicks']),
                       'roas': ratio(m['revenue_attributed'], m['spend_amount']),
                       'sales_revenue': sales['sales_revenue'] if sales else None,
                       'sales_profit': sales['sales_profit'] if sales else None,
                       'order_count': len(sales['orders']) if sales else None,
                       'order_conversion_rate': ratio(len(sales['orders']) if sales else None, m['conversions']),
                       'new_customers': count, 'total_new_customers': total, 'new_customer_share': share,
                       'blended_cac': ratio(m['spend_amount'], total),
                       'ltv_90d_revenue_per_customer': ratio(lifetime['revenue'] if lifetime else None, count),
                       'ltv_90d_profit_per_customer': ratio(lifetime['profit'] if lifetime else None, count),
                       'payback_90d_profit_ratio': ratio(lifetime['profit'] if lifetime else None,
                           m['spend_amount'] * share if share is not None and m['spend_amount'] is not None else None)}
                for field in INTS:
                    if row[field] is not None:
                        value = dec(row[field])
                        if value != value.to_integral_value():
                            raise ValueError(f'Input-derived nonintegral count: {field}')
                        row[field] = int(value)
                output.append(row)
    return output, {'input_rows': {k: len(v) for k, v in inputs.items()}, 'output_rows': len(output),
                    'marketing_month_channels': len(marketing), 'base_groups_without_new_customers': without_new_customers,
                    'first_date_channel_ties': tie_customers, 'ltv_day_89_joined_rows': boundary_89,
                    'ltv_day_90_joined_rows': boundary_90}


def compare_output(expected, observed):
    """Explicit nullable composite key and per-column comparison contract."""
    from dbt_bench.comparison import Column, TableContract, compare_tables

    def normalize(rows):
        result = {}
        for raw in rows:
            if not COLUMNS <= raw.keys():
                raise ValueError('Missing declared output columns')
            row = {name: raw[name] for name in COLUMNS}
            row['month_start'] = day(row['month_start'])
            if row['month_start'] is None or row['channel_key'] is None:
                raise ValueError('NULL required month/channel key')
            for name in INTS:
                value = row[name]
                if value is not None:
                    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                        raise ValueError(f'Expected exact integer representation: {name}')
                    if dec(value) != dec(value).to_integral_value():
                        raise ValueError(f'Fractional integer field: {name}')
                    row[name] = int(value)
            for name in MONEY | RATIOS:
                row[name] = dec(row[name])
            key = tuple(row[name] for name in KEY)
            if key in result:
                raise ValueError(f'Duplicate nullable business key: {key}')
            result[key] = row
        return result

    try:
        left, right = normalize(expected), normalize(observed)
    except (ValueError, TypeError, ArithmeticError) as exc:
        return {'equal': False, 'reason': str(exc)}
    if left.keys() != right.keys():
        return {'equal': False, 'reason': 'Missing or extra business keys',
                'missing': len(left.keys()-right.keys()), 'extra': len(right.keys()-left.keys())}
    columns = [Column('month_start', 'date')]
    columns += [Column(name, 'integer') for name in sorted(INTS)]
    columns += [Column(name, 'text') for name in sorted(TEXT)]
    columns += [Column(name, 'numeric', '0.000001', '0.000001') for name in sorted(MONEY)]
    columns += [Column(name, 'numeric', '0.000001', '0.0001') for name in sorted(RATIOS)]
    contract = TableContract(tuple(columns), ordered=True, allow_extra_columns=True)
    for key, row in left.items():
        result = compare_tables([row], [right[key]], contract)
        if not result.equal:
            return {'equal': False, 'reason': result.reason, 'key': [str(v) if v is not None else None for v in key]}
    return {'equal': True, 'reason': 'All declared columns match by explicit nullable business key', 'rows': len(left)}

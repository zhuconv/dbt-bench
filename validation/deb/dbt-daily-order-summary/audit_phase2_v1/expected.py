"""Independent daily expected rows; no native grader, model SQL or gold import."""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext

EXCLUDED = frozenset({'CANCELLED', 'RETURNED', 'FAILED'})


class InputPolicyUnknown(ValueError):
    """The public contract does not resolve this encountered input policy."""


def derive(rows):
    groups = {}
    statuses = Counter()
    with localcontext() as context:
        context.prec = 60
        for number, row in enumerate(rows):
            status, when, amount = (row.get(n) for n in ('status', 'ordered_at', 'grand_total'))
            if status is None:
                raise InputPolicyUnknown(f'Row {number}: NULL status interpretation is unspecified')
            if not isinstance(status, str):
                raise InputPolicyUnknown(f'Row {number}: nontext status')
            statuses[status] += 1
            if status in EXCLUDED:
                continue
            if when is None or amount is None:
                raise InputPolicyUnknown(f'Row {number}: eligible NULL date/amount interpretation is unspecified')
            timestamp = when if isinstance(when, datetime) else datetime.fromisoformat(when)
            if timestamp.tzinfo is not None:
                raise InputPolicyUnknown('Timezone-dependent date extraction requires an explicit session policy')
            if isinstance(amount, (float, bool)):
                raise InputPolicyUnknown('Exact original DECIMAL source must be captured losslessly')
            amount = Decimal(amount)
            if not amount.is_finite():
                raise InputPolicyUnknown('Nonfinite original revenue is outside the reviewed source domain')
            day = timestamp.date()
            count, total = groups.get(day, (0, Decimal(0)))
            groups[day] = count + 1, total + amount
        expected = [dict(order_date=day, order_count=count,
                         total_revenue=total.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
                    for day, (count, total) in sorted(groups.items())]
    return expected, {'source_rows': sum(statuses.values()), 'statuses': dict(statuses),
                      'eligible_orders': sum(r['order_count'] for r in expected),
                      'dates': len(expected), 'rounding': 'Decimal sum first, ROUND_HALF_UP to 0.01',
                      'null_policy': 'encountered eligible NULL or status NULL remains unknown'}

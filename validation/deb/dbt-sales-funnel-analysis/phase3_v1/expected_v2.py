"""Independent fixed-input funnel arithmetic under the reviewed blind interpretation.

The source aliases remain documented; matching gold never supplies expected values.
The case engine derive function reads original inputs only and never calls a grader.
"""
from collections import Counter
from case_engine_v1 import derive as recompute, trim

COLS = {
    'int_session_funnel': ['session_id', 'visitor_id', 'customer_id', 'session_start',
                           'has_product_view', 'has_add_to_cart', 'has_purchase', 'funnel_stage'],
    'funnel_conversion_rates': ['total_sessions', 'sessions_with_view', 'sessions_with_cart',
                                'sessions_with_purchase', 'view_rate', 'view_to_cart_rate',
                                'cart_to_purchase_rate', 'overall_conversion_rate'],
    'funnel_dropoff_analysis': ['dropoff_stage', 'session_count', 'dropoff_rate'],
}


def derive(raw):
    expected = recompute(raw)
    aliases = Counter((row['event_type'], row['event_name']) for row in raw['events'])
    tokens = Counter(str(row['is_converted']) for row in raw['sessions'])
    unknown = [token for token in tokens
               if token.strip().upper() not in ('TRUE', 'FALSE', 'T', 'F', 'YES', 'NO', 'Y', 'N', '1', '0')]
    data = {model: [[row[column] for column in columns] for row in expected[model]]
            for model, columns in COLS.items()}
    return {
        'columns': COLS,
        'policies': {'reviewed_blind': data},
        'profile': {
            'rows': len(raw['sessions']),
            'reviewed_policy': 'phase3-funnel-blind-v1',
            'conversion_tokens': dict(tokens),
            'unknown_boolean_tokens': unknown,
            'ambiguous_event_rows': sum(count for (kind, _), count in aliases.items()
                                        if str(trim(kind)).lower() in ('v', 'view', 'a')),
            'event_type_name_profile': [{'event_type': kind, 'event_name': name, 'rows': count}
                                         for (kind, name), count in aliases.items()],
        },
        'scope': 'Fixed reviewed input policy; no rounding/zero-denominator policy inferred for unseen data.',
    }

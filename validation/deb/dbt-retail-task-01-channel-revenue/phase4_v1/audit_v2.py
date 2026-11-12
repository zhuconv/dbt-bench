"""Reject an explicitly absent required column before value normalization."""
import audit_v1 as legacy


def evaluate(outputs, expected, workflow=None, timestamp_window=None):
    value = outputs.get(legacy.NAME, {})
    table = value.get('table'); required = set(legacy.COLUMNS)
    if table is not None:
        absent = required - set(table.columns) | required - set(table.physical_columns)
        absent |= {k for row in table.rows for k in required if k not in row}
        if absent:
            return {'status': 'scored', 'reward': 0,
                    'checks': {'required_columns': {'status': 'fail',
                        'reason': 'Actual declared required columns absent before normalization',
                        'missing': sorted(absent)}},
                    'model_requests': 0, 'native_rewards_modified': False}
    return legacy.evaluate(outputs, expected, workflow, timestamp_window)

"""Accept exact integer/text channel identifiers while preserving physical types."""
from copy import deepcopy
from dataclasses import replace
import audit_v2 as guarded


def evaluate(outputs, expected, workflow=None, timestamp_window=None):
    name = guarded.legacy.NAME
    value = outputs.get(name, {})
    table = value.get('table')
    if table is None or 'channel_key' not in table.columns:
        return guarded.evaluate(outputs, expected, workflow, timestamp_window)
    canonical = deepcopy(outputs)
    rows = []
    for row in table.rows:
        row = dict(row)
        if type(row.get('channel_key')) is int:
            row['channel_key'] = str(row['channel_key'])
        rows.append(row)
    canonical[name]['rows'] = rows
    canonical[name]['table'] = replace(table, rows=rows)
    result = guarded.evaluate(canonical, expected, workflow, timestamp_window)
    result['identifier_representation'] = {'actual_physical_type': table.physical_sql_types.get('channel_key'),
        'scope': 'Task requires text cast for the join, not a text-only output key. Integers are converted directly to exact decimal text for key alignment; floats are never coerced.'}
    return result

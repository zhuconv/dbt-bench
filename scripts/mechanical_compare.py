"""Lossless repeat/transfer comparison, with declared dynamic-column policies.

This checks reproducibility and transfer, not whether a business answer is gold.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path


def token(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def time_value(value):
    if isinstance(value, dict):
        if value.get('$type') not in {'datetime', 'date'}:
            raise ValueError('Expected a captured datetime')
        value = value['value']
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Naive timestamp needs an explicit timezone policy')
    return result.astimezone(timezone.utc)


def compare_rows(left, right, columns, *, ordered=False, dynamic=None, windows=None):
    dynamic = dynamic or {}
    detail = {'row_counts': [len(left), len(right)], 'dynamic_checks': [], 'column_multisets': {}}
    if len(left) != len(right):
        return {'equal': False, 'reason': 'Row counts differ', **detail}
    dynamic_indexes = set()
    for name, rule in dynamic.items():
        if name not in columns:
            return {'equal': None, 'reason': 'Dynamic column not captured: ' + name, **detail}
        if rule.get('kind') != 'current_timestamp' or not windows:
            return {'equal': None, 'reason': 'Unsupported/unbound dynamic rule: ' + name, **detail}
        index = columns.index(name)
        for side, rows in enumerate((left, right)):
            try:
                start, end = map(time_value, windows[side])
                values = [time_value(r[index]) for r in rows]
                passed = all(start <= v <= end for v in values)
            except (ValueError, TypeError) as exc:
                return {'equal': None, 'reason': str(exc), **detail}
            detail['dynamic_checks'].append({'column': name, 'side': side, 'rule': rule,
                                             'window': windows[side], 'values_checked': len(values), 'passed': passed})
            if not passed:
                return {'equal': False, 'reason': 'Dynamic timestamp outside execution interval', **detail}
        dynamic_indexes.add(index)
    indexes = [i for i in range(len(columns)) if i not in dynamic_indexes]
    for i in indexes:
        detail['column_multisets'][columns[i]] = Counter(token(r[i]) for r in left) == Counter(token(r[i]) for r in right)
    projected = [[token([row[i] for i in indexes]) for row in rows] for rows in (left, right)]
    equal = projected[0] == projected[1] if ordered else Counter(projected[0]) == Counter(projected[1])
    return {'equal': equal, 'reason': 'Typed rows match with multiplicity and row associations preserved' if equal else 'Typed row associations/values differ', **detail}


def compare_snapshots(left_path, right_path, reference_windows=None):
    left_path, right_path = Path(left_path), Path(right_path)
    left, right = (json.loads(p.read_text()) for p in (left_path, right_path))
    details = []
    if len(left['outputs']) != len(right['outputs']):
        return {'equal': False, 'reason': 'Declared output count differs', 'tables': []}
    for l, r in zip(left['outputs'], right['outputs']):
        spec = l['specification']
        detail = {'relation': spec['relation'], 'database': spec['database']}
        if spec != r['specification']:
            detail.update(equal=None, reason='Different output specifications')
        elif l.get('error') or r.get('error'):
            detail.update(equal=None, reason='Output read failed', errors=[l.get('error'), r.get('error')])
        elif {c['name']: c['type'] for c in l['schema']} != {c['name']: c['type'] for c in r['schema']}:
            detail.update(equal=False, reason='Column names or exact DuckDB types differ')
        else:
            columns = [c['name'] for c in l['schema']]
            rnames = [c['name'] for c in r['schema']]
            captured = []
            for metadata, path in ((l, left_path), (r, right_path)):
                data = (path.parent / metadata['file']).read_bytes()
                if hashlib.sha256(data).hexdigest() != metadata['sha256']:
                    raise ValueError('Captured output hash changed: ' + str(path))
                rows = [json.loads(line) for line in data.decode().splitlines()]
                if len(rows) != metadata['rows']:
                    raise ValueError('Captured row count differs from observer')
                captured.append(rows)
            lrows, rrows = captured
            rrows = [[row[rnames.index(name)] for name in columns] for row in rrows]
            windows = reference_windows
            if not windows and all(v.get('started_at_utc') and v.get('finished_at_utc') for v in (left, right)):
                windows = [[v['started_at_utc'], v['finished_at_utc']] for v in (left, right)]
            detail.update(compare_rows(lrows, rrows, columns, ordered=spec.get('ordered', False),
                dynamic=spec.get('dynamic_columns'), windows=windows))
            detail['exact_reproducibility'] = True
            detail['scope'] = 'Exact typed repeat/transfer equality except explicit dynamic rules; no global numeric coercion or relaxed native tolerance.'
        details.append(detail)
    equal = False if any(d['equal'] is False for d in details) else None if any(d['equal'] is None for d in details) else True
    return {'equal': equal, 'tables': details, 'left': str(left_path), 'right': str(right_path)}

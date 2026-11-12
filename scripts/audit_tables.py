"""Declared-table audit primitives for lossless mechanical-observer captures.

These checks cover an explicit table interface and, optionally, independently
derived expected rows. They do not establish dbt workflow or trust in a gold.
Missing/corrupt capture is unknown, never a successful or failed submission.
The module lives outside native validation implementation identity; audit
versions must include its hash and that of dbt_bench.comparison explicitly.
"""
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path

from dbt_bench.comparison import Column, TableContract, compare_tables


class CaptureError(ValueError):
    """Evidence unavailable, ambiguous or internally inconsistent."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CaptureError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _json(text):
    def constant(value):
        raise CaptureError('Untagged nonfinite value: ' + value)
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=constant)


def decode(value):
    """Decode the locked mechanical_observer representation without coercion."""
    if value is None or type(value) in (str, int, bool):
        return value
    if not isinstance(value, dict) or set(value) != {'$type', 'value'}:
        raise CaptureError('Unknown observer value representation')
    kind, item = value['$type'], value['value']
    scalar = {'decimal': Decimal, 'float': float, 'date': date.fromisoformat,
              'datetime': datetime.fromisoformat, 'time': time.fromisoformat,
              'bytes': bytes.fromhex}
    if kind in scalar and isinstance(item, str):
        return scalar[kind](item)
    if kind == 'list' and isinstance(item, list):
        return [decode(v) for v in item]
    if kind == 'dict' and isinstance(item, list):
        result = {}
        for pair in item:
            if not isinstance(pair, list) or len(pair) != 2:
                raise CaptureError('Malformed captured map')
            key, val = map(decode, pair)
            if key in result:
                raise CaptureError('Duplicate captured map key')
            result[key] = val
        return result
    raise CaptureError('Unknown observer tag: ' + str(kind))


@dataclass(frozen=True)
class CapturedTable:
    columns: tuple[str, ...]
    sql_types: dict[str, str]
    rows: list[dict]
    physical_columns: tuple[str, ...]
    physical_sql_types: dict[str, str]
    relation_type: str | None
    capture_ref: dict


@dataclass(frozen=True)
class TablePolicy:
    table: TableContract
    sql_types: dict[str, tuple[str, ...]]
    not_null: tuple[str, ...] = ()
    relation_types: tuple[str, ...] = ()

    def __post_init__(self):
        names = {c.name for c in self.table.columns}
        if set(self.sql_types) != names or any(not v or isinstance(v, str) or
                any(not isinstance(t, str) or not t for t in v) for v in self.sql_types.values()):
            raise ValueError('Declare allowed SQL types for every contract column')
        if not set(self.not_null) <= names:
            raise ValueError('Unknown non-null column')


def _schema(value):
    if not isinstance(value, list):
        raise CaptureError('SQL schema was not captured')
    names, types = [], {}
    for column in value:
        name, kind = column.get('name'), column.get('type')
        if not isinstance(name, str) or not isinstance(kind, str) or name in types:
            raise CaptureError('Malformed or duplicate captured columns')
        names.append(name)
        types[name] = kind
    return tuple(names), types


def load_observer_table(snapshot_ref, *, task_key, database, relation, stage, root=Path('.')):
    """Load exactly one declared relation, with snapshot and row hashes checked."""
    try:
        path = Path(root) / snapshot_ref['path']
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != snapshot_ref['sha256']:
            raise CaptureError('Snapshot hash mismatch')
        snapshot = _json(data)
        if snapshot.get('task_key') != task_key or snapshot.get('stage') != stage:
            raise CaptureError('Snapshot task/stage mismatch')
        matches = [v for v in snapshot['outputs'] if
                   v['specification']['database'] == database and
                   v['specification']['relation'] == relation]
        if len(matches) != 1:
            raise CaptureError('Declared output not uniquely captured')
        item = matches[0]
        if item.get('error'):
            raise CaptureError('Output read failed: ' + str(item['error']))
        columns, types = _schema(item.get('sql_schema'))
        if 'relation_sql_schema' in item:
            physical, physical_types = _schema(item['relation_sql_schema'])
        else:
            # Older locked observer captured DESCRIBE of its query only.
            # This proves the physical inventory only for its exact SELECT *
            # path, never for a projection that can hide extra columns.
            quoted = '.'.join('"' + p.replace('"', '""') + '"' for p in relation.split('.'))
            if item['specification'].get('columns') or item.get('query') != 'SELECT * FROM ' + quoted:
                raise CaptureError('Physical column inventory was not captured')
            physical, physical_types = columns, dict(types)
        dbapi, _ = _schema(item.get('schema'))
        if columns != dbapi or any(physical_types.get(n) != types[n] for n in columns):
            raise CaptureError('Projection and physical schema disagree')
        filename = item['file']
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise CaptureError('Output filename is not a local basename')
        rows_path = path.parent / filename
        if rows_path.is_symlink():
            raise CaptureError('Captured output must not be a symlink')
        raw_rows = rows_path.read_bytes()
        if hashlib.sha256(raw_rows).hexdigest() != item['sha256']:
            raise CaptureError('Captured rows hash mismatch')
        rows = []
        for line in raw_rows.splitlines():
            values = _json(line)
            if not isinstance(values, list) or len(values) != len(columns):
                raise CaptureError('Captured row width mismatch')
            rows.append(dict(zip(columns, map(decode, values))))
        if type(item['rows']) is not int or len(rows) != item['rows']:
            raise CaptureError('Captured row count mismatch')
        metadata = item.get('relation_metadata')
        if metadata is not None and (not isinstance(metadata, list) or len(metadata) != 1):
            raise CaptureError('Relation metadata not uniquely captured')
        return CapturedTable(columns, types, rows, physical, physical_types,
                             metadata[0]['table_type'] if metadata else None, dict(snapshot_ref))
    except CaptureError:
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CaptureError(str(exc)) from exc


def _valid_kind(value, kind):
    if value is None:
        return True
    return {'integer': lambda: type(value) is int,
            'boolean': lambda: type(value) is bool,
            'text': lambda: type(value) is str,
            'date': lambda: type(value) is date,
            'datetime': lambda: type(value) is datetime,
            'numeric': lambda: type(value) in (int, float, Decimal),
            'exact': lambda: True}[kind]()


def audit_table(candidate, policy, expected_rows=None):
    """Check explicit columns/types/grain; optional row comparison keeps keys.

    An empty table still has a schema. No tolerance is applied to identifiers;
    numeric tolerance is the per-column contract's existing comparison policy.
    Caller must validate expected provenance and bind all submission artifacts.
    """
    names = {c.name for c in policy.table.columns}
    checks = {}
    try:
        if len(candidate.columns) != len(set(candidate.columns)) or len(candidate.physical_columns) != len(set(candidate.physical_columns)):
            raise CaptureError('Duplicate capture column inventory')
        if set(candidate.sql_types) != set(candidate.columns) or set(candidate.physical_sql_types) != set(candidate.physical_columns):
            raise CaptureError('Malformed capture type inventory')
        if any(set(row) != set(candidate.columns) for row in candidate.rows):
            raise CaptureError('Malformed captured row inventory')
        checks['required_columns'] = names <= set(candidate.physical_columns)
        # Omitting a required column from the observer projection is a capture
        # limitation; a genuinely absent physical column is a submission error.
        if checks['required_columns'] and not names <= set(candidate.columns):
            raise CaptureError('Required physical columns were not captured')
        checks['column_set'] = policy.table.allow_extra_columns or set(candidate.physical_columns) == names
        checks['sql_types'] = all(candidate.physical_sql_types.get(n) in allowed for n, allowed in policy.sql_types.items())
        if policy.relation_types and candidate.relation_type is None:
            raise CaptureError('Required relation type was not captured')
        checks['relation_type'] = not policy.relation_types or candidate.relation_type in policy.relation_types
        checks['value_types'] = all(all(c.name in row and _valid_kind(row[c.name], c.kind) for c in policy.table.columns) for row in candidate.rows)
        checks['not_null'] = all(all(row.get(n) is not None for n in policy.not_null) for row in candidate.rows)
        keys, seen = policy.table.keys, set()
        key_valid = True
        for row in candidate.rows if keys else ():
            values = tuple(row.get(n) for n in keys)
            if any(v is None or isinstance(v, (float, Decimal)) and math.isnan(v) for v in values):
                key_valid = False
                continue
            key = tuple((type(v).__name__, v) for v in values)
            if key in seen:
                key_valid = False
            seen.add(key)
        checks['unique_grain'] = key_valid
        if expected_rows is not None:
            expected_rows = list(expected_rows)
            # Invalid expectations are a checker error, not a candidate failure.
            validation = compare_tables(expected_rows, expected_rows, policy.table)
            if not validation.equal:
                raise CaptureError('Invalid expected rows: ' + validation.reason)
            if all(checks.values()):
                compared = compare_tables(expected_rows, candidate.rows, policy.table)
                checks['values'] = compared.equal
                reason = compared.reason
            else:
                checks['values'] = False
                reason = 'Candidate interface/grain failed before row comparison'
        else:
            reason = 'Interface/grain only; business values and workflow are not assessed'
        return {'status': 'scored', 'reward': float(all(checks.values())), 'checks': checks,
                'reason': reason, 'capture': candidate.capture_ref,
                'scope': 'declared_table_and_expected_rows' if expected_rows is not None else 'declared_table_interface_only'}
    except (CaptureError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        return {'status': 'unknown', 'reward': None, 'checks': checks, 'reason': str(exc),
                'capture': candidate.capture_ref}

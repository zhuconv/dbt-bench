"""Independent ads transformation from the three declared public CSV inputs.

No verifier, SQL, hidden expected rows or dbt macros are imported. Counts retain
integer precision; exact integer-valued Decimal representations are equivalent.
"""
from collections import Counter
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

SOURCES = {
    'google': ('googleads.csv', ('views',)),
    'meta': ('metaads.csv', ('views_1', 'views_2')),
    'tiktok': ('tiktokads.csv', ('views_1',)),
}
STAGING_COLUMNS = ('ad_date', 'clicks', 'impressions', 'views', 'conversions')
INT_COLUMNS = ('source', *STAGING_COLUMNS)
TABLES = {
    'google': 'stg__ads_googleads', 'meta':'stg__ads_metaads',
    'tiktok':'stg__ads_tiktokads', 'unified':'int__ads_unified',
}


def count(value):
    if value is None or isinstance(value, bool):
        raise ValueError('A count must be a finite exact integral numeric value')
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid count') from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError('Nonintegral/nonfinite count')
    return int(number)


def canonical(row, unified=False):
    day = str(row['ad_date'])
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('Expected an ISO date without time')
    values = (day, *(count(row[c]) for c in STAGING_COLUMNS[1:]))
    if unified:
        if row['source'] not in SOURCES:
            raise ValueError('Unknown platform label')
        return (row['source'], *values)
    return values


def derive(inputs):
    staged, combined = {}, []
    for source, (_, view_columns) in SOURCES.items():
        records = set()
        for raw in inputs[source]:
            record = {
                'ad_date':raw['ad_date'], 'clicks':count(raw['clicks']),
                'impressions':count(raw['impressions']),
                'views':sum(count(raw[c]) for c in view_columns),
                'conversions':count(raw['conversions']),
            }
            records.add(canonical(record))
        staged[source] = sorted(records)
        combined.extend((source, *record) for record in staged[source])
    return {**staged, 'unified':sorted(combined)}


def read_inputs(directory):
    inputs = {}
    for source, (filename, views) in SOURCES.items():
        with (Path(directory)/filename).open(newline='') as stream:
            reader = csv.DictReader(stream)
            required = {'ad_date','clicks','impressions','conversions',*views}
            if set(reader.fieldnames or ()) != required:
                raise ValueError('Unexpected declared CSV schema: '+filename)
            inputs[source] = list(reader)
    return inputs


def compare(expected, observed, unified=False):
    wanted = Counter(tuple(row) for row in expected)
    actual = Counter(canonical(row, unified) for row in observed)
    missing, extra = wanted-actual, actual-wanted
    return {'equal':not missing and not extra,
            'expected_rows':sum(wanted.values()), 'actual_rows':sum(actual.values()),
            'missing': [{'row':list(row),'count':n} for row,n in sorted(missing.items())],
            'extra': [{'row':list(row),'count':n} for row,n in sorted(extra.items())]}

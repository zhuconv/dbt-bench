"""Workday case proof from raw rows, independent of dbt and the native grader.

This is a scoped case-analysis helper, not a certified supplemental checker.
The fixed single-source project gives every staged row source_relation=''.
"""
from collections import Counter
from functools import lru_cache
import json

CORE = {
    'job_profile_id': 'id', 'job_profile_code': 'job_profile_code',
    'job_title': 'title', 'private_title': 'private_title',
    'job_summary': 'summary', 'job_description': 'description',
}
AGGREGATES = ('job_family_codes', 'job_family_summaries',
              'job_family_group_codes', 'job_family_group_summaries')
INPUTS = ('workday_job_profile_data', 'workday_job_family_job_profile_data',
          'workday_job_family_data', 'workday_job_family_job_family_group_data',
          'workday_job_family_group_data', 'workday_organization_job_family_data')


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def recompute(inputs):
    """Explicit nested relations, with no SQL or native expected/gold access."""
    active = {name: [r for r in inputs[name]['rows'] if r['_fivetran_deleted'] is not True]
              for name in INPUTS}
    rows = []
    for profile in active[INPUTS[0]]:
        groups = [set() for _ in AGGREGATES]
        for link in active[INPUTS[1]]:
            if link['job_profile_id'] != profile['id']:
                continue
            for family in active[INPUTS[2]]:
                if family['id'] is None or family['id'] != link['job_family_id']:
                    continue
                for index, name in [(0, 'job_family_code'), (1, 'summary')]:
                    if family[name] is not None:
                        groups[index].add(family[name])
                for bridge in active[INPUTS[3]]:
                    if bridge['job_family_id'] != family['id']:
                        continue
                    for group in active[INPUTS[4]]:
                        if group['id'] is None or group['id'] != bridge['job_family_group_id']:
                            continue
                        for index, name in [(2, 'job_family_group_code'), (3, 'summary')]:
                            if group[name] is not None:
                                groups[index].add(group[name])
        rows.append({**{column: profile[source] for column, source in CORE.items()},
                     'source_relation': '',
                     **{column: sorted(values) for column, values in zip(AGGREGATES, groups)}})
    return sorted(rows, key=lambda row: row['job_profile_id'])


def aggregate_matches(actual, members):
    """Accept unordered distinct members, without splitting embedded delimiters.

    Upstream string_agg has no ordering requirement. NULL/empty string/empty
    list for *no members* are treated as non-discriminating in this case proof;
    this is not a general NULL substitution rule or a final task adjudication.
    """
    if not members:
        return actual is None or actual == '' or actual == []
    if isinstance(actual, list):
        return all(isinstance(x, str) for x in actual) and Counter(actual) == Counter(members)
    if not isinstance(actual, str):
        return False
    if len(set(members)) != len(members) or not all(isinstance(x, str) for x in members):
        raise ValueError('Expected distinct non-NULL string members')

    @lru_cache(None)
    def match(offset, used):
        if used == (1 << len(members)) - 1:
            return offset == len(actual)
        for index, member in enumerate(members):
            if used & (1 << index):
                continue
            token = (', ' if used else '') + member
            if actual.startswith(token, offset) and match(offset + len(token), used | (1 << index)):
                return True
        return False

    return match(0, 0)


def assess(expected, output):
    required = set(CORE) | {'source_relation'} | set(AGGREGATES)
    columns = {c['name'] for c in output['columns']}
    errors = []
    if required - columns:
        return [{'kind': 'missing_columns', 'columns': sorted(required - columns)}]
    expected_map = {(r['source_relation'], r['job_profile_id']): r for r in expected}
    actual_map = {}
    for row in output['rows']:
        key = (row['source_relation'], row['job_profile_id'])
        if key in actual_map:
            errors.append({'kind': 'duplicate_key', 'key': key})
        actual_map[key] = row
    for key in expected_map.keys() - actual_map.keys():
        errors.append({'kind': 'missing_row', 'key': key})
    for key in actual_map.keys() - expected_map.keys():
        errors.append({'kind': 'extra_row', 'key': key})
    for key in sorted(expected_map.keys() & actual_map.keys()):
        left, right = expected_map[key], actual_map[key]
        for column in sorted(required):
            good = (aggregate_matches(right[column], left[column]) if column in AGGREGATES
                    else encoded(right[column]) == encoded(left[column]))
            if not good:
                errors.append({'kind': 'value', 'key': key, 'column': column,
                               'expected': left[column], 'actual': right[column]})
    return errors

"""Small declared Workday relationship fixture; original five core profiles remain.

Every expected aggregate below is written explicitly, independently of the
relation traversal in independent.py. All changes are additional-input cases.
"""
from copy import deepcopy
from independent import AGGREGATES, INPUTS


def construct(pristine):
    data = deepcopy(pristine)
    profiles = sorted(data[INPUTS[0]]['rows'], key=lambda row: row['id'])
    if len(profiles) != 5 or any(r['_fivetran_deleted'] for r in profiles):
        raise ValueError('Fixture is scoped to the pinned five active profiles')
    ids = [r['id'] for r in profiles]

    def row(table, **values):
        result = {col['name']: None for col in data[table]['columns']}
        result.update(_fivetran_deleted=False, _fivetran_synced='2023-10-30 05:43:45')
        result.update(values)
        if set(result) != {col['name'] for col in data[table]['columns']}:
            raise ValueError('Fixture column absent from pinned input schema')
        return result

    deleted = deepcopy(profiles[0])
    deleted.update(id='fixture-deleted-profile', _fivetran_deleted=True)
    data[INPUTS[0]]['rows'] = profiles + [deleted]
    family_defs = [
        ('a', 'ALPHA', 'Ops, North', False, False),
        ('b', 'BETA', 'Beta', False, False),
        ('i', 'INACTIVE', 'Inactive retained', True, False),
        ('d', 'DELETED_FAMILY', 'Deleted family', False, True),
        ('l', 'DELETED_LINK', 'Deleted link', False, False),
        ('n', None, None, False, False),
        ('e', '', 'Empty-code', False, False),
    ]
    data[INPUTS[2]]['rows'] = [row(INPUTS[2], id='f-' + key, job_family_code=code,
                                  summary=summary, inactive=inactive, _fivetran_deleted=deleted)
                              for key, code, summary, inactive, deleted in family_defs]
    group_defs = [('1', 'GENERAL', 'General', False), ('2', 'SPECIAL', 'Special', False),
                  ('3', 'RETAINED', 'Retained', False), ('o', 'ORG_ONLY', 'Wrong route', False),
                  ('d', 'DELETED_GROUP', 'Deleted group', True),
                  ('l', 'DELETED_BRIDGE', 'Deleted bridge', False)]
    data[INPUTS[4]]['rows'] = [row(INPUTS[4], id='g-' + key, job_family_group_code=code,
                                  summary=summary, inactive=False, _fivetran_deleted=deleted)
                              for key, code, summary, deleted in group_defs]
    links = [(0, 'a', False), (0, 'a', False), (0, 'b', False), (0, 'i', False),
             (0, 'd', False), (0, 'l', True), (1, 'b', False), (2, 'missing', False),
             (4, 'n', False), (4, 'e', False)]
    data[INPUTS[1]]['rows'] = [row(INPUTS[1], job_profile_id=ids[index], job_family_id='f-' + family,
                                  _fivetran_deleted=deleted) for index, family, deleted in links]
    data[INPUTS[1]]['rows'].append(row(INPUTS[1], job_profile_id='fixture-deleted-profile', job_family_id='f-a'))
    bridges = [('a', '1', False), ('a', '1', False), ('a', '2', False), ('a', 'd', False),
               ('a', 'l', True), ('b', '2', False), ('i', '3', False), ('e', '1', False),
               ('missing', 'o', False)]
    data[INPUTS[3]]['rows'] = [row(INPUTS[3], job_family_id='f-' + family, job_family_group_id='g-' + group,
                                  _fivetran_deleted=deleted) for family, group, deleted in bridges]
    data[INPUTS[5]]['rows'] = [row(INPUTS[5], job_family_id='f-' + family,
                                  job_family_group_id='g-o', organization_id='fixture-organization')
                              for family in ['a', 'b', 'i', 'e', 'missing']]
    manual = {
        ids[0]: [['ALPHA', 'BETA', 'INACTIVE'], ['Beta', 'Inactive retained', 'Ops, North'],
                 ['GENERAL', 'RETAINED', 'SPECIAL'], ['General', 'Retained', 'Special']],
        ids[1]: [['BETA'], ['Beta'], ['SPECIAL'], ['Special']],
        ids[2]: [[], [], [], []], ids[3]: [[], [], [], []],
        ids[4]: [[''], ['Empty-code'], ['GENERAL'], ['General']],
    }
    # These deletion cases respect the staged relationship unique-key tests.
    # The earlier fixture intentionally included duplicate links as robustness
    # cases; here they are removed while all hand-calculated members stay equal.
    for table, keys in [(INPUTS[1], ('job_profile_id', 'job_family_id')),
                        (INPUTS[3], ('job_family_id', 'job_family_group_id'))]:
        unique = {}
        for row in data[table]['rows']:
            key = tuple(row[k] for k in keys)
            if key in unique and row != unique[key]:
                raise ValueError('Conflicting relationship rows in unique-key fixture')
            unique[key] = row
        data[table]['rows'] = list(unique.values())
    return data, {key: dict(zip(AGGREGATES, values)) for key, values in manual.items()}

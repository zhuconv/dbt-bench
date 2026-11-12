"""Validate absence-aware captures, retaining the original v1 byte checks."""
from difficulty_submission import verify_capture as verify_v1


def verify_absences(capture):
    if capture.get('schema_version') != 2:
        return
    if capture.get('inventory_complete') is not True:
        raise ValueError('Capture lacks complete inventory')
    expected = {('database_wal' if r['path'].endswith('.wal') else 'database', r['path'])
                for r in capture['databases'] if r['exists'] is False}
    project = capture['project_observation']
    if project['exists'] is False:
        expected.add(('project_root', capture['project_root']))
        if capture['project_files']:
            raise ValueError('Absent project has contradictory captured files')
    elif project['exists'] is not True or project['kind'] != 'directory':
        raise ValueError('Project state not observed')
    actual = set()
    for row in capture['absence_observations']:
        key = row['role'], row['path']
        if (key in actual or not row['stable_absence'] or row['before'] != {'exists': False, 'errno': 2}
                or row['after']['exists'] is not False or row['after']['errno'] != 2
                or row['after']['path'] != row['path']):
            raise ValueError('Absence was not independently observed twice')
        actual.add(key)
    if actual != expected:
        raise ValueError('Incomplete or foreign absence inventory')


def verify_capture(trial, task_key):
    result = verify_v1(trial, task_key)
    verify_absences(result[0])
    return result

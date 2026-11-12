"""Explicit source transfer and exact replay checks for Workday diagnostics."""
from collections import Counter
import hashlib
import json
from pathlib import Path

SOURCE_DIRS = {'models', 'macros', 'dbt_packages'}
SOURCE_FILES = {'dbt_project.yml', 'profiles.yml', 'packages.yml', 'package-lock.yml', '.user.yml'}
GENERATED_DIRS = {'target', 'logs'}
INPUT_TABLES = (
    'workday_job_profile_data', 'workday_job_family_job_profile_data',
    'workday_job_family_job_family_group_data', 'workday_job_family_data',
    'workday_job_family_group_data', 'workday_organization_role_data',
    'workday_organization_role_worker_data', 'workday_organization_job_family_data',
    'workday_organization_data', 'workday_position_organization_data',
    'workday_position_data', 'workday_position_job_profile_data',
    'workday_worker_history_data', 'workday_personal_information_history_data',
    'workday_person_name_data', 'workday_personal_information_ethnicity_data',
    'workday_military_service_data', 'workday_person_contact_email_address_data',
    'workday_worker_position_history_data', 'workday_worker_leave_status_data',
    'workday_worker_position_organization_history_data',
)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def source_manifest(project):
    result = {}
    for path in sorted(Path(project).rglob('*')):
        rel = path.relative_to(project)
        if rel.parts[0] in GENERATED_DIRS:
            continue
        if path.is_symlink():
            raise ValueError('Unreviewed source symlink: ' + str(rel))
        if not path.is_file():
            continue
        if str(rel) == 'workday.duckdb':
            continue
        if path.suffix.lower() in {'.duckdb', '.wal', '.db'}:
            raise ValueError('Database outside the declared initial input: ' + str(rel))
        if not (rel.parts[0] in SOURCE_DIRS or str(rel) in SOURCE_FILES):
            raise ValueError('Unreviewed project file: ' + str(rel))
        result[rel.as_posix()] = {'sha256': sha(path), 'executable': bool(path.stat().st_mode & 0o111)}
    return result


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def exact_table_equal(left, right):
    if left is None or right is None:
        return left is right
    return (left['columns'] == right['columns'] and
            Counter(encoded(row) for row in left['rows']) == Counter(encoded(row) for row in right['rows']))


def verify_full_run(run_results, manifest):
    args = run_results['args']
    if args.get('which') != 'run' or args.get('select') or args.get('exclude'):
        raise ValueError('Evidence is not an unselected dbt run')
    required = {key for key, node in manifest['nodes'].items()
                if node['resource_type'] == 'model' and node['config']['enabled']}
    results = run_results['results']
    observed = [row['unique_id'] for row in results]
    if not required or set(observed) != required or len(observed) != len(required):
        raise ValueError('Executed model IDs do not cover every enabled model exactly once')
    if any(row['status'] != 'success' for row in results):
        raise ValueError('At least one enabled model did not run successfully')
    if run_results['metadata']['invocation_id'] != manifest['metadata']['invocation_id']:
        raise ValueError('Manifest and run evidence belong to different invocations')
    return sorted(required)

"""Read-only Workday capture before and after the unchanged native verifier."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

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
    'workday_worker_position_organization_history_data')
PROJECT = Path('/workspace')
DATABASE = PROJECT / 'workday.duckdb'
TARGET = 'workday__job_overview'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)


def table(connection, name):
    cur = connection.execute('select * from main."' + name + '"')
    cols = [{'name': c[0].lower(), 'type': str(c[1])} for c in cur.description]
    rows = [dict(zip([c['name'] for c in cols], row)) for row in cur.fetchall()]
    return {'columns': cols, 'rows': sorted(rows, key=encoded)}


def collect(phase):
    if phase not in {'before-native', 'after-native'}:
        raise ValueError('Unknown collection phase')
    out = Path('/logs/verifier/workday-audit') / phase
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'phase': phase, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'collector_sha256': sha(__file__), 'read_only_database': True, 'dbt_invoked': False,
        'files': {}, 'project_files': {}, 'errors': [], 'database_sha256_before': None,
        'database_sha256_after': None, 'relation_type': None,
        'context': {'uid': os.getuid(), 'cwd': os.getcwd(), 'python': sys.executable,
                    'python_version': sys.version}}

    def save(name, content):
        data = content if isinstance(content, bytes) else (encoded(content) + '\n').encode()
        target = out / name;target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(data)
        report['files'][name] = {'sha256': hashlib.sha256(data).hexdigest()}

    try:
        import duckdb
        report['context']['duckdb_version'] = duckdb.__version__
        paths = set()
        for directory in ['models', 'macros', 'analyses', 'snapshots']:
            paths.update(p for p in (PROJECT / directory).rglob('*') if p.is_file() or p.is_symlink())
        for name in ['dbt_project.yml', 'profiles.yml', 'packages.yml', 'package-lock.yml']:
            if (PROJECT / name).exists():paths.add(PROJECT / name)
        for path in sorted(paths):
            if path.is_symlink():raise ValueError('Unreviewed source symlink: ' + str(path))
            relative = path.relative_to(PROJECT).as_posix()
            save('project/' + relative, path.read_bytes())
            report['project_files'][relative] = {'sha256': sha(path), 'executable': bool(path.stat().st_mode & 0o111)}
        for name in ['target/manifest.json', 'target/run_results.json', 'logs/dbt.log']:
            path = PROJECT / name
            if path.is_symlink():raise ValueError('Unreviewed generated-artifact symlink: ' + str(path))
            if path.is_file():save(name, path.read_bytes())
            else:report['files'][name] = {'absent': True}
        report['database_sha256_before'] = sha(DATABASE)
        with duckdb.connect(str(DATABASE), read_only=True) as connection:
            save('inputs.json', {name: table(connection, name) for name in INPUT_TABLES})
            relations = connection.execute('select table_type from information_schema.tables where table_schema=? and table_name=?', ['main', TARGET]).fetchall()
            if len(relations) > 1:raise ValueError('Ambiguous target relation')
            report['relation_type'] = relations[0][0] if relations else None
            save('output.json', table(connection, TARGET) if relations else None)
        report['database_sha256_after'] = sha(DATABASE)
        if report['database_sha256_after'] != report['database_sha256_before']:
            raise ValueError('Database changed during read-only collection')
    except Exception as exc:
        report['errors'].append({'type': type(exc).__name__, 'message': str(exc)})
    (out / 'snapshot.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'phase': phase, 'errors': report['errors'], 'relation_type': report['relation_type']}))
    # Collection failure is recorded, but must never replace native grading.


if __name__ == '__main__':
    collect(sys.argv[1])

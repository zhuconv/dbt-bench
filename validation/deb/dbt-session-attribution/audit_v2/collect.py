"""Read declared Session inputs/submission around native grading, without dbt."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

PROJECTS = {'native': '/app/dbt_models_duckdb', 'instruction': '/app/dbt_transforms'}
DATABASE = Path('/app/database/retail.duckdb')
SOURCE_DIRS = ('models', 'macros', 'analyses', 'snapshots', 'seeds')
CONFIG_FILES = ('dbt_project.yml', 'profiles.yml', 'packages.yml', 'package-lock.yml')
COPIED_FILES = (*CONFIG_FILES, 'models/marts/attribution_report.sql',
                'target/manifest.json', 'target/run_results.json', 'logs/dbt.log')
INPUTS = (
    ('sessions', 'stg_digital__web_sessions', ('session_id', 'customer_id', 'session_start',
     'referrer', 'utm_source', 'utm_medium', 'device_type', 'duration_seconds', 'page_views',
     'is_converted', 'order_id')),
    ('sales', 'stg_analytics__fact_sales', ('order_id', 'total_amount')),
)


def encoded(value):
    return json.dumps(value, default=str, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''): h.update(chunk)
    return h.hexdigest()


def safe_source(path, project):
    if path.is_symlink() or not path.resolve().is_relative_to(project.resolve()):
        raise ValueError('Unreviewed source symlink/path: ' + str(path))


def database_files():
    return {str(p): sha(p) if p.exists() else None for p in [DATABASE, Path(str(DATABASE) + '.wal')]}


def collect(phase):
    if phase not in {'before-native', 'after-native'}: raise ValueError('Unknown capture phase')
    out = Path('/logs/verifier/session-audit') / phase
    out.mkdir(parents=True, exist_ok=False)
    result = dict(schema_version=1, phase=phase, timestamp_utc=datetime.now(timezone.utc).isoformat(),
        collector_sha256=sha(__file__), read_only_database=True, dbt_invoked=False,
        arbitrary_submission_sql_executed=False, files={}, projects={}, errors=[],
        database_files_before=None, database_files_after=None, relation_type=None,
        context={'python': sys.executable, 'python_version': sys.version, 'uid': os.getuid(),
                 'cwd': os.getcwd(), 'database': str(DATABASE),
                 'native_project_env': os.environ.get('DBT_PROJECT_DIR_DUCKDB')})

    def save(name, content):
        data = content if isinstance(content, bytes) else (encoded(content) + '\n').encode()
        target = out / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        result['files'][name] = {'sha256': hashlib.sha256(data).hexdigest()}

    try:
        import duckdb
        result['context']['duckdb_version'] = duckdb.__version__
        if os.environ.get('DUCKDB_PATH', str(DATABASE)) != str(DATABASE):
            raise ValueError('Undeclared database location')
        for label, value in PROJECTS.items():
            project = Path(value); safe_source(project, project)
            inventory = {}
            paths = {project / name for name in CONFIG_FILES if (project / name).exists()}
            for directory in SOURCE_DIRS:
                parent = project / directory
                safe_source(parent, project)
                paths.update(p for p in parent.rglob('*') if p.is_file() or p.is_symlink())
            for path in sorted(paths):
                safe_source(path, project)
                if path.is_file():
                    inventory[path.relative_to(project).as_posix()] = {
                        'sha256': sha(path), 'executable': bool(path.stat().st_mode & 0o111)}
            result['projects'][label] = {'path': value, 'present': project.is_dir(), 'source_inventory': inventory}
            for name in COPIED_FILES:
                path = project / name; safe_source(path, project)
                target = 'projects/' + label + '/' + name
                if path.is_file(): save(target, path.read_bytes())
                else: result['files'][target] = {'absent': True}
        result['database_files_before'] = database_files()
        with duckdb.connect(str(DATABASE), read_only=True) as connection:
            data = {}
            for key, table, columns in INPUTS:
                cur = connection.execute('select ' + ','.join(columns) + ' from main.' + table)
                values = [dict(zip(columns, row)) for row in cur.fetchall()]
                # Exact compact encoding matches the explicitly pinned input profile.
                data[key] = sorted(json.loads(encoded(values)), key=encoded)
            save('inputs.json', data)
            types = connection.execute('select table_type from information_schema.tables where table_schema=? and table_name=?',
                                       ['main', 'attribution_report']).fetchall()
            if len(types) > 1: raise ValueError('Ambiguous target relation')
            result['relation_type'] = types[0][0] if types else None
            output = None
            if types:
                cur = connection.execute('select * from main.attribution_report')
                columns = [c[0].lower() for c in cur.description]
                if len(set(columns)) != len(columns): raise ValueError('Duplicate normalized output columns')
                output = {'columns': columns, 'rows': sorted([dict(zip(columns, r)) for r in cur.fetchall()], key=encoded),
                          'relation_type': types[0][0]}
            save('output.json', output)
        result['database_files_after'] = database_files()
        if result['database_files_before'] != result['database_files_after']:
            raise ValueError('Database/WAL changed during read-only capture')
    except Exception as exc:
        result['errors'].append({'type': type(exc).__name__, 'message': str(exc)})
    (out / 'snapshot.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'phase': phase, 'errors': result['errors'], 'relation_type': result['relation_type']}))


if __name__ == '__main__': collect(sys.argv[1])

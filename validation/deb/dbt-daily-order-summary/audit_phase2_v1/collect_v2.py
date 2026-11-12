"""Task-specific read-only Phase2 capture. Does not run dbt or modify a database.

The trusted Harbor harness owns output location and pins this script. Process
chronology/environment must be captured by the runner separately; these are not
inferred from final files. Current implementation covers the pinned DuckDB task.
"""
import argparse
import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import traceback


def value(v):
    if v is None or type(v) in (str, int, bool):
        return v
    if isinstance(v, Decimal):
        return {'$type': 'decimal', 'value': str(v)}
    if type(v) is float:
        return {'$type': 'float', 'value': repr(v)}
    if isinstance(v, datetime.datetime):
        return {'$type': 'datetime', 'value': v.isoformat()}
    if isinstance(v, datetime.date):
        return {'$type': 'date', 'value': v.isoformat()}
    raise TypeError(type(v).__name__)


def ref(p):
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def collect(out, phase):
    out.mkdir(parents=True, exist_ok=False)
    root = Path('/app/dbt_project')
    def database_hashes(database):
        result = {}
        for name in (database, database+'.wal'):
            path = Path(name)
            result[name] = {'exists': path.exists()}
            if path.is_file():
                with path.open('rb') as stream:
                    result[name].update(sha256=hashlib.file_digest(stream, 'sha256').hexdigest(), size=path.stat().st_size)
        return result

    database = None
    result = dict(schema_version=2, task_key='deb__dbt-daily-order-summary', phase=phase,
                  started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  environment={k: os.environ.get(k) for k in ('DB_TYPE', 'DUCKDB_PATH', 'DBT_PROFILES_DIR')},
                  project_path=str(root), project_exists=root.exists(), files={}, probed_paths=[],
                  collector=ref(Path(__file__)), read_only_capture=True)
    try:
        assert os.environ.get('DB_TYPE') == 'duckdb', 'Only current DuckDB binding is reviewed'
        database = os.environ.get('DUCKDB_PATH', '/app/database/retail.duckdb')
        result['database'] = database
        result['database_hashes_before'] = database_hashes(database)
        import duckdb
        result['duckdb'] = duckdb.__version__
        manifest = None
        for name in ['dbt_project.yml', 'profiles.yml', 'target/manifest.json', 'target/run_results.json']:
            result['probed_paths'].append(name)
            p = root/name
            if p.is_file() and not p.is_symlink():
                assert p.resolve().is_relative_to(root.resolve())
                dest = out/'project'/name; dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(p.read_bytes()); result['files'][name] = dict(ref(dest), runtime_path=str(p))
                if name == 'target/manifest.json':
                    manifest = json.loads(p.read_text())
        if manifest:
            models = [(uid, n) for uid, n in manifest.get('nodes', {}).items() if n.get('resource_type') == 'model' and n.get('name') == 'daily_order_summary']
            for uid, n in models:
                name = n['original_file_path']; p = root/name
                assert not Path(name).is_absolute() and p.resolve().is_relative_to(root.resolve())
                result['probed_paths'].append(name)
                if p.is_file() and not p.is_symlink():
                    dest = out/'project'/name; dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(p.read_bytes()); result['files'][name] = dict(ref(dest), runtime_path=str(p))
        with duckdb.connect(database, read_only=True) as conn:
            if phase == 'initial':
                cur = conn.execute('SELECT ORDER_ID, ORDERED_AT, GRAND_TOTAL, STATUS FROM ORDERS.ORDERS ORDER BY ORDER_ID')
                columns = [x[0].lower() for x in cur.description]
                path = out/'initial-orders.jsonl'
                with path.open('w') as stream:
                    count = 0
                    for row in cur.fetchall():
                        stream.write(json.dumps([value(v) for v in row])+'\n'); count += 1
                result['original_orders'] = dict(ref(path), columns=columns, rows=count,
                    sql_schema=conn.execute('DESCRIBE SELECT ORDER_ID,ORDERED_AT,GRAND_TOTAL,STATUS FROM ORDERS.ORDERS').fetchall())
            elif manifest and len(models) == 1:
                uid, node = models[0]; sql = node.get('compiled_code')
                if sql:
                    path = out/'compiled-select.sql'; path.write_text(sql)
                    statements = conn.extract_statements(sql)
                    result['compiled_statement_types'] = [str(statement.type) for statement in statements]
                    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
                        raise ValueError('Only one parsed SELECT statement is eligible for ordered result capture')
                    cur = conn.execute(sql); columns = [x[0] for x in cur.description]
                    rows = cur.fetchall(); data = out/'ordered-result.jsonl'
                    with data.open('w') as stream:
                        for row in rows: stream.write(json.dumps([value(v) for v in row])+'\n')
                    result['ordered_result'] = dict(ref(data), columns=columns, rows=len(rows),
                        compiled_sql=ref(path), unique_id=uid, order_preserved=True,
                        scope='Actual compiled SELECT result; no extra ORDER BY was added by collector')
                else:
                    result['ordered_result_missing'] = 'Manifest contains no compiled SELECT'
    except Exception as exc:
        result['capture_error'] = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
    if database is not None:
        result['database_hashes_after'] = database_hashes(database)
        result['database_and_wal_unchanged'] = result.get('database_hashes_before') == result['database_hashes_after']
        if not result['database_and_wal_unchanged']:
            result['capture_error'] = dict(type='DatabaseIntegrityError', message='Database/WAL changed during declared read-only capture')
    result['read_only_scope'] = 'Single parsed SELECT on a read_only DuckDB connection, with whole DB and WAL hashes checked. Does not certify absence of all external function side effects.'
    result['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path = out/'capture.json'; path.write_text(json.dumps(result, default=str, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--output', required=True); p.add_argument('--phase', choices=['initial', 'final'], required=True)
    args = p.parse_args(); r = collect(Path(args.output), args.phase)
    print(json.dumps({'phase': args.phase, 'capture_error': r.get('capture_error')}))

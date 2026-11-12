"""Container-side read-only projection of actual submitted DB/WAL and SQL ASTs."""
import argparse
import hashlib
import json
from pathlib import Path

from mechanical_observer import encode, observe


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main(request, out):
    import duckdb
    if duckdb.__version__ != '1.2.2' or request['model_requests'] != 0:
        raise ValueError('Locked DuckDB1.2.2 and no model requests are required')
    if out.exists():
        raise ValueError('Never replace a database observation')
    before = {name: sha(name) if Path(name).is_file() else None
              for db in request['observation']['database_paths'] for name in (db, db + '.wal')}
    observe(request['observation'], 'agent-submission', out)
    raw = {}
    for name, item in request['raw_queries'].items():
        with duckdb.connect(item['database'], read_only=True) as c:
            statements = c.extract_statements(item['sql'])
            if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
                raise ValueError('Raw projection is not a single read-only SELECT')
            schema = c.execute('DESCRIBE ' + item['sql']).fetchall()
            cursor = c.execute(item['sql'])
            columns = [r[0] for r in cursor.description]
            path = out / ('raw-' + name + '.jsonl')
            rows = 0
            with path.open('x') as f:
                while batch := cursor.fetchmany(1000):
                    for row in batch:
                        f.write(json.dumps([encode(v) for v in row]) + '\n')
                        rows += 1
            raw[name] = {'file': path.name, 'sha256': sha(path), 'rows': rows,
                         'columns': columns, 'sql_schema': schema, **item}
    parsed = []
    with duckdb.connect(':memory:') as c:
        for row in request['compiled_sql']:
            parsed.append({k: v for k, v in row.items() if k != 'sql'} | {
                'compiled_sha256': hashlib.sha256(row['sql'].encode()).hexdigest(),
                'ast': json.loads(c.execute('SELECT json_serialize_sql(?)', [row['sql']]).fetchone()[0])})
    after = {name: sha(name) if Path(name).is_file() else None for name in before}
    result = {'schema_version': 1, 'raw': raw, 'parsed': parsed,
              'database_and_wal_unchanged': before == after, 'hashes_before': before,
              'hashes_after': after, 'duckdb_version': duckdb.__version__,
              'fixture_only': request['fixture_only'], 'model_requests': 0,
              'scope': 'Actual pre-native submission DB copied and mounted read-only; candidate-generated target files never read.'}
    (out / 'probe.json').write_text(json.dumps(result, indent=2) + '\n')
    if before != after:
        raise ValueError('Read-only submitted DB/WAL changed')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    answer = main(json.loads(a.request.read_text()), a.output)
    print(json.dumps({k: answer[k] for k in ('database_and_wal_unchanged', 'fixture_only', 'model_requests')}))

"""Locked DuckDB read-only projection of the actual pre-native Spider DB."""
import argparse
import hashlib
import json
from pathlib import Path
from mechanical_observer import encode, observe


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main(request, output):
    import duckdb
    if duckdb.__version__ != '1.2.2' or request['model_requests'] != 0 or output.exists():
        raise ValueError('Expected locked DuckDB, no model requests and a new output directory')
    before = {p: sha(p) if Path(p).is_file() else None
              for db in request['observation']['database_paths'] for p in (db, db + '.wal')}
    observe(request['observation'], 'agent-submission', output)
    inventory = []
    for item in request['observation']['outputs']:
        row = {'database': item['database'], 'relation': item['relation']}
        try:
            with duckdb.connect(item['database'], read_only=True) as connection:
                parts = item['relation'].split('.')
                schema, name = ('main', parts[0]) if len(parts) == 1 else parts
                matches = connection.execute('SELECT table_schema, table_name FROM information_schema.tables '
                    'WHERE lower(table_schema)=lower(?) AND lower(table_name)=lower(?)', [schema, name]).fetchall()
                row.update(exists=bool(matches), matches=matches, query_succeeded=True)
        except Exception as exc:
            row.update(exists=None, query_succeeded=False, error=str(exc))
        inventory.append(row)
    raw = []
    for index, item in enumerate(request['raw_queries']):
        with duckdb.connect(item['database'], read_only=True) as connection:
            statements = connection.extract_statements(item['query'])
            if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
                raise ValueError('Declared original projection must be one SELECT')
            schema = connection.execute('DESCRIBE ' + item['query']).fetchall()
            cursor = connection.execute(item['query'])
            columns = [x[0] for x in cursor.description]
            path = output / f'raw-{index:03d}.jsonl'
            count = 0
            with path.open('x') as stream:
                while rows := cursor.fetchmany(1000):
                    for row in rows:
                        stream.write(json.dumps([encode(v) for v in row]) + '\n')
                        count += 1
            raw.append({**item, 'columns': columns, 'sql_types': {x[0]: x[1] for x in schema},
                        'file': path.name, 'sha256': sha(path), 'row_count': count})
    after = {p: sha(p) if Path(p).is_file() else None for p in before}
    value = {'raw': raw, 'relation_inventory': inventory, 'hashes_before': before, 'hashes_after': after,
             'database_and_wal_unchanged': before == after, 'duckdb_version': duckdb.__version__,
             'fixture_only': request['fixture_only'], 'model_requests': 0}
    (output / 'probe.json').write_text(json.dumps(value, indent=2) + '\n')
    if before != after:
        raise ValueError('Database/WAL changed during read-only observation')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(main(json.loads(args.request.read_text()), args.output)['database_and_wal_unchanged'])

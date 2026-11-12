"""Parse already captured compiled SQL in the locked DuckDB image, read-only."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    import duckdb
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError('Never replace a parser run')
    request = json.loads(a.input.read_text())
    if request['model_requests'] != 0 or duckdb.__version__ != '1.2.2':
        raise ValueError('Require locked DuckDB1.2.2 and no model requests')
    records = []
    with duckdb.connect(':memory:') as c:
        for row in request['records']:
            mf = a.root / row['manifest']['path']
            if digest(mf) != row['manifest']['sha256']:
                raise ValueError('Captured manifest changed')
            node = json.loads(mf.read_text())['nodes'][row['unique_id']]
            if node['compiled_code'] != row['compiled_sql']:
                raise ValueError('Parser input differs from actual compiled code')
            parsed = json.loads(c.execute('SELECT json_serialize_sql(?)', [row['compiled_sql']]).fetchone()[0])
            if parsed.get('error'):
                raise ValueError('Actual compiled SQL failed parsing: ' + str(parsed))
            records.append({k: v for k, v in row.items() if k != 'compiled_sql'} | {
                'compiled_sha256': hashlib.sha256(row['compiled_sql'].encode()).hexdigest(), 'ast': parsed})
    a.output.write_text(json.dumps({'records': records, 'duckdb_version': duckdb.__version__,
        'input': {'path': str(a.input), 'sha256': digest(a.input)},
        'parser': {'path': str(Path(__file__).resolve()), 'sha256': digest(__file__)},
        'model_requests': 0, 'task_database_opened': False}, indent=2) + '\n')
    print(json.dumps({'records': len(records), 'output': str(a.output), 'sha256': digest(a.output)}))


if __name__ == '__main__':
    main()

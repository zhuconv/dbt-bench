#!/usr/bin/env python3
"""Read only declared outputs and execution context inside validation containers."""
import argparse
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import fnmatch
import json
from pathlib import Path
import sys


def encode(value):
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, Decimal):
        return {'$type': 'decimal', 'value': str(value)}
    if isinstance(value, (date, datetime, time)):
        return {'$type': type(value).__name__, 'value': value.isoformat()}
    if type(value) is float:
        return {'$type': 'float', 'value': repr(value)}
    if isinstance(value, bytes):
        return {'$type': 'bytes', 'value': value.hex()}
    if isinstance(value, (list, tuple)):
        return {'$type': 'list', 'value': [encode(v) for v in value]}
    if isinstance(value, dict):
        return {'$type': 'dict', 'value': [[encode(k), encode(v)] for k, v in value.items()]}
    raise TypeError('Undeclared serialization type: ' + type(value).__name__)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def observe(spec, stage, out):
    from context_probe import probe
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    report = {'stage': stage, 'task_key': spec['task_key'], 'started_at_utc': started,
              'context': probe(spec['database_paths']),
              'outputs': [], 'files': [], 'scope': 'Declared outputs only; no database mutation or full /app export.'}
    if stage == 'initial':
        report['hidden_relation_observations'] = []
        for database in spec['database_paths']:
            observation = {'database': database, 'declared_patterns': spec.get('hidden_relations', []), 'matches': []}
            try:
                with open(database, 'rb') as stream:
                    observation['initial_database_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
                import duckdb
                with duckdb.connect(database, read_only=True) as con:
                    relations = con.execute('SELECT table_schema,table_name,table_type FROM information_schema.tables').fetchall()
                for pattern in spec.get('hidden_relations', []):
                    if isinstance(pattern, str):
                        observation['matches'].extend({'pattern': pattern, 'schema': s, 'table': t, 'type': kind}
                            for s, t, kind in relations if fnmatch.fnmatch(t, pattern) or fnmatch.fnmatch(s + '.' + t, pattern))
            except Exception as exc:
                observation['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            report['hidden_relation_observations'].append(observation)
    for index, item in enumerate(spec['outputs']):
        row = {'specification': item}
        try:
            import duckdb
            with duckdb.connect(item['database'], read_only=True) as con:
                relation = '.'.join(quote(p) for p in item['relation'].split('.'))
                columns = item.get('columns')
                selected = ','.join(quote(c) for c in columns) if columns else '*'
                query = f'SELECT {selected} FROM {relation}'
                if spec.get('observation_sql_types'):
                    # DB-API description can return only NUMBER/STRING. Keep
                    # exact SQL metadata separately; do not coerce row values.
                    # A declared projection may reorder or omit physical
                    # columns. Observe the relation's actual ordinal schema
                    # independently, without reading any extra row values.
                    row['relation_sql_schema'] = [dict(zip(('name', 'type', 'nullable', 'key', 'default', 'extra'), x))
                                                  for x in con.execute('DESCRIBE SELECT * FROM ' + relation).fetchall()]
                    row['sql_schema'] = [dict(zip(('name', 'type', 'nullable', 'key', 'default', 'extra'), x))
                                         for x in con.execute('DESCRIBE ' + query).fetchall()]
                    parts = item['relation'].split('.')
                    catalog, schema_name = con.execute('SELECT current_database(), current_schema()').fetchone()
                    if len(parts) == 3:
                        catalog, schema_name, table_name = parts
                    elif len(parts) == 2:
                        schema_name, table_name = parts
                    else:
                        assert len(parts) == 1
                        table_name = parts[0]
                    row['relation_metadata'] = [dict(zip(('catalog', 'schema', 'name', 'table_type'), x))
                        for x in con.execute('SELECT table_catalog, table_schema, table_name, table_type '
                            'FROM information_schema.tables WHERE lower(table_catalog)=lower(?) '
                            'AND lower(table_schema)=lower(?) AND lower(table_name)=lower(?)',
                            [catalog, schema_name, table_name]).fetchall()]
                    assert len(row['relation_metadata']) == 1, 'Declared relation metadata is not uniquely located'
                cursor = con.execute(query)
                schema = [{'name': col[0], 'type': str(col[1])} for col in cursor.description]
                path = out / f'output-{index:03d}.jsonl'
                count = 0
                h = hashlib.sha256()
                with path.open('w') as stream:
                    while True:
                        rows = cursor.fetchmany(1000)
                        if not rows:
                            break
                        for values in rows:
                            line = json.dumps([encode(v) for v in values], ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
                            stream.write(line)
                            h.update(line.encode())
                            count += 1
                row.update(query=query, schema=schema, rows=count, file=path.name, sha256=h.hexdigest())
        except Exception as exc:
            row['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        report['outputs'].append(row)
    for filename in spec.get('files', []):
        path = Path(filename)
        item = {'path': filename, 'exists': path.is_file()}
        if path.is_file():
            data = path.read_bytes()
            item.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
            if len(data) <= 1024 * 1024:
                try:
                    item['text'] = data.decode('utf-8')
                except UnicodeDecodeError:
                    item['capture'] = 'binary file hash only'
        report['files'].append(item)
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    (out / 'snapshot.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps({'stage': stage, 'output_count': len(report['outputs']),
                      'errors': sum('error' in r for r in report['outputs'])}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--stage', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    observe(json.loads(a.spec.read_text()), a.stage, a.output)

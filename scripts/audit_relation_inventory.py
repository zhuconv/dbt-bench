"""Read-only existence evidence for explicitly declared relations.

An absent relation is distinct from an unreadable database or failed capture.
No inference from exception text, no output-table guessing.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def hashes(database):
    result = {}
    for path in (Path(database), Path(database + '.wal')):
        if path.is_file():
            with path.open('rb') as stream:
                result[str(path)] = hashlib.file_digest(stream, 'sha256').hexdigest()
        else:
            result[str(path)] = None
    return result


def observe(spec, stage):
    result = {'schema_version': 1, 'task_key': spec['task_key'], 'stage': stage,
              'started_at_utc': datetime.now(timezone.utc).isoformat(), 'relations': []}
    import duckdb
    for output in spec['outputs']:
        database, relation = output['database'], output['relation']
        item = {'database': database, 'relation': relation, 'exists': None}
        try:
            before = hashes(database)
            with duckdb.connect(database, read_only=True) as con:
                catalog, schema = con.execute('SELECT current_database(), current_schema()').fetchone()
                parts = relation.split('.')
                if len(parts) == 3:
                    catalog, schema, table = parts
                elif len(parts) == 2:
                    schema, table = parts
                elif len(parts) == 1:
                    table = parts[0]
                else:
                    raise ValueError('Undeclared relation syntax')
                matches = con.execute('SELECT table_catalog,table_schema,table_name,table_type FROM information_schema.tables '
                    'WHERE lower(table_catalog)=lower(?) AND lower(table_schema)=lower(?) AND lower(table_name)=lower(?)',
                    [catalog, schema, table]).fetchall()
            after = hashes(database)
            item.update(database_hashes_before=before, database_hashes_after=after,
                        input_unchanged=before == after, matches=matches,
                        duckdb_version=duckdb.__version__)
            if before != after:
                raise ValueError('Database changed during relation inventory')
            if len(matches) > 1:
                raise ValueError('Relation lookup is ambiguous')
            item['exists'] = bool(matches)
        except Exception as exc:
            item['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['relations'].append(item)
    result['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True); p.add_argument('--stage', required=True); p.add_argument('--output', required=True)
    args = p.parse_args(); out = Path(args.output)
    if out.exists(): raise ValueError('Refuse to overwrite relation evidence')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(observe(json.loads(Path(args.spec).read_text()), args.stage), indent=2)+'\n')

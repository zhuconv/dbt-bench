"""Read initial relation metadata only; never dump rows or modify databases."""
import hashlib
import json
from pathlib import Path
import sys
import duckdb


def checksum(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


spec = json.loads(Path(sys.argv[1]).read_text())
records = []
for filename in spec['database_paths']:
    p = Path(filename)
    item = {'database': filename, 'exists': p.is_file(), 'relations': [], 'views': []}
    if p.is_file():
        item['before_sha256'] = checksum(p)
        try:
            with duckdb.connect(filename, read_only=True) as con:
                tables = con.execute('SELECT table_catalog,table_schema,table_name,table_type FROM information_schema.tables ORDER BY 1,2,3').fetchall()
                columns = con.execute('SELECT table_catalog,table_schema,table_name,column_name,data_type,ordinal_position FROM information_schema.columns ORDER BY 1,2,3,6').fetchall()
                for catalog, schema, name, kind in tables:
                    item['relations'].append({'catalog': catalog, 'schema': schema, 'name': name, 'type': kind,
                        'columns': [{'name': col, 'type': typ, 'ordinal': ordinal}
                                    for c, s, t, col, typ, ordinal in columns if (c, s, t) == (catalog, schema, name)]})
                item['views'] = [{'schema': s, 'name': n, 'sql': sql} for s, n, sql in
                    con.execute('SELECT schema_name,view_name,sql FROM duckdb_views() WHERE NOT internal ORDER BY 1,2').fetchall()]
        except Exception as exc:
            item['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        item['after_sha256'] = checksum(p)
        item['unchanged'] = item['before_sha256'] == item['after_sha256']
    records.append(item)
out = {'duckdb': duckdb.__version__, 'databases': records, 'model_requests': 0,
       'hidden_material_uploaded': False, 'scope': 'Read-only relation/column metadata and view definitions; no table rows exported.'}
Path('/logs/agent/initial-databases.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({'databases': len(records), 'errors': sum('error' in r for r in records)}))

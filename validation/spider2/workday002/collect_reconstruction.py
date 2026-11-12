"""Read only the declared Workday input tables and output in a diagnostic copy."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import duckdb
from reconstruction import INPUT_TABLES, encoded, sha, source_manifest


def collect(phase, database):
    out = Path('/logs/agent/workday-rebuild') / phase
    out.mkdir(parents=True, exist_ok=False)
    database = Path(database)
    before = sha(database)
    with duckdb.connect(str(database), read_only=True) as con:
        def table(name):
            cur = con.execute('select * from main."' + name + '"')
            columns = [{'name': col[0].lower(), 'type': str(col[1])} for col in cur.description]
            names = [col['name'] for col in columns]
            rows = [dict(zip(names, row)) for row in cur.fetchall()]
            return {'columns': columns, 'rows': sorted(rows, key=encoded)}
        inputs = {name: table(name) for name in INPUT_TABLES}
        relations = con.execute('select table_schema, table_name, table_type from information_schema.tables order by 1,2').fetchall()
        present = any(schema == 'main' and name == 'workday__job_overview' for schema, name, _ in relations)
        output = table('workday__job_overview') if present else None
    assert sha(database) == before, 'Read-only collector changed the database'
    for name, value in [('inputs.json', inputs), ('output.json', output), ('relations.json', relations)]:
        (out / name).write_text(encoded(value) + '\n')
    project = Path('/workspace')
    for name in ['manifest.json', 'run_results.json']:
        path = project / 'target' / name
        if path.exists() and phase != 'captured-submission':
            shutil.copyfile(path, out / name)
    snapshot = {'phase': phase, 'database': str(database), 'database_sha256': before,
                'read_only': True, 'python': sys.executable, 'duckdb': duckdb.__version__,
                'source_inventory': source_manifest(project),
                'hidden_paths_present': [str(p) for p in [Path('/tests'), Path('/solution')] if p.exists()],
                'files': {p.name: sha(p) for p in out.iterdir() if p.is_file()}}
    (out / 'snapshot.json').write_text(encoded(snapshot) + '\n')
    print(encoded({'phase': phase, 'input_tables': len(inputs), 'output_present': present,
                   'sources': len(snapshot['source_inventory']), 'hidden_paths_present': snapshot['hidden_paths_present']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['initial', 'before-build', 'after-build', 'captured-submission'])
    parser.add_argument('--database', default='/workspace/workday.duckdb')
    args = parser.parse_args()
    collect(args.phase, args.database)

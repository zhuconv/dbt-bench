"""Run inside a pinned DuckDB container; synthetic fixture, not a benchmark."""
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile

import duckdb
from mechanical_observer import observe


def main(output):
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='observer-fixture-') as directory:
        root = Path(directory)
        database = root / 'fixture.duckdb'
        model = root / 'model.sql'
        model.write_text('select id, money, flag, day from fixture_input\n')
        with duckdb.connect(str(database)) as con:
            con.execute('CREATE TABLE t(id BIGINT, money DECIMAL(18,2), flag BOOLEAN, day DATE)')
            con.execute("INSERT INTO t VALUES (?, ?, true, DATE '2026-11-06')", [9007199254740993, Decimal('123.45')])
        spec = {'task_key': 'synthetic-observer', 'database_paths': [str(database)],
                'files': [str(model)], 'observation_sql_types': True,
                'outputs': [{'database': str(database), 'relation': 'main.t',
                             'columns': ['id', 'money', 'flag', 'day']}]}
        observe(spec, 'fixture-before', output / 'before')
        before = json.loads((output / 'before/snapshot.json').read_text())
        table = before['outputs'][0]
        assert 'error' not in table, table
        assert {c['name']: c['type'] for c in table['sql_schema']} == {
            'id': 'BIGINT', 'money': 'DECIMAL(18,2)', 'flag': 'BOOLEAN', 'day': 'DATE'}
        assert table['relation_metadata'][0]['table_type'] == 'BASE TABLE'
        assert [c['name'] for c in table['relation_sql_schema']] == ['id', 'money', 'flag', 'day']
        values = json.loads((output / 'before/output-000.jsonl').read_text())
        assert values == [9007199254740993, {'$type': 'decimal', 'value': '123.45'}, True,
                          {'$type': 'date', 'value': '2026-11-06'}]
        assert before['files'][0]['exists'] is True and before['files'][0]['text'] == model.read_text()
        with duckdb.connect(str(database)) as con:
            con.execute('ALTER TABLE t RENAME TO backing')
            con.execute("CREATE VIEW t AS SELECT day, id, money, flag, 'extra' AS unexpected FROM backing")
        model.unlink()
        observe(spec, 'fixture-after', output / 'after')
        after = json.loads((output / 'after/snapshot.json').read_text())
        assert after['outputs'][0]['relation_metadata'][0]['table_type'] == 'VIEW'
        assert after['outputs'][0]['sql_schema'] == table['sql_schema']
        assert [c['name'] for c in after['outputs'][0]['relation_sql_schema']] == ['day', 'id', 'money', 'flag', 'unexpected']
        assert after['outputs'][0]['relation_sql_schema'] != table['relation_sql_schema']
        assert after['files'][0]['exists'] is False
        assert (output / 'after/output-000.jsonl').read_bytes() == (output / 'before/output-000.jsonl').read_bytes()
    summary = {'passed': True, 'duckdb': duckdb.__version__, 'model_requests': 0,
               'scope': 'Synthetic exact type/value, TABLE-to-VIEW, removed file, physical column reorder/extra column hidden by an unchanged projection; not benchmark evidence.'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main(Path(sys.argv[1]))

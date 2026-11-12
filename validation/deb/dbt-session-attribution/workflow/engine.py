"""Execute a named workflow case before the unchanged native verifier."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import duckdb
from independent import SESSION_COLUMNS, SALES_COLUMNS, encoded, canonical, calculate
from common import PLAN, assess

PROJECT = Path(os.environ['DBT_PROJECT_DIR_DUCKDB'])
DB = os.environ['DUCKDB_PATH']
MODEL = PROJECT / 'models/marts/attribution_report.sql'
LOGS = Path('/logs/agent')


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded(value) + '\n')


def inputs():
    data = {}
    with duckdb.connect(DB, read_only=True) as con:
        for key, table, cols in [('sessions', 'stg_digital__web_sessions', SESSION_COLUMNS),
                                 ('sales', 'stg_analytics__fact_sales', SALES_COLUMNS)]:
            data[key] = [dict(zip(cols, r)) for r in con.execute('select ' + ','.join(cols) + ' from main.' + table).fetchall()]
    return canonical(json.loads(encoded(data)))


def snapshot(label):
    out = LOGS / label
    out.mkdir(exist_ok=False)
    manifest = json.loads((PROJECT / 'target/manifest.json').read_text())
    nodes = [n for n in manifest['nodes'].values() if n['resource_type'] == 'model' and n['name'] == 'attribution_report']
    assert len(nodes) == 1
    compiled = nodes[0]['compiled_code']
    (out / 'last-successful-compiled.sql').write_text(compiled)
    for name in ['models/marts/attribution_report.sql', 'models/staging/stg_validation_extra.sql',
                 'dbt_project.yml', 'profiles.yml', 'target/run_results.json', 'target/manifest.json']:
        source = PROJECT / name
        if source.exists():
            dest = out / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
    with duckdb.connect(DB, read_only=True) as con:
        def relation(schema, name):
            found = con.execute('select table_type from information_schema.tables where table_schema=? and table_name=?', [schema, name]).fetchall()
            if not found: return None
            assert len(found) == 1
            cursor = con.execute('select * from "' + schema + '"."' + name + '"')
            cols = [c[0].lower() for c in cursor.description]
            return {'columns': cols, 'rows': [dict(zip(cols, r)) for r in cursor.fetchall()], 'relation_type': found[0][0]}
        required = relation('main', 'attribution_report')
        wrong = relation('main_case_wrong', 'attribution_report')
        extra = relation('main', 'stg_validation_extra')
        cursor = con.execute(compiled)
        cols = [c[0].lower() for c in cursor.description]
        query = [dict(zip(cols, r)) for r in cursor.fetchall()]
    result = {'required_output': required, 'wrong_schema_output': wrong,
              'extra_relation_type': extra['relation_type'] if extra else None,
              'model_present': MODEL.exists(), 'model_sha256': ref(MODEL)['sha256'] if MODEL.exists() else None,
              'staging_sources': {str(p.relative_to(PROJECT)): ref(p)['sha256'] for p in sorted((PROJECT / 'models/staging').rglob('*')) if p.is_file()},
              'query_channels': [r['channel'] for r in query], 'compiled_query_rows': query,
              'compiled_query_scope': 'Last successful build; retained artifacts can outlive a removed source file.',
              'files': [ref(p) for p in sorted(out.rglob('*')) if p.is_file()]}
    save(out / 'snapshot.json', result)
    return json.loads((out / 'snapshot.json').read_text())


def build(label, extra=False):
    command = ['dbt', 'run', '--no-partial-parse', '--profiles-dir', str(PROJECT), '--select', 'attribution_report']
    if extra: command.append('stg_validation_extra')
    run = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True)
    save(LOGS / (label + '-command.json'), {'command': command, 'cwd': str(PROJECT), 'returncode': run.returncode,
                                          'stdout': run.stdout, 'stderr': run.stderr})
    assert run.returncode == 0, 'Case build failed before independent proof: ' + label


def main(mode):
    config = json.loads(Path(__file__).with_name('case.json').read_text())
    sql = MODEL.read_text()
    assert ref(MODEL)['sha256'] == config['reference_model_sha256']
    data = inputs()
    save(LOGS / 'evaluated-inputs.json', data)
    assert ref(LOGS / 'evaluated-inputs.json')['sha256'] == config['pristine_inputs_sha256']
    expected = calculate(data)
    assert not expected['unresolved']
    save(LOGS / 'independent-expectation.json', expected)
    before = snapshot('before-case')
    phases = []
    if mode in ['idempotent-control', 'append-on-repeat']:
        if mode == 'append-on-repeat':
            assert sql.count("{{ config(materialized='table') }}") == 1
            MODEL.write_text(sql.replace("{{ config(materialized='table') }}", "{{ config(materialized='incremental', incremental_strategy='append') }}"))
            with duckdb.connect(DB) as con: con.execute('drop table main.attribution_report')
        for label in ['first-run', 'second-run']:
            build(label)
            phases.append(snapshot(label))
    elif mode == 'missing-model':
        MODEL.unlink()
        phases.append(snapshot('after-case'))
    else:
        if mode == 'view-output':
            assert sql.count("{{ config(materialized='table') }}") == 1
            MODEL.write_text(sql.replace("{{ config(materialized='table') }}", "{{ config(materialized='view') }}"))
        elif mode == 'new-staging':
            extra = PROJECT / 'models/staging/stg_validation_extra.sql'
            assert not extra.exists()
            extra.write_text("{{ config(materialized='view') }}\nselect 1 as unneeded\n")
        elif mode == 'descending-query':
            assert sql.count('order by channel') == 1
            MODEL.write_text(sql.replace('order by channel', 'order by channel desc'))
        elif mode == 'wrong-schema':
            assert sql.count("{{ config(materialized='table') }}") == 1
            MODEL.write_text(sql.replace("{{ config(materialized='table') }}", "{{ config(materialized='table', schema='case_wrong') }}"))
            with duckdb.connect(DB) as con: con.execute('drop table main.attribution_report')
        else: raise ValueError(mode)
        build('case-build', extra=mode == 'new-staging')
        phases.append(snapshot('after-case'))
    assert inputs() == data
    facts = assess(mode, before, phases, expected['expected'])
    kind, requirements = PLAN[mode]
    proof = {'schema_version': 1, 'case': mode, 'validity': 'built_from_inputs' if kind == 'witness' else 'business_error',
             'requirement_ids': requirements, 'data_scope': 'original_pinned_inputs',
             'tested_checker_invoked_to_define_validity': False, 'derivation': facts['derivation'],
             'facts': facts, 'phases': ['first-run', 'second-run'] if len(phases) == 2 else ['after-case'],
             'input_artifacts': [ref(LOGS / 'evaluated-inputs.json'), ref(LOGS / 'independent-expectation.json')],
             'submission_artifacts': [ref(p / 'snapshot.json') for p in [LOGS / 'before-case'] + [LOGS / n for n in (['first-run', 'second-run'] if len(phases) == 2 else ['after-case'])]]}
    save(Path(os.environ['DBT_BENCH_PROOF']), proof)
    print(encoded({'mode': mode, 'validity': proof['validity'], 'facts': facts}))


if __name__ == '__main__': main(sys.argv[1])

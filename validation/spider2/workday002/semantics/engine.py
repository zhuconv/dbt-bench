"""Run one declared Workday dbt case, proving its meaning before native grading."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import duckdb
from fixtures import construct
from independent import AGGREGATES, INPUTS, assess, encoded, recompute
from reconstruction import INPUT_TABLES, exact_table_equal, verify_full_run

PAYLOAD = Path(__file__).resolve().parent
OUT = Path('/logs/agent')
PROJECT = Path('/workspace')
DB = PROJECT / 'workday.duckdb'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(name, data):
    (OUT / name).write_text(encoded(data) + '\n')


def table(connection, name):
    cur = connection.execute('select * from main."' + name + '"')
    columns = [{'name': col[0].lower(), 'type': str(col[1])} for col in cur.description]
    rows = [dict(zip([col['name'] for col in columns], row)) for row in cur.fetchall()]
    return json.loads(encoded({'columns': columns, 'rows': sorted(rows, key=encoded)}))


def inputs():
    with duckdb.connect(str(DB), read_only=True) as con:
        return {name: table(con, name) for name in INPUT_TABLES}


def run(argv, logfile):
    with (OUT / logfile).open('w') as stream:
        subprocess.run(argv, cwd=PROJECT, stdout=stream, stderr=subprocess.STDOUT, check=True)


def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Mutation context differs: ' + old)
    return text.replace(old, new)


def main():
    case = json.loads((PAYLOAD / 'case.json').read_text())
    mode = case['mode']
    if Path('/tests').exists() or any(Path('/solution').glob('*.duckdb')):
        raise ValueError('Hidden material present before case build')
    pristine = inputs()
    if hashlib.sha256((encoded(pristine) + '\n').encode()).hexdigest() != case['pristine_inputs_sha256']:
        raise ValueError('Initial raw input identity changed')
    write('pristine-inputs.json', pristine)
    current = pristine
    if mode.startswith('fixture-'):
        current, manual = construct(pristine)
        expected = recompute(current)
        calculated = {r['job_profile_id']: {key: r[key] for key in AGGREGATES} for r in expected}
        if calculated != manual:
            raise ValueError('Independent relation derivation contradicts hand-written expectations')
        write('manual-expectations.json', manual)
        with duckdb.connect(str(DB)) as con:
            for name in INPUTS:
                columns = [c['name'] for c in current[name]['columns']]
                con.execute('delete from main."' + name + '"')
                query = ('insert into main."' + name + '" (' + ','.join('"' + c + '"' for c in columns)
                         + ') values (' + ','.join('?' for _ in columns) + ')')
                con.executemany(query, [[r[col] for col in columns] for r in current[name]['rows']])
        observed = inputs()
        if not all(exact_table_equal(observed[name], current[name]) for name in INPUT_TABLES):
            raise ValueError('Fixture installation differs from declared rows or changes unrelated inputs')
        current = observed
    expected = recompute(current)
    write('inputs.json', current)
    write('independent-expected.json', expected)

    # Dependencies must actually be installed by dbt, as required by the task.
    # Use exact captured versions; reject any source-byte or lockfile drift.
    for name in ['target', 'logs', 'dbt_packages']:
        path = PROJECT / name
        if path.exists():
            shutil.rmtree(path)
    for name in ['packages.yml', 'package-lock.yml']:
        shutil.copyfile(PAYLOAD / name, PROJECT / name)
    run(['dbt', 'deps', '--profiles-dir', '/workspace'], 'dbt-deps.log')
    inventory = {p.relative_to(PROJECT).as_posix(): sha(p) for p in (PROJECT / 'dbt_packages').rglob('*') if p.is_file()}
    pinned = json.loads((PAYLOAD / 'dependency-inventory.json').read_text())
    write('dependency-inventory.json', inventory)
    if inventory != pinned or sha(PROJECT / 'package-lock.yml') != sha(PAYLOAD / 'package-lock.yml'):
        raise ValueError('Installed dependency source bytes or resolved lock differ from captured pin')

    sql = (PAYLOAD / 'upstream-reference.sql').read_text()
    if mode == 'fixture-no-distinct':
        sql = replace(sql, "string_agg('distinct ", "string_agg('", count=4)
    elif mode == 'fixture-organization-bridge':
        sql = replace(sql, "ref('stg_workday__job_family_job_family_group')", "ref('stg_workday__organization_job_family')")
    elif mode == 'fixture-inner-join':
        sql = replace(sql, 'left join job_family_data\n', 'inner join job_family_data\n')
    elif mode == 'fixture-reordered':
        for field in ['job_family_data.job_family_code', 'job_family_data.job_family_summary',
                      'job_family_group_data.job_family_group_code', 'job_family_group_data.job_family_group_summary']:
            old = "{{ fivetran_utils.string_agg('distinct " + field + "', \"', '\" ) }}"
            sql = replace(sql, old, "string_agg(distinct " + field + ", ', ' order by " + field + ' desc)')
        sql += '\norder by job_profile_id desc\n'
    elif mode == 'fixture-model-submission':
        sql = (PAYLOAD / 'model-submission.sql').read_text()
    if mode in ['fixture-deleted-family', 'fixture-deleted-profile']:
        name = 'job_family' if mode.endswith('family') else 'job_profile'
        path = PROJECT / ('models/staging/stg_workday__' + name + '.sql')
        old = path.read_text()
        updated = replace(old, 'where not coalesce(_fivetran_deleted, false)', 'where true')
        path.write_text(updated)
        write('staging-mutation.json', {'path': str(path), 'before': old, 'after': updated})
    model = PROJECT / 'models/workday__job_overview.sql'
    model.write_text(sql)
    shutil.copyfile(model, OUT / 'submission.sql')
    run(['dbt', 'run', '--no-partial-parse', '--profiles-dir', '/workspace'], 'dbt-run.log')
    run_results = json.loads((PROJECT / 'target/run_results.json').read_text())
    manifest = json.loads((PROJECT / 'target/manifest.json').read_text())
    models = verify_full_run(run_results, manifest)
    if len(models) != 50:
        raise ValueError('Enabled model set differs from the pinned task')
    for name in ['run_results.json', 'manifest.json']:
        shutil.copyfile(PROJECT / 'target' / name, OUT / ('dbt-' + name))
    if mode == 'original-private-title':
        with duckdb.connect(str(DB)) as con:
            con.execute('update main.workday__job_overview set private_title=42')
    after = inputs()
    if not all(exact_table_equal(current[name], after[name]) for name in INPUT_TABLES):
        raise ValueError('Submission changed input data during dbt build')
    with duckdb.connect(str(DB), read_only=True) as con:
        output = table(con, 'workday__job_overview')
    write('output.json', output)
    differences = assess(expected, output)
    write('independent-comparison.json', {'differences': differences, 'accepted': not differences})
    wrong = case['kind'] == 'negative'
    if wrong != bool(differences):
        raise ValueError('Case is not independently established as declared; native grading must not run')

    def ref(name):
        p = OUT / name
        return {'path': str(p), 'sha256': sha(p)}
    write('case-proof.json', {
        'validity': 'business_error' if wrong else 'legal_equivalent' if case['kind'] == 'equivalent' else 'built_from_inputs',
        'derivation': case['independent_derivation'], 'mode': mode, 'data_scope': case['data_scope'],
        'gold_access': False, 'all_models_run': len(models), 'semantic_certification': False,
        'changed_columns': sorted({d['column'] for d in differences if 'column' in d}),
        'differences': differences, 'requirement_ids': case['requirement_ids'],
        'input_artifacts': [ref(name) for name in ['pristine-inputs.json', 'inputs.json', 'independent-expected.json']],
        'submission_artifacts': [ref(name) for name in ['submission.sql', 'output.json', 'dbt-manifest.json',
                                                        'dbt-run_results.json', 'dependency-inventory.json', 'independent-comparison.json']],
    })
    print(encoded({'mode': mode, 'all_models_run': len(models), 'independent_differences': len(differences)}))


if __name__ == '__main__':
    main()

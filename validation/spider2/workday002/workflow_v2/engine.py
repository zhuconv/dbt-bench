"""Execute an isolated, explicitly proved Workday workflow/representation case."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import duckdb
from independent import AGGREGATES, INPUTS, assess, encoded, recompute
from fixtures import construct
from reconstruction import INPUT_TABLES, exact_table_equal
from workflow_proof import EXPECTED_FAILURES, violations

PAYLOAD = Path(__file__).resolve().parent
OUT = Path('/logs/agent')
PROJECT = Path('/workspace')
DB = PROJECT / 'workday.duckdb'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(name, value):
    (OUT / name).write_text(encoded(value) + '\n')


def table(con, name):
    cur = con.execute('select * from main."' + name + '"')
    cols = [{'name': c[0].lower(), 'type': str(c[1])} for c in cur.description]
    rows = [dict(zip([c['name'] for c in cols], row)) for row in cur.fetchall()]
    return json.loads(encoded({'columns': cols, 'rows': sorted(rows, key=encoded)}))


def inputs():
    with duckdb.connect(str(DB), read_only=True) as con:
        return {n: table(con, n) for n in INPUT_TABLES}


def main():
    case = json.loads((PAYLOAD / 'case.json').read_text())
    mode = case['mode']
    if mode not in EXPECTED_FAILURES:
        raise ValueError('Unknown case')
    if Path('/tests').exists() or any(Path('/solution').glob('*.duckdb')):
        raise ValueError('Hidden reference material present')
    pristine = inputs()
    if hashlib.sha256((encoded(pristine) + '\n').encode()).hexdigest() != case['pristine_inputs_sha256']:
        raise ValueError('Pristine input identity differs')
    write('pristine-inputs.json', pristine)
    current = pristine
    if mode.startswith('fixture-'):
        current, manual = construct(pristine)
        derived = {r['job_profile_id']: {k: r[k] for k in AGGREGATES} for r in recompute(current)}
        if derived != manual:
            raise ValueError('Hand-computed fixture expectations disagree')
        write('manual-expectations.json', manual)
        with duckdb.connect(str(DB)) as con:
            for name in INPUTS:
                cols = [c['name'] for c in current[name]['columns']]
                con.execute('delete from main."' + name + '"')
                sql = ('insert into main."' + name + '" (' + ','.join('"' + c + '"' for c in cols)
                       + ') values (' + ','.join('?' for _ in cols) + ')')
                con.executemany(sql, [[r[c] for c in cols] for r in current[name]['rows']])
        observed = inputs()
        if not all(exact_table_equal(observed[k], current[k]) for k in INPUT_TABLES):
            raise ValueError('Fixture installation changed undeclared rows')
        current = observed
    expected = recompute(current)
    write('inputs.json', current)
    write('independent-expected.json', expected)

    # Remove generated evidence so no prior run can satisfy this workflow proof.
    for name in ['target', 'logs']:
        if (PROJECT / name).exists():
            shutil.rmtree(PROJECT / name)
    commands = []

    def run(argv, logfile):
        with (OUT / logfile).open('w') as stream:
            result = subprocess.run(argv, cwd=PROJECT, stdout=stream, stderr=subprocess.STDOUT)
        commands.append({'argv': argv, 'cwd': str(PROJECT), 'returncode': result.returncode,
                         'log': logfile, 'log_sha256': sha(OUT / logfile)})
        write('commands.json', commands)
        result.check_returncode()

    model = PROJECT / 'models/workday__job_overview.sql'
    if model.exists():
        raise ValueError('Target unexpectedly exists in initial project')
    rr = manifest = None
    if mode == 'original-direct-table':
        # Make the conditional dependency requirement applicable before omitting it.
        # The native initial workspace has no packages.yml.
        for name in ['packages.yml', 'package-lock.yml']:
            shutil.copyfile(PAYLOAD / name, PROJECT / name)
        # Deliberately bypass dbt using only raw input-derived values, not gold.
        cols = ['job_profile_id', 'source_relation', 'job_profile_code', 'job_title', 'private_title',
                'job_summary', 'job_description', *AGGREGATES]
        write('submission.sql', {'method': 'parameterized direct table from independently recomputed raw rows',
                                 'columns': cols})
        with duckdb.connect(str(DB)) as con:
            con.execute('create table main.workday__job_overview (' + ','.join('"' + c + '" VARCHAR' for c in cols) + ')')
            rows = [[None if c in AGGREGATES and not r[c] else ', '.join(r[c]) if c in AGGREGATES else r[c]
                     for c in cols] for r in expected]
            con.executemany('insert into main.workday__job_overview values (' + ','.join('?' for _ in cols) + ')', rows)
    else:
        if (PROJECT / 'dbt_packages').exists():
            shutil.rmtree(PROJECT / 'dbt_packages')
        for name in ['packages.yml', 'package-lock.yml']:
            shutil.copyfile(PAYLOAD / name, PROJECT / name)
        run(['dbt', 'deps', '--profiles-dir', '/workspace'], 'dbt-deps.log')
        inventory = {p.relative_to(PROJECT).as_posix(): sha(p)
                     for p in (PROJECT / 'dbt_packages').rglob('*') if p.is_file()}
        if inventory != json.loads((PAYLOAD / 'dependency-inventory.json').read_text()):
            raise ValueError('Installed dependency bytes differ')
        if sha(PROJECT / 'package-lock.yml') != sha(PAYLOAD / 'package-lock.yml'):
            raise ValueError('Dependency lock differs')
        write('dependency-inventory.json', inventory)
        sql = (PAYLOAD / 'upstream-reference.sql').read_text()
        if mode == 'fixture-arrays':
            for field in ['job_family_data.job_family_code', 'job_family_data.job_family_summary',
                          'job_family_group_data.job_family_group_code', 'job_family_group_data.job_family_group_summary']:
                old = "{{ fivetran_utils.string_agg('distinct " + field + "', \"', '\" ) }}"
                if sql.count(old) != 1:
                    raise ValueError('Array mutation context differs')
                new = 'coalesce(list(distinct ' + field + ') filter (where ' + field + ' is not null), []::varchar[])'
                sql = sql.replace(old, new)
        if mode == 'fixture-extra-column':
            old = 'select *\nfrom job_data_enhanced'
            if sql.count(old) != 1:
                raise ValueError('Extra column mutation context differs')
            sql = sql.replace(old, 'select *, NULL::varchar as optional_note\nfrom job_data_enhanced')
            sql += '\norder by job_profile_id desc\n'
        if mode == 'original-view':
            sql = "{{ config(materialized='view') }}\n" + sql
        model.write_text(sql)
        shutil.copyfile(model, OUT / 'submission.sql')
        argv = ['dbt', 'run', '--no-partial-parse', '--profiles-dir', '/workspace']
        if mode == 'original-selected':
            argv += ['--select', '+workday__job_overview']
        run(argv, 'dbt-run.log')
        rr = json.loads((PROJECT / 'target/run_results.json').read_text())
        manifest = json.loads((PROJECT / 'target/manifest.json').read_text())
        for n in ['run_results.json', 'manifest.json']:
            shutil.copyfile(PROJECT / 'target' / n, OUT / ('dbt-' + n))
        if mode == 'original-missing-model':
            model.unlink()
    write('commands.json', commands)
    after = inputs()
    if not all(exact_table_equal(current[k], after[k]) for k in INPUT_TABLES):
        raise ValueError('Case changed raw input during submission')
    with duckdb.connect(str(DB), read_only=True) as con:
        output = table(con, 'workday__job_overview')
        relation = con.execute("select table_type from information_schema.tables where table_schema='main' and table_name='workday__job_overview'").fetchall()
    if len(relation) != 1:
        raise ValueError('Ambiguous output relation')
    write('output.json', output)
    differences = assess(expected, output)
    if differences:
        raise ValueError('Workflow control output must remain semantically correct: ' + encoded(differences))
    state = {'commands': commands, 'packages_present': (PROJECT / 'packages.yml').is_file(),
             'target_model_present': model.is_file(), 'relation_type': relation[0][0],
             'target_model_sha256': sha(model) if model.is_file() else None,
             'output_sha256': sha(OUT / 'output.json')}
    write('workflow-state.json', state)
    failures = violations(state, rr, manifest, case['required_model_ids'])
    if failures != EXPECTED_FAILURES[mode] or bool(failures) != (case['kind'] == 'negative'):
        raise ValueError('Workflow case meaning differs: ' + encoded(failures))
    write('independent-comparison.json', {'semantic_differences': differences, 'workflow_violations': failures})

    def ref(name):
        return {'path': str(OUT / name), 'sha256': sha(OUT / name)}
    artifacts = ['submission.sql', 'output.json', 'commands.json', 'workflow-state.json', 'independent-comparison.json']
    if rr is not None:
        artifacts += ['dbt-manifest.json', 'dbt-run_results.json', 'dependency-inventory.json', 'dbt-run.log', 'dbt-deps.log']
    write('case-proof.json', {
        'validity': 'business_error' if failures else 'legal_equivalent' if case['kind'] == 'equivalent' else 'built_from_inputs',
        'derivation': case['independent_derivation'], 'mode': mode, 'data_scope': case['data_scope'],
        'violation_kind': 'explicit_workflow_requirement' if failures else None,
        'gold_access': False, 'semantic_differences': differences, 'workflow_violations': failures,
        'executed_models': len(rr['results']) if rr is not None else 0,
        'requirement_ids': case['requirement_ids'],
        'input_artifacts': [ref(n) for n in ['pristine-inputs.json', 'inputs.json', 'independent-expected.json']],
        'submission_artifacts': [ref(n) for n in artifacts],
    })
    print(encoded({'mode': mode, 'semantic_differences': differences, 'workflow_violations': failures}))


if __name__ == '__main__':
    main()

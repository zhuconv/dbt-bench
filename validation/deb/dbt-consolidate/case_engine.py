"""Controlled original-data cases; establish wrongness before calling any grader."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb

from independent import SOURCES, TABLES, STAGING_COLUMNS, INT_COLUMNS, derive, read_inputs, compare

PROJECT=Path('/app/dbt_consolidate')
DB=Path('/app/consolidate.duckdb')
LOGS=Path('/logs/agent')
STAGING=PROJECT/'models/staging'
UNIFIED=PROJECT/'models/int/int__ads_unified.sql'

CASES={
    'reference':('witness',['R-values','R-dedup','R-union-values']),
    'reordered':('equivalent',['R-values','R-union-values']),
    'decimal-counts':('equivalent',['R-values','R-count-representation']),
    'omit-meta-views-2':('negative',['R-values']),
    'swap-google-clicks':('negative',['R-row-association']),
    'keep-tiktok-duplicates':('negative',['R-dedup']),
    'drop-google-date':('negative',['R-union-values']),
    'compensated-staging':('negative',['R-staging-values']),
    'missing-staging-yaml':('negative',['R-yaml']),
    'wrong-project-name':('negative',['R-project']),
    'staging-tables':('negative',['R-staging-views']),
    'intermediate-view':('negative',['R-intermediate-table']),
    'wrong-dbt-test':('negative',['R-unique-test']),
    'distinct-dedup':('negative',['R-qualify-row-number']),
    'union-distinct':('negative',['R-union-all']),
}


def ref(path):
    path=Path(path)
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def replace(path,before,after,expected=1):
    text=path.read_text()
    assert text.count(before)==expected,(str(path),before,text.count(before))
    path.write_text(text.replace(before,after))


def snapshot(label):
    out=LOGS/('consolidate-'+label)
    out.mkdir(exist_ok=False)
    connection=duckdb.connect(str(DB),read_only=True)
    tables={}
    for key,table in TABLES.items():
        columns=INT_COLUMNS if key=='unified' else STAGING_COLUMNS
        query=connection.execute('SELECT '+','.join(columns)+' FROM analytics.'+table)
        types={c[0]:str(c[1]) for c in query.description}
        rows=[dict(zip(columns,row)) for row in query.fetchall()]
        table_type=connection.execute('SELECT table_type FROM information_schema.tables WHERE table_schema=? AND table_name=?',['analytics',table]).fetchone()[0]
        tables[key]={'rows':rows,'columns':list(columns),'types':types,'table_type':table_type}
    connection.close()
    # Copy the complete declared source submission; no hidden task assets.
    paths=[PROJECT/name for name in ['dbt_project.yml','profiles.yml','packages.yml','package-lock.yml']]
    for directory in ['models','macros','seeds']:
        paths += [p for p in (PROJECT/directory).rglob('*') if p.is_file()]
    copied={}
    for path in paths:
        if not path.is_file():continue
        relative=path.relative_to(PROJECT)
        target=out/'submission'/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);copied[str(relative)]=ref(target)
    for name in ['manifest.json','run_results.json']:
        path=PROJECT/'target'/name
        if path.is_file():shutil.copyfile(path,out/name)
    package_files=[{'path':str(p.relative_to(PROJECT/'dbt_packages')), 'sha256':ref(p)['sha256']}
                   for p in sorted((PROJECT/'dbt_packages').rglob('*')) if p.is_file()]
    data={'tables':tables,'source_files':copied,'package_files':package_files,
          'python':sys.executable,'duckdb_version':duckdb.__version__}
    path=out/'snapshot.json';path.write_text(json.dumps(data,sort_keys=True,default=str,indent=2)+'\n')
    return data,path


def mutate(mode):
    if mode=='reference':return
    if mode=='reordered':replace(UNIFIED,'select * from unioned','select * from unioned order by ad_date desc, source desc')
    elif mode=='decimal-counts':
        replace(UNIFIED,'select * from unioned',
                'select source, ad_date, '+', '.join('cast('+c+' as decimal(18,2)) as '+c for c in STAGING_COLUMNS[1:])+' from unioned')
    elif mode=='omit-meta-views-2':replace(STAGING/'stg__ads_metaads.sql','views_1 + views_2 as views','views_1 as views')
    elif mode=='swap-google-clicks':
        replace(UNIFIED,'select * from unioned',
            "select source, ad_date, case when source='google' and ad_date=DATE '2025-12-01' then 12 "
            "when source='google' and ad_date=DATE '2025-12-02' then 10 else clicks end as clicks, impressions, views, conversions from unioned")
    elif mode in {'keep-tiktok-duplicates','distinct-dedup'}:
        path=STAGING/'stg__ads_tiktokads.sql';text=path.read_text();assert text.count('select * from source\nqualify')==1
        prefix=text.split('select * from source\nqualify')[0]
        path.write_text(prefix+('select * from source\n' if mode=='keep-tiktok-duplicates' else 'select distinct * from source\n'))
    elif mode=='drop-google-date':replace(UNIFIED,'select * from unioned',"select * from unioned where not(source='google' and ad_date=DATE '2025-12-10')")
    elif mode=='compensated-staging':
        replace(STAGING/'stg__ads_googleads.sql','\t\t, clicks\n','\t\t, clicks + 1 as clicks\n')
        replace(UNIFIED,"select 'google' as source, *","select 'google' as source, ad_date, clicks - 1 as clicks, impressions, views, conversions")
    elif mode=='missing-staging-yaml':(STAGING/'staging.yml').unlink()
    elif mode=='wrong-project-name':
        replace(PROJECT/'dbt_project.yml',"name: 'dbt_consolidate'","name: 'wrong_consolidate'")
        replace(PROJECT/'dbt_project.yml','  dbt_consolidate:\n','  wrong_consolidate:\n')
    elif mode=='staging-tables':replace(PROJECT/'dbt_project.yml','staging:\n      +materialized: view','staging:\n      +materialized: table')
    elif mode=='intermediate-view':replace(PROJECT/'dbt_project.yml','int:\n      +materialized: table','int:\n      +materialized: view')
    elif mode=='wrong-dbt-test':
        (PROJECT/'models/int/int.yml').write_text('version: 2\nmodels:\n  - name: int__ads_unified\n    columns:\n      - name: source\n        tests:\n          - not_null\n')
    elif mode=='union-distinct':replace(UNIFIED,'union all','union',expected=2)
    else:raise ValueError(mode)


def main(mode):
    kind,requirements=CASES[mode]
    inputs=read_inputs('/app/data');expected=derive(inputs)
    assert [len(inputs[s]) for s in SOURCES]==[10,10,12]
    assert [len(expected[s]) for s in SOURCES]==[10,10,10]
    input_refs=[ref(Path('/app/data')/filename) for filename,_ in SOURCES.values()]
    for filename,_ in SOURCES.values():
        assert ref(PROJECT/'seeds'/filename)['sha256']==ref(Path('/app/data')/filename)['sha256']
        shutil.copyfile(Path('/app/data')/filename,LOGS/('public-'+filename))
    before,before_path=snapshot('before')
    before_comparisons={k:compare(expected[k],before['tables'][k]['rows'],k=='unified') for k in TABLES}
    assert all(c['equal'] for c in before_comparisons.values()),before_comparisons
    mutate(mode)
    if mode!='reference':
        command=['dbt','run','--select',*TABLES.values(),'--project-dir',str(PROJECT),'--profiles-dir',str(PROJECT)]
        executed=subprocess.run(command,cwd=PROJECT,text=True,capture_output=True)
        (LOGS/'case-dbt-run.log').write_text(executed.stdout+executed.stderr)
        assert executed.returncode==0,'Mutation did not execute; it is not a semantic false-acceptance case'
    after,after_path=snapshot('after')
    comparisons={k:compare(expected[k],after['tables'][k]['rows'],k=='unified') for k in TABLES}
    changed={k for k,c in comparisons.items() if not c['equal']}
    facts={'comparisons':comparisons,'changed_outputs':sorted(changed),'input_rows':{s:len(inputs[s]) for s in SOURCES}}
    if kind in {'witness','equivalent'}:
        assert not changed
        derivation='Independent CSV parsing, per-platform views mapping, full-normalized-row deduplication and tagged union reproduce all four outputs exactly as integer-valued numbers; SQL row order and decimal scale do not change these values.'
        violation_kind=None
    elif mode=='omit-meta-views-2':
        assert changed=={'meta','unified'}
        derivation='Meta views must sum both input views fields; independent CSV sums disagree with all 10 submitted Meta daily values.';violation_kind='data_semantics'
    elif mode=='swap-google-clicks':
        assert changed=={'unified'}
        assert len(comparisons['unified']['missing'])==2
        for col in INT_COLUMNS:
            assert Counter(str(r[col]) for r in before['tables']['unified']['rows'])==Counter(str(r[col]) for r in after['tables']['unified']['rows'])
        derivation='Two Google dates now carry each other\'s clicks. Every column multiset is unchanged, but the independent date/platform-to-click association differs.';violation_kind='data_semantics'
    elif mode=='keep-tiktok-duplicates':
        assert changed=={'tiktok','unified'} and len(after['tables']['unified']['rows'])==32
        derivation='Two exact TikTok duplicates from the CSV remain; expected full-row deduplication gives 10 TikTok rows and 30 unified rows, while the successfully built submission has 12 and 32.';violation_kind='data_semantics'
    elif mode=='drop-google-date':
        assert changed=={'unified'} and len(after['tables']['unified']['rows'])==29
        derivation='The required Google 2025-12-10 row is absent although it is present once in the input and staging model.';violation_kind='data_semantics'
    elif mode=='compensated-staging':
        assert changed=={'google'} and comparisons['unified']['equal']
        derivation='All 10 Google staging click values are one too high versus the public CSV; subtracting one again in the intermediate model hides that staging error from final-table-only comparison.';violation_kind='data_semantics'
    else:
        assert not changed
        violation_kind='explicit_workflow_requirement'
        if mode=='missing-staging-yaml':
            assert not (STAGING/'staging.yml').exists();derivation='The instruction explicitly requires staging.yml, which is absent although SQL execution and all data values remain correct.'
        elif mode=='wrong-project-name':
            assert "name: 'wrong_consolidate'" in (PROJECT/'dbt_project.yml').read_text();derivation='The project name is wrong_consolidate instead of the explicitly required dbt_consolidate; all materializations and data values remain correct.'
        elif mode=='staging-tables':
            assert all(after['tables'][s]['table_type']=='BASE TABLE' for s in SOURCES);derivation='All three required staging views are instead base tables, established through information_schema; their values remain correct.'
        elif mode=='intermediate-view':
            assert after['tables']['unified']['table_type']=='VIEW';derivation='The required intermediate table is a view according to information_schema; all data values remain correct.'
        elif mode=='wrong-dbt-test':
            manifest=json.loads((PROJECT/'target/manifest.json').read_text())
            tests=[n.get('test_metadata') for n in manifest['nodes'].values() if n['resource_type']=='test']
            assert tests and all(t['name']!='unique_combination_of_columns' for t in tests)
            facts['declared_tests']=tests
            derivation='The required dbt_utils unique_combination_of_columns test is removed and replaced with source not_null; manifest metadata independently identifies the different test.'
        elif mode=='distinct-dedup':
            text=(STAGING/'stg__ads_tiktokads.sql').read_text().lower();assert 'qualify' not in text and 'row_number' not in text and 'select distinct' in text
            derivation='This controlled replacement uses DISTINCT and contains no QUALIFY or ROW_NUMBER, contrary to the expressly required deduplication workflow; outputs remain equivalent.'
        elif mode=='union-distinct':
            text=UNIFIED.read_text().lower();assert text.count('union\n')==2 and 'union all' not in text
            derivation='The two controlled UNION ALL operators are replaced by UNION DISTINCT semantics; platform tags make the original-data values equal, but the expressly requested UNION ALL workflow is absent.'
        else:raise AssertionError(mode)
    for item in input_refs:assert ref(item['path'])['sha256']==item['sha256']
    proof={'validity':'built_from_inputs' if kind=='witness' else 'legal_equivalent' if kind=='equivalent' else 'business_error',
           'requirement_ids':requirements,'derivation':derivation,'violation_kind':violation_kind,'case':mode,
           'facts':facts,'tested_checker_invoked_to_define_validity':False,
           'input_artifacts':input_refs,'submission_artifacts':[ref(DB),ref(before_path),ref(after_path)],
           'data_scope':'Original full three public CSVs; native private seeds are independently hash-matched in the task review'}
    Path(os.environ['DBT_BENCH_PROOF']).write_text(json.dumps(proof,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'case':mode,'validity':proof['validity'],'changed_outputs':sorted(changed)}))


if __name__=='__main__':main(sys.argv[1])

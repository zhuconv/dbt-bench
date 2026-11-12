"""Derive a customer dimension from public source rows before native grading."""
from collections import Counter
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys

MODES={'reference':('witness',['R-rename','R-unique','R-values']),
    'duplicate-source':('negative',['R-unique']), 'duplicate-output':('negative',['R-unique']),
    'missing-customer':('negative',['R-complete']), 'swapped-keys':('negative',['R-values']),
    'wrong-name':('negative',['R-values']), 'no-rename':('negative',['R-rename']),
    'reordered':('equivalent',['R-order']), 'distinct':('equivalent',['R-equivalent-sql']),
    'null-key':('negative',['R-unique']), 'table-only':('negative',['R-model']),
    'source-only':('equivalent',['R-model']), 'view':('equivalent',['R-model']),
    'alternate-path':('equivalent',['R-model']), 'extra-column':('equivalent',['R-equivalent-sql']),
    'direct-source':('equivalent',['R-model'])}


def derive(raw):
    rows={}
    for row in raw:
        key=row['id']
        if type(key) is not int:raise ValueError('Customer key must be a non-NULL exact integer')
        record={('customer_id' if c=='id' else c):v for c,v in row.items()}
        if key in rows and rows[key]!=record:raise ValueError('Conflicting same-ID input requires task-specific adjudication')
        rows[key]=record
    return [rows[k] for k in sorted(rows)]


def compare(wanted, actual, columns):
    if actual.get('absent'):
        return {'equal':False,'missing_columns':list(columns),'missing_rows':len(wanted),'extra_rows':0}
    missing=set(columns)-set(actual['columns'])
    if missing:return {'equal':False,'missing_columns':sorted(missing),'missing_rows':None,'extra_rows':None}
    if any(type(r.get('customer_id')) is not int for r in actual['rows']):
        return {'equal':False,'missing_columns':[],'missing_rows':None,'extra_rows':None,'invalid_key':True}
    def row(r):
        key=r['customer_id']
        if type(key) is not int:raise ValueError('Expected exact integer customer_id')
        return tuple((type(r[c]).__name__,str(r[c])) for c in columns)
    expected=Counter(row(r) for r in wanted);observed=Counter(row(r) for r in actual['rows'])
    return {'equal':expected==observed,'missing_columns':[],
            'missing_rows':sum((expected-observed).values()),'extra_rows':sum((observed-expected).values())}


def encode(value):return json.dumps(value,sort_keys=True,indent=2,default=str)+'\n'
def ref(path):return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def main(mode):
    import duckdb
    db='/app/analytics_engineering.duckdb';logs=Path('/logs/agent');model=Path('/app/models/warehouse/dim_customer.sql')
    kind,requirements=MODES[mode]
    config=json.loads(Path(__file__).with_name('case.json').read_text())
    def source():
        with duckdb.connect(db,read_only=True) as conn:
            q=conn.execute('SELECT * FROM main.customer ORDER BY id');columns=[c[0] for c in q.description]
            return {'columns':columns,'rows':[dict(zip(columns,r)) for r in q.fetchall()]}
    inputs=source();input_path=logs/'customer-input.json';input_path.write_text(encode(inputs))
    assert ref(input_path)['sha256']==config['pristine_input_sha256']
    expected=derive(inputs['rows']);columns=['customer_id' if c=='id' else c for c in inputs['columns']]
    assert len(inputs['rows'])==30 and len(expected)==29 and len(columns)==18
    original_sql=model.read_text()
    def build(label):
        result=subprocess.run(['dbt','run','--select','dim_customer'],cwd='/app',capture_output=True,text=True)
        (logs/(label+'-dbt.log')).write_text(result.stdout+result.stderr)
        assert result.returncode==0,'SQL execution failure before proof; not a semantic negative'
    def snapshot(label):
        out=logs/('customer-'+label);out.mkdir()
        with duckdb.connect(db,read_only=True) as conn:
            exists=conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_name='dim_customer'").fetchone()[0]
            if exists:
                q=conn.execute('SELECT * FROM main.dim_customer');names=[c[0] for c in q.description]
                observed={'columns':names,'rows':[dict(zip(names,r)) for r in q.fetchall()]}
            else:observed={'columns':[],'rows':[],'absent':True}
        observed.update(model_exists=model.is_file(),model_path=str(model))
        saved=[]
        for name,p in [('model.sql',model),('dbt_project.yml',Path('/app/dbt_project.yml')),('profiles.yml',Path('/app/profiles.yml')),('manifest.json',Path('/app/target/manifest.json'))]:
            if p.is_file():shutil.copyfile(p,out/name);saved.append(name)
        observed['source_files']=[ref(out/name) for name in saved]
        path=out/'snapshot.json';path.write_text(encode(observed));return observed,path
    build('reference');before,before_path=snapshot('before');assert compare(expected,before,columns)['equal']
    # No hidden answer used; each candidate is rebuilt from the staged public rows.
    if mode=='table-only':model.unlink()
    elif mode=='source-only':
        with duckdb.connect(db) as conn:conn.execute('DROP TABLE main.dim_customer')
    elif mode=='alternate-path':
        alternative=Path('/app/models/dim_customer.sql');model.rename(alternative);model=alternative;build('case')
    elif mode!='reference':
        select=', '.join(('id as customer_id' if c=='id' else '"'+c+'"') for c in inputs['columns'])
        base='select distinct '+select+" from {{ ref('stg_customer') }}"
        final='select * from correct'
        if mode=='duplicate-source':base='select '+select+" from {{ ref('stg_customer') }}"
        elif mode=='duplicate-output':final='select * from correct union all select * from correct where customer_id=2'
        elif mode=='missing-customer':final='select * from correct where customer_id<>2'
        elif mode=='swapped-keys':final='select * replace(case when customer_id=1 then 2 when customer_id=2 then 1 else customer_id end as customer_id) from correct'
        elif mode=='wrong-name':final="select * replace(case when customer_id=1 then 'Incorrect customer name' else last_name end as last_name) from correct"
        elif mode=='null-key':final='select * replace(case when customer_id=1 then NULL else customer_id end as customer_id) from correct'
        elif mode=='no-rename':final='select customer_id as id, '+', '.join('"'+c+'"' for c in columns if c!='customer_id')+' from correct'
        elif mode=='reordered':final+=' order by customer_id desc'
        elif mode=='extra-column':final="select *, 'derived-column' as extra_information from correct"
        elif mode=='direct-source':base='select distinct '+select+' from main.customer'
        elif mode=='view':pass
        elif mode!='distinct':raise ValueError(mode)
        model.write_text(("{{ config(materialized='view') }}\n" if mode=='view' else '')+'with correct as ('+base+')\n'+final+'\n');build('case')
    after,after_path=snapshot('after');comparison=compare(expected,after,columns)
    if kind in {'witness','equivalent'}:
        assert comparison['equal'] or mode=='source-only' and after.get('absent') and model.read_text()==original_sql
        derivation='Public main.customer contains 30 rows with one identical duplicate ID 1. Independent Python groups exact integer IDs, verifies equal attributes within each group, renames id and retains 29 complete rows. The real dbt build matches all 18 columns.'
        if mode=='source-only':derivation+=' The built output is then removed without changing the proven SQL. The native workflow runs dbt itself; a source-only submission must remain legal.'
        if mode=='extra-column':derivation+=' All required columns and rows remain exact; an extra informational column is not forbidden by the instruction.'
    else:
        assert not comparison['equal'] or mode=='table-only' and not model.exists()
        if mode.startswith('duplicate-'):
            counts=Counter(r['customer_id'] for r in after['rows']);assert len(after['rows'])==30 and any(n==2 for n in counts.values())
            assert comparison['missing_rows']==0 and comparison['extra_rows']==1
            derivation='The rebuilt model contains one extra duplicate customer row. Its customer_id is therefore not unique, contradicting the explicit primary-key requirement; the independent multiset comparison identifies the extra multiplicity.'
        elif mode=='missing-customer':
            assert comparison['missing_rows']==1 and comparison['extra_rows']==0
            derivation='Customer ID 2 exists in the public source but is absent from the model.'
        elif mode=='swapped-keys':
            assert comparison['missing_rows']==2 and comparison['extra_rows']==2
            assert all(Counter(str(r[c]) for r in before['rows'])==Counter(str(r[c]) for r in after['rows']) for c in columns)
            derivation='The full per-column multisets are preserved, but attributes of IDs 1 and 2 are associated with the wrong customer ID.'
        elif mode=='wrong-name':
            assert comparison['missing_rows']==1 and comparison['extra_rows']==1
            derivation='One customer name differs from the public source while its ID is preserved.'
        elif mode=='no-rename':
            assert comparison['missing_columns']==['customer_id']
            derivation='The output column remains id; the explicitly requested customer_id column is absent despite successful SQL execution.'
        elif mode=='null-key':
            assert sum(r['customer_id'] is None for r in after['rows'])==1
            derivation='A successful dbt build replaces the source customer ID 1 with NULL. A NULL value cannot satisfy the explicit primary-key requirement.'
        elif mode=='table-only':
            assert comparison['equal'] and not model.exists()
            derivation='The output table is still correct, but the requested dim_customer dbt model file has been removed. A final table alone does not fulfill the explicit model deliverable.'
    assert encode(source())==input_path.read_text(),'Input changed'
    proof={'case':mode,'validity':'built_from_inputs' if kind=='witness' else 'legal_equivalent' if kind=='equivalent' else 'business_error',
        'requirement_ids':requirements,'derivation':derivation,'comparison':comparison,
        'tested_checker_invoked_to_define_validity':False,'expected_columns':columns,
        'input_artifacts':[ref(input_path)],'submission_artifacts':[ref(Path(db)),ref(before_path),ref(after_path)],
        'scope':'Pinned full customer input; counterexample SQL built successfully before unchanged native verifier'}
    (logs/'case-proof.json').write_text(encode(proof));print(encode({'case':mode,'validity':proof['validity'],'comparison':comparison}))


if __name__=='__main__':main(sys.argv[1])

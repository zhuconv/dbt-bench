"""Construct inventory cases and prove their meaning before native grading."""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

COLUMNS = ['inventory_id','transaction_type','transaction_created_date','transaction_modified_date',
           'product_id','quantity','purchase_order_id','customer_order_id','comments']
MODEL = Path('/app/models/warehouse/fact_inventory.sql')
OUT = Path('/logs/agent/inventory-case')
DATABASE = '/app/analytics_engineering.duckdb'


def encoded(value): return json.dumps(value, default=str, sort_keys=True, separators=(',', ':'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path): return {'path':str(path), 'sha256':sha(path)}
def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value, default=str, sort_keys=True, indent=2)+'\n')


def snapshot(path):
    import duckdb
    with duckdb.connect(DATABASE,read_only=True) as con:
        cur=con.execute('select * from main.inventory_transactions')
        source={'columns':[c[0] for c in cur.description], 'rows':sorted(cur.fetchall(),key=encoded)}
        cur=con.execute('select * from main.fact_inventory')
        output={'columns':[c[0] for c in cur.description], 'rows':sorted(cur.fetchall(),key=encoded)}
        relation_type=con.execute("select table_type from information_schema.tables where table_schema='main' and table_name='fact_inventory'").fetchone()[0]
    value=json.loads(encoded({'source':source,'output':output,'model_sha256':sha(MODEL) if MODEL.exists() else None,
        'model_sql':MODEL.read_text() if MODEL.exists() else None,'model_present':MODEL.is_file(),
        'named_models':[str(p) for p in Path('/app/models').rglob('fact_inventory.sql')],
        'relation_type':relation_type,'duckdb_version':duckdb.__version__}))
    save(path,value)
    return value


def expected_from_source(source):
    assert source['columns']==['id',*COLUMNS[1:]]
    groups=defaultdict(list)
    for values in source['rows']:
        row=list(values)
        assert isinstance(row[0],int) and not isinstance(row[0],bool)
        row[2]=datetime.strptime(row[2],'%m/%d/%Y %H:%M:%S').date().isoformat()
        groups[row[0]].append(row)
    assert groups and all(len({encoded(row) for row in rows})==1 for rows in groups.values())
    return sorted([rows[0] for rows in groups.values()])


def output_rows(value):
    output=value['output']
    assert set(COLUMNS)<=set(output['columns']) and len(output['columns'])==len(set(output['columns']))
    rows=[[row[output['columns'].index(name)] for name in COLUMNS] for row in output['rows']]
    for row in rows:
        # Only the declared numeric-quantity representation is normalized, exactly.
        quantity=row[5]
        if isinstance(quantity,str):
            value=Decimal(quantity)
            assert value.is_finite() and value==value.to_integral_value()
            row[5]=int(value)
    return rows


def build(number):
    p=subprocess.run(['dbt','run','--select','fact_inventory'],cwd='/app',capture_output=True,text=True)
    (OUT/f'dbt-{number}.log').write_text(p.stdout+p.stderr)
    assert p.returncode==0, 'Case SQL failed; not a semantic negative'
    return snapshot(OUT/f'build-{number}.json')


def run(mode):
    OUT.mkdir(parents=True,exist_ok=False)
    before=snapshot(OUT/'before.json')
    expected=expected_from_source(before['source'])
    first,second=expected[0][0],expected[1][0]
    base='''select distinct
        id as inventory_id, transaction_type,
        cast(strptime(transaction_created_date, '%m/%d/%Y %H:%M:%S') as date) as transaction_created_date,
        transaction_modified_date, product_id, quantity, purchase_order_id, customer_order_id, comments
        from {{ ref('stg_inventory_transactions') }}'''
    if mode not in {'reference','missing-model'}:
        projection=', '.join(COLUMNS)
        query='select '+projection+' from repaired'
        if mode=='extra-column': query="select *, 'deduplicated' as repair_note from repaired"
        elif mode=='numeric-representation': query='select '+', '.join('cast(quantity as decimal(18,0)) as quantity' if n=='quantity' else n for n in COLUMNS)+' from repaired'
        elif mode=='view': pass
        else: raise ValueError('Unknown case mode')
        config="{{ config(materialized='view') }}\n" if mode=='view' else ''
        MODEL.write_text(config+'with repaired as (\n'+base+'\n)\n'+query+'\n')
    after=build(1)
    repeated=build(2)
    assert before['source']==after['source']==repeated['source']
    assert after['model_sha256']==repeated['model_sha256']
    wanted=Counter(map(encoded,expected))
    observed=Counter(map(encoded,output_rows(after)))
    assert observed==Counter(map(encoded,output_rows(repeated)))
    assert observed==wanted
    rows=output_rows(after); counts=Counter(row[0] for row in rows)
    assert len(rows)==len(expected) and all(n==1 for n in counts.values())
    if mode=='extra-column':
        assert set(after['output']['columns'])==set(COLUMNS)|{'repair_note'}
        assert {r[after['output']['columns'].index('repair_note')] for r in after['output']['rows']}=={'deduplicated'}
    if mode=='view': assert after['relation_type']==repeated['relation_type']=='VIEW'
    if mode=='missing-model':
        MODEL.unlink()
        submitted=snapshot(OUT/'submission.json')
        assert not submitted['model_present'] and not submitted['named_models']
        assert Counter(map(encoded,output_rows(submitted)))==wanted
    else: submitted=snapshot(OUT/'submission.json')
    validity='built_from_inputs' if mode=='reference' else 'business_error' if mode=='missing-model' else 'legal_equivalent'
    save(OUT/'expected.json',{'columns':COLUMNS,'rows':expected})
    proof=dict(case=mode,validity=validity,derivation='Python groups complete source rows by integer ID, rejects conflicting duplicates, parses the existing date transformation and preserves all nine existing columns. Compare complete keyed rows and multiplicities before any grader. No gold or verifier result is used.',
        tested_checker_invoked_to_define_validity=False,gold_accessed_by_recompute=False,
        input_artifacts=[ref(OUT/'before.json'),ref(OUT/'expected.json')],
        submission_artifacts=([ref(MODEL)] if MODEL.exists() else [])+[ref(OUT/'build-1.json'),ref(OUT/'build-2.json'),ref(OUT/'submission.json')],
        expected_rows=len(expected),actual_rows=len(rows),source_unchanged=True,repeated_build_unchanged=True,
        missing_rows=sum((wanted-observed).values()),extra_rows=sum((observed-wanted).values()),
        required_model_absent=mode=='missing-model',violation='Required dbt model source removed; correct residual table does not deliver the requested model' if mode=='missing-model' else None,
        legal_equivalence_basis='All existing keyed rows/columns and uniqueness preserved. The one-sentence task does not restrict added columns, exact numeric representation or materialization. No checker result is used to establish this scope.')
    save(Path(os.environ['DBT_BENCH_PROOF']),proof)
    print(encoded(proof))


if __name__=='__main__':
    if sys.argv[1]=='capture-after-native': snapshot(Path('/logs/verifier/inventory-after-native.json'))
    else: run(sys.argv[1])

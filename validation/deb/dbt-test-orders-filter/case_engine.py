"""Actual dbt builds and independently established original-data case proofs."""
from collections import Counter
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys
import duckdb
from independent import FLAGS,MONEY,derive,compare

PROJECT=Path('/app/dbt_models_duckdb')
MODEL=PROJECT/'models/marts/sales/production_sales.sql'
DB=Path('/app/database/retail.duckdb')
LOGS=Path('/logs/agent')
MODES={
    'full-reference':('witness',['R-filter','R-preserve','R-date','R-money','R-association']),
    'native-minimum':('negative',['R-preserve']),
    'reordered':('equivalent',['R-order']),
    'swap-totals':('negative',['R-association']),
    'shift-date':('negative',['R-date']),
    'wrong-status':('negative',['R-association']),
    'fractional-money':('negative',['R-money']),
    'include-test':('negative',['R-filter-test']),
    'include-sample':('negative',['R-filter-sample']),
    'include-internal':('negative',['R-filter-internal']),
    'drop-null-flags':('negative',['R-null-flags']),
    'missing-order':('negative',['R-completeness']),
    'duplicate-order':('negative',['R-unique-order']),
}


def ref(path):
    path=Path(path)
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def encoded(value):return json.dumps(value,default=str,sort_keys=True,indent=2)+'\n'
def identifier(value):return '"'+value.replace('"','""')+'"'
def literal(value):return "'"+value.replace("'","''")+"'"


def input_rows(conn):
    q=conn.execute('SELECT * FROM main.orders ORDER BY order_id')
    columns=[{'name':d[0].lower(),'type':str(d[1])} for d in q.description]
    rows=[dict(zip([c['name'] for c in columns],row)) for row in q.fetchall()]
    return {'columns':columns,'rows':rows}


def snapshot(name):
    out=LOGS/('orders-filter-'+name);out.mkdir(exist_ok=False)
    with duckdb.connect(str(DB),read_only=True) as conn:
        q=conn.execute('SELECT * FROM main.production_sales ORDER BY order_id')
        columns=[d[0].lower() for d in q.description]
        rows=[dict(zip(columns,r)) for r in q.fetchall()]
        types=dict(conn.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='main' AND table_name='production_sales'").fetchall())
        table_type=conn.execute("SELECT table_type FROM information_schema.tables WHERE table_schema='main' AND table_name='production_sales'").fetchone()[0]
    sources={}
    for relative in ['dbt_project.yml','profiles.yml','packages.yml','package-lock.yml','models/marts/sales/production_sales.sql','models/marts/sales/_sources.yml']:
        p=PROJECT/relative
        if p.is_file():
            dest=out/'submission'/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);sources[relative]=ref(dest)
    for relative in ['manifest.json','run_results.json']:
        p=PROJECT/'target'/relative
        if p.is_file():shutil.copyfile(p,out/relative)
    payload={'columns':columns,'rows':rows,'types':types,'table_type':table_type,'source_files':sources,
             'python':sys.executable,'duckdb_version':duckdb.__version__}
    path=out/'snapshot.json';path.write_text(encoded(payload));return payload,path


def build(columns, mode, expected):
    selections=[('round('+identifier(c)+',2) as '+identifier(c)) if c in MONEY else identifier(c) for c in columns]
    selections.append('cast(ordered_at as date) as order_date')
    filters=[]
    for flag in FLAGS:
        if mode=='include-'+flag.split('_')[0]:continue
        filters.append(identifier(flag)+' = false' if mode=='drop-null-flags' else 'coalesce('+identifier(flag)+',false)=false')
    base='select '+', '.join(selections)+" from {{ source('main','orders') }} where "+' and '.join(filters)
    first=expected[0]
    second=next(r for r in expected[1:] if r['grand_total']!=first['grand_total'])
    key=literal(first['order_id']);key2=literal(second['order_id'])
    if mode=='swap-totals':
        expr='case when order_id='+key+' then '+str(second['grand_total'])+' when order_id='+key2+' then '+str(first['grand_total'])+' else grand_total end'
        final='select * replace('+expr+' as grand_total) from corrected'
    elif mode=='shift-date':
        final='select * replace(case when order_id='+key+" then cast(order_date+interval '1 day' as date) else order_date end as order_date) from corrected"
    elif mode=='wrong-status':
        different='DELIVERED' if first['status']!='DELIVERED' else 'COMPLETED'
        final='select * replace(case when order_id='+key+' then '+literal(different)+' else status end as status) from corrected'
    elif mode=='fractional-money':
        final='select * replace(case when order_id='+key+' then grand_total+0.001 else grand_total end as grand_total) from corrected'
    elif mode=='missing-order':final='select * from corrected where order_id<>'+key
    elif mode=='duplicate-order':final='select * from corrected union all select * from corrected where order_id='+key
    else:final='select * from corrected'+(' order by order_id desc' if mode=='reordered' else '')
    MODEL.write_text("{{ config(materialized='table',tags=['mart','sales','production']) }}\nwith corrected as ("+base+')\n'+final+'\n')
    execution=subprocess.run(['dbt','run','--select','production_sales','--profiles-dir',str(PROJECT)],cwd=PROJECT,capture_output=True,text=True)
    (LOGS/('dbt-'+mode+'.log')).write_text(execution.stdout+execution.stderr)
    assert execution.returncode==0,'Build failed before semantic proof; not a valid negative'


def main(mode):
    kind,requirements=MODES[mode]
    config=json.loads(Path(__file__).with_name('case.json').read_text())
    with duckdb.connect(str(DB),read_only=True) as conn:
        original=input_rows(conn)
        types=dict(conn.execute("SELECT lower(column_name),data_type FROM information_schema.columns WHERE lower(table_schema)='main' AND lower(table_name)='orders'").fetchall())
    input_path=LOGS/'orders-input.json';input_path.write_text(encoded(original))
    assert ref(input_path)['sha256']==config['pristine_input_sha256']
    columns=[c['name'] for c in original['columns']]
    assert len(columns)==109 and len(original['rows'])==2037
    assert set(types)==set(columns),'Incomplete actual input type metadata'
    expected=derive(original['rows']);assert len(expected)==1950
    required=columns+['order_date']
    native,native_path=snapshot('native-reference')
    native_comparison=compare(expected,native,required,types)
    assert len(native_comparison['missing_columns'])==105 and not native_comparison['value_mismatches']
    assert not native_comparison['missing_keys'] and not native_comparison['extra_keys'] and not native_comparison['duplicate_keys']
    if mode=='native-minimum':
        after,path=native,native_path
        comparison=native_comparison
        derivation='The instruction requires every source column. The public source has 109 columns, while the native solution provides only four of those plus order_date; 105 required source columns are absent. Its retained five columns otherwise match the independently derived 1950 orders.'
        violation='schema_preservation'
    else:
        build(columns,'full-reference',expected)
        before,before_path=snapshot('correct-baseline')
        assert compare(expected,before,required,types)['equal']
        if mode!='full-reference':build(columns,mode,expected)
        after,path=snapshot('after')
        comparison=compare(expected,after,required,types)
        if kind in {'witness','equivalent'}:
            assert comparison['equal']
            derivation='Independent Python excludes rows with any of the three explicit Boolean purpose flags, retains NULL-flag orders, preserves all 109 columns, rounds declared monetary fields with Decimal half-up and derives order_date; all 1950 rows and 110 output columns match the real dbt build.'
            violation=None
        else:
            assert not comparison['equal'] and not comparison['missing_columns']
            violation='data_semantics'
            if mode=='swap-totals':
                assert len(comparison['value_mismatches'])==2 and {x['column'] for x in comparison['value_mismatches']}=={'grand_total'}
                assert Counter(str(r['grand_total']) for r in after['rows'])==Counter(str(r['grand_total']) for r in before['rows'])
                derivation='Two eligible order IDs have each other\'s rounded grand_total. The complete value multiset and total revenue stay unchanged, but independent source-to-order association differs in exactly two cells.'
            elif mode in {'shift-date','wrong-status','fractional-money'}:
                assert len(comparison['value_mismatches'])==1
                column={'shift-date':'order_date','wrong-status':'status','fractional-money':'grand_total'}[mode]
                assert comparison['value_mismatches'][0]['column']==column
                derivation='Exactly one '+column+' value differs from the independent source-derived requirement: '+str(comparison['value_mismatches'][0])
            elif mode.startswith('include-'):
                flag=mode.removeprefix('include-')+'_order_flag'
                source={r['order_id']:r for r in original['rows']}
                assert comparison['extra_keys'] and not comparison['missing_keys'] and not comparison['value_mismatches']
                assert all(source[k][flag] is True for k in comparison['extra_keys'])
                derivation='The successfully built submission includes '+str(len(comparison['extra_keys']))+' extra orders with '+flag+'=TRUE, independently read from the pristine source.'
            elif mode=='drop-null-flags':
                source={r['order_id']:r for r in original['rows']}
                assert len(comparison['missing_keys'])==37 and all(all(source[k][f] is None for f in FLAGS) for k in comparison['missing_keys'])
                derivation='37 otherwise eligible orders have NULL purpose flags, meaning no affirmative test/sample/internal flag; requiring false instead of coalescing drops them. This NULL policy is explicit in this scoped contract.'
            elif mode=='missing-order':
                assert len(comparison['missing_keys'])==1
                derivation='One eligible order from the public source is absent from the rebuilt model.'
            elif mode=='duplicate-order':
                assert len(comparison['duplicate_keys'])==1 and list(comparison['duplicate_keys'].values())==[2]
                derivation='One eligible order appears twice although source order_id is unique; row multiplicity and unique identifier requirements are violated.'
            else:raise AssertionError(mode)
    with duckdb.connect(str(DB),read_only=True) as conn:
        assert encoded(input_rows(conn))==input_path.read_text(),'Public source changed'
    proof={'validity':'built_from_inputs' if kind=='witness' else 'legal_equivalent' if kind=='equivalent' else 'business_error',
        'requirement_ids':requirements,'case':mode,'derivation':derivation,'violation_kind':violation,
        'tested_checker_invoked_to_define_validity':False,
        'input_artifacts':[ref(input_path)],'submission_artifacts':[ref(DB),ref(path)],
        'comparison':comparison,'native_reference_comparison':native_comparison,
        'data_scope':'Original full 2037-row main.orders, pristine input content hash verified',
        'expected_columns':required,'input_types':types,
        'scope_limit':'Only the three explicitly named purpose flags are interpreted; no policy is invented for exclude_from_reporting'}
    Path(os.environ['DBT_BENCH_PROOF']).write_text(encoded(proof))
    print(json.dumps({'case':mode,'validity':proof['validity'],'rows':len(after['rows']),
        'missing_columns':len(comparison['missing_columns']),'changed_cells':len(comparison['value_mismatches'])}))


if __name__=='__main__':main(sys.argv[1])

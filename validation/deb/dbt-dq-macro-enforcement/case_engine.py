"""Build DQ cases and independently establish validity before native grading."""
from pathlib import Path
from collections import Counter
import hashlib,json,os,shutil,subprocess,sys
import duckdb
from independent import INPUTS,encoded,canonical_inputs,derive,compare

PROJECT=Path(os.environ.get('DBT_PROJECT_DIR_DUCKDB','/app/dbt_models_duckdb'))
DB=Path(os.environ.get('DUCKDB_PATH','/app/database/retail.duckdb'))
MODEL=PROJECT/'models/marts/sales/fct_order_line_detail.sql'
LOGS=Path('/logs/agent')
MODES={
 'reference':('witness',['R-grain','R-normalization','R-pricing','R-corrections']),
 'reordered':('equivalent',['R-order']),
 'duplicate-replaces-missing':('negative',['R-grain']),
 'missing-row':('negative',['R-grain']),
 'missing-variant-column':('negative',['R-preserve-columns']),
 'null-line-total':('negative',['R-pricing','R-existing-nulls']),
 'null-net-corrected':('negative',['R-corrections']),
 'wrong-profit':('negative',['R-pricing']),
 'wrong-sku':('negative',['R-normalization']),
 'table-materialization':('negative',['R-workflow']),
}


def ref(path):
    return {'path':str(path),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def inputs():
    tables={}
    with duckdb.connect(str(DB),read_only=True) as conn:
        for name in INPUTS:
            cur=conn.execute('select * from main.'+name)
            columns=[c[0].lower() for c in cur.description]
            tables[name]=[dict(zip(columns,r)) for r in cur.fetchall()]
    return canonical_inputs(json.loads(encoded(tables)))


def snapshot(label):
    out=LOGS/('dq-'+label);out.mkdir(exist_ok=False)
    with duckdb.connect(str(DB),read_only=True) as conn:
        cur=conn.execute('select * from main.fct_order_line_detail')
        columns=[c[0].lower() for c in cur.description]
        rows=[dict(zip(columns,r)) for r in cur.fetchall()]
        table_type=conn.execute("select table_type from information_schema.tables where table_schema='main' and table_name='fct_order_line_detail'").fetchone()[0]
    for relative in ['models/marts/sales/fct_order_line_detail.sql','macros/data_quality/add_dq_flags.sql',
                     'macros/data_quality/dq_checks.sql','dbt_project.yml','profiles.yml','target/run_results.json','target/manifest.json']:
        path=PROJECT/relative
        if path.is_file():
            target=out/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    data={'columns':columns,'rows':rows,'table_type':table_type,
          'files':[ref(p) for p in sorted(out.rglob('*')) if p.is_file()],
          'python':sys.executable,'duckdb_version':duckdb.__version__}
    path=out/'snapshot.json';path.write_text(encoded(data)+'\n')
    return json.loads(path.read_text()),path


def main(mode):
    kind,requirements=MODES[mode]
    config=json.loads(Path(__file__).with_name('case.json').read_text())
    original=inputs();input_path=LOGS/'dq-inputs.json';input_path.write_text(encoded(original)+'\n')
    assert ref(input_path)['sha256']==config['pristine_inputs_sha256'],'Public input identity changed'
    expected=derive(original)
    before,before_path=snapshot('before');baseline=compare(expected,before)
    (LOGS/'independent-baseline.json').write_text(encoded(baseline)+'\n')
    assert baseline['equal'], 'Scoped independent pricing/normalization baseline disagrees; inspect retained evidence'
    assert before['table_type']=='VIEW'
    sql=MODEL.read_text()
    assert hashlib.sha256(sql.encode()).hexdigest()==config['native_reference_model_sha256']
    if mode!='reference':
        selector={
            'reordered':'select * from original_result order by order_line_id desc',
            'duplicate-replaces-missing':'select * from original_result where order_line_id <> (select max(order_line_id) from original_result) union all select * from original_result where order_line_id = (select min(order_line_id) from original_result)',
            'missing-row':'select * from original_result where order_line_id <> (select max(order_line_id) from original_result)',
            'missing-variant-column':'select * exclude(variant_name) from original_result',
            'null-line-total':'select * replace(cast(null as decimal(18,6)) as line_total) from original_result',
            'null-net-corrected':'select * replace(cast(null as boolean) as unit_price_net_corrected) from original_result',
            'wrong-profit':'select * replace(line_profit + 1 as line_profit) from original_result',
            'wrong-sku':"select * replace('WRONG-SKU-VALIDATION-CASE' as sku) from original_result",
        }
        changed=("{{ config(materialized='table') }}\n"+sql if mode=='table-materialization' else
                 'with original_result as (\n'+sql.rstrip().rstrip(';')+'\n)\n'+selector[mode]+'\n')
        MODEL.write_text(changed)
        run=subprocess.run(['dbt','run','--select','fct_order_line_detail','--profiles-dir',str(PROJECT)],cwd=PROJECT,capture_output=True,text=True)
        (LOGS/'case-build.log').write_text(run.stdout+run.stderr)
        assert run.returncode==0,'Case SQL did not build; not a semantic negative'
    after,after_path=snapshot('after');result=compare(expected,after)
    assert encoded(inputs())==encoded(original),'Public inputs changed'
    facts={'baseline_comparison':baseline,'comparison':result,'before_rows':len(before['rows']),
           'after_rows':len(after['rows']),'after_table_type':after['table_type'],'source_inputs_unchanged':True}
    if kind!='negative':
        assert result['equal'] and after['table_type']=='VIEW'
        assert Counter(encoded(r) for r in before['rows'])==Counter(encoded(r) for r in after['rows'])
        derivation='Public source keys, exact-string left joins, Decimal sums/products and an independent Python model of pinned DOUBLE division/rounding reproduce 15 scoped fields; all 33 output columns retain their multiset across this case. Other DQ flags are not independently certified by this proof.'
    elif mode=='duplicate-replaces-missing':
        assert len(result['missing_keys'])==len(result['duplicate_keys'])==1
        assert result['duplicate_keys'][0]['count']==2 and result['actual_rows']==result['expected_rows']
        assert not result['mismatches'] and not result['extra_keys']
        derivation='Source order_line_id values are independently checked unique. The successfully rebuilt model omits one source key and duplicates another while retaining total row count; this violates one row per original order line even though every remaining row value is correct.'
    elif mode=='missing-row':
        assert len(result['missing_keys'])==1 and not result['duplicate_keys'] and not result['mismatches']
        derivation='One independently enumerated source order_line_id is missing after the successful rebuild, and the output has one fewer row than the source.'
    elif mode=='missing-variant-column':
        assert result['missing_preserved_columns']==['variant_name'] and not result['mismatches']
        derivation='The original public model and initialized view expose variant_name; the instruction requires preserving every existing column. This successful build removes only that column.'
    elif mode in {'null-line-total','null-net-corrected','wrong-profit','wrong-sku'}:
        column={'null-line-total':'line_total','null-net-corrected':'unit_price_net_corrected','wrong-profit':'line_profit','wrong-sku':'sku'}[mode]
        assert result['mismatches'] and {m['column'] for m in result['mismatches']}=={column}
        assert not result['missing_keys'] and not result['extra_keys'] and not result['duplicate_keys']
        derivation={'null-line-total':'Source quantity/price/discount/tax are non-NULL; independent Decimal arithmetic requires non-NULL line totals. The model instead emits NULL while retaining correct other fields.',
            'null-net-corrected':'The required correction flag is the literal pinned-backend absolute-difference > 0.01 boolean with NULL treated as FALSE. The successfully rebuilt model substitutes NULL for these independently computed booleans.',
            'wrong-profit':'Independent Decimal recomputation establishes line_profit from corrected total and the specified cost fallback; the model adds one to each value after a successful build.',
            'wrong-sku':'The prescribed normalized primary/fallback SKU is independently recomputed from public product and order-line inputs. The rebuilt model replaces it with an unrelated constant.'}[mode]
    elif mode=='table-materialization':
        assert result['equal'] and after['table_type']=='BASE TABLE'
        derivation='The initial public project and native reference materialize this model as a view. Changing it to a table violates the explicit preserve-materialization instruction despite unchanged scoped values.'
    else:raise AssertionError(mode)
    proof={'schema_version':1,'case':mode,'requirement_ids':requirements,
           'validity':{'witness':'built_from_inputs','equivalent':'legal_equivalent','negative':'business_error'}[kind],
           'tested_checker_invoked_to_define_validity':False,'derivation':derivation,'facts':facts,
           'scope':'Original fixed input and named requirement only; other DQ macro semantics remain separately pending.',
           'input_artifacts':[ref(input_path)],'submission_artifacts':[ref(before_path),ref(after_path),ref(MODEL)]}
    Path(os.environ['DBT_BENCH_PROOF']).write_text(encoded(proof)+'\n')
    print(encoded({'mode':mode,'validity':proof['validity'],'mismatches':len(result['mismatches']),
                   'missing_keys':len(result['missing_keys']),'duplicate_keys':len(result['duplicate_keys'])}))


if __name__=='__main__':main(sys.argv[1])

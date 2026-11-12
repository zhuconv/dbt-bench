"""Run declared input fixtures through real dbt and unchanged native grading."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys
from decimal import Decimal
import duckdb
from independent import SESSION_COLUMNS,SALES_COLUMNS,encoded,canonical,calculate,compare
from fixtures import fixture

PROJECT=Path(os.environ['DBT_PROJECT_DIR_DUCKDB']);DB=os.environ['DUCKDB_PATH']
MODEL=PROJECT/'models/marts/attribution_report.sql';LOGS=Path('/logs/agent')
MODES={
 'original-reference':('witness',['R-conversion','R-single-touch','R-idempotence']),
 'fixture-reference':('witness',['R-channel-priority','R-quality','R-window','R-position','R-decay','R-normalization','R-bonus']),
 'fixture-extra-null-column':('equivalent',['R-output-location']),
 'fixture-paid-priority':('negative',['R-channel-priority']),
 'fixture-duration':('negative',['R-quality']),
 'fixture-pageviews':('negative',['R-quality']),
 'fixture-bot':('negative',['R-quality']),
 'fixture-upper-bound':('negative',['R-window']),
 'fixture-lower-bound':('negative',['R-window']),
 'fixture-position':('negative',['R-position']),
 'fixture-flat-decay':('negative',['R-decay']),
 'fixture-integer-days':('negative',['R-decay']),
 'fixture-global-normalization':('negative',['R-normalization']),
 'fixture-no-bonus':('negative',['R-bonus']),
 'fixture-credit-bonus':('negative',['R-bonus']),
 'fixture-revenue-max':('negative',['R-conversion']),
 'fixture-customer-join':('negative',['R-conversion']),
 'fixture-drop-single':('negative',['R-single-touch']),
 'fixture-excess-precision':('negative',['R-aggregate-round']),
}


def ref(path):return {'path':str(path),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def inputs():
    data={}
    with duckdb.connect(DB,read_only=True) as con:
        for key,table,cols in [('sessions','stg_digital__web_sessions',SESSION_COLUMNS),('sales','stg_analytics__fact_sales',SALES_COLUMNS)]:
            cur=con.execute('select '+','.join(cols)+' from main.'+table)
            data[key]=[dict(zip(cols,r)) for r in cur.fetchall()]
    return canonical(json.loads(encoded(data)))


def snapshot(label):
    out=LOGS/label;out.mkdir(exist_ok=False)
    with duckdb.connect(DB,read_only=True) as con:
        cur=con.execute('select * from main.attribution_report')
        cols=[c[0].lower() for c in cur.description];rows=[dict(zip(cols,r)) for r in cur.fetchall()]
        relation_type=con.execute("select table_type from information_schema.tables where table_schema='main' and table_name='attribution_report'").fetchone()[0]
    for name in ['models/marts/attribution_report.sql','dbt_project.yml','profiles.yml','target/manifest.json','target/run_results.json']:
        p=PROJECT/name
        if p.exists():
            dst=out/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
    d={'columns':cols,'rows':rows,'relation_type':relation_type,'files':[ref(p) for p in sorted(out.rglob('*')) if p.is_file()],
       'python':sys.executable,'duckdb':duckdb.__version__}
    path=out/'snapshot.json';path.write_text(encoded(d)+'\n');return json.loads(path.read_text()),path


def install_fixture():
    # Only declared input relations in this isolated validation copy change.
    # Native staging source files are retained; the model-only build reads these fixture relations.
    data=fixture()
    with duckdb.connect(DB) as con:
        for key,table,cols,types in [
            ('sessions','stg_digital__web_sessions',SESSION_COLUMNS,['varchar','varchar','timestamp','varchar','varchar','varchar','varchar','integer','integer','boolean','varchar']),
            ('sales','stg_analytics__fact_sales',SALES_COLUMNS,['varchar','decimal(18,4)'])]:
            con.execute('drop view main.'+table)
            con.execute('create table main.'+table+' ('+','.join(c+' '+t for c,t in zip(cols,types))+')')
            con.executemany('insert into main.'+table+' values ('+','.join('?' for _ in cols)+')',[[r[c] for c in cols] for r in data[key]])
    actual=inputs()
    # Exact data equivalence with declared fixture, permitting only typed storage representation.
    def norm(value):
        d=json.loads(encoded(value))
        for row in d['sessions']:row['session_start']=str(row['session_start']).replace('T',' ')
        for row in d['sales']:row['total_amount']=format(Decimal(row['total_amount']).normalize(),'f')
        return canonical(d)
    assert norm(actual)==norm(data)


def build(label):
    run=subprocess.run(['dbt','run','--select','attribution_report','--profiles-dir',str(PROJECT)],cwd=PROJECT,capture_output=True,text=True)
    (LOGS/(label+'.log')).write_text(run.stdout+run.stderr)
    assert run.returncode==0,'dbt failed; this is not a proved semantic case'


def change(sql,mode):
    def replace(old,new,count=1):
        nonlocal sql
        assert sql.count(old)==count,(mode,old,sql.count(old))
        sql=sql.replace(old,new)
    if mode=='fixture-paid-priority':replace("when lower(utm_medium) in ('cpc', 'ppc') then 'Paid Search'","when lower(utm_medium) in ('cpc', 'ppc') then 'Referral'")
    elif mode=='fixture-duration':replace('duration_seconds >= 5','duration_seconds >= 4')
    elif mode=='fixture-pageviews':replace('page_views >= 2','page_views >= 1')
    elif mode=='fixture-bot':replace("lower(device_type) != 'bot'","device_type != 'bot'")
    elif mode=='fixture-upper-bound':replace('s.session_start < c.conversion_at','s.session_start <= c.conversion_at')
    elif mode=='fixture-lower-bound':replace("s.session_start >= c.conversion_at - interval '14 days'","s.session_start > c.conversion_at - interval '14 days'")
    elif mode=='fixture-position':
        replace('when position = 1 then 1.5','when position = 1 then 1.3');replace('when position = total_sessions then 1.3','when position = total_sessions then 1.5')
    elif mode=='fixture-flat-decay':replace('exp(-days_diff / 7.0)','1.0')
    elif mode=='fixture-integer-days':replace('exp(-days_diff / 7.0)','exp(-floor(days_diff) / 7.0)')
    elif mode=='fixture-global-normalization':replace('(swb.raw_weight / ctw.total_weight)','(swb.raw_weight / (select sum(raw_weight) from sessions_with_bonus))',2)
    elif mode=='fixture-no-bonus':replace('case when cd.distinct_channels >= 3','case when cd.distinct_channels >= 4')
    elif mode=='fixture-credit-bonus':replace('(swb.raw_weight / ctw.total_weight) as credit','(swb.raw_weight / ctw.total_weight) * (case when swb.adjusted_revenue <> swb.revenue then 1.1 else 1 end) as credit')
    elif mode=='fixture-revenue-max':replace('sum(total_amount) as revenue','max(total_amount) as revenue')
    elif mode=='fixture-customer-join':replace('on c.customer_id = s.customer_id','on 1=1')
    elif mode=='fixture-drop-single':replace('from final_attribution\ngroup by channel','from final_attribution\nwhere credit < 1\ngroup by channel')
    elif mode=='fixture-excess-precision':replace('round(sum(credit), 2) as total_conversions','round(sum(credit), 2) + 0.001 as total_conversions')
    elif mode=='fixture-extra-null-column':replace('round(sum(attributed_revenue), 2) as total_revenue','round(sum(attributed_revenue), 2) as total_revenue, cast(NULL as varchar) as optional_note')
    else:raise ValueError(mode)
    return sql


def main(mode):
    kind,requirements=MODES[mode];config=json.loads(Path(__file__).with_name('case.json').read_text())
    sql=MODEL.read_text();assert ref(MODEL)['sha256']==config['reference_model_sha256']
    original=inputs();(LOGS/'original-inputs.json').write_text(encoded(original)+'\n')
    assert ref(LOGS/'original-inputs.json')['sha256']==config['pristine_inputs_sha256']
    original_expected=calculate(original);assert not original_expected['unresolved']
    original_snapshot,original_path=snapshot('original-output')
    assert compare(original_expected['expected'],original_snapshot)['equal']
    is_fixture=mode!='original-reference'
    if is_fixture:install_fixture();build('fixture-reference-build')
    evaluated_inputs=inputs();input_path=LOGS/'evaluated-inputs.json';input_path.write_text(encoded(evaluated_inputs)+'\n')
    expected=calculate(evaluated_inputs);assert not expected['unresolved']
    (LOGS/'independent-expectation.json').write_text(encoded(expected)+'\n')
    before,before_path=snapshot('before-case');baseline=compare(expected['expected'],before)
    (LOGS/'baseline-comparison.json').write_text(encoded(baseline)+'\n');assert baseline['equal']
    if mode not in ['original-reference','fixture-reference']:
        MODEL.write_text(change(sql,mode));build('case-build')
    after,after_path=snapshot('after-case');comparison=compare(expected['expected'],after)
    assert inputs()==evaluated_inputs
    assert after['relation_type']=='BASE TABLE'
    if kind=='negative':assert not comparison['equal'],'Variant is equivalent on this fixture; not a valid negative'
    else:assert comparison['equal']
    validity={'witness':'built_from_inputs','equivalent':'legal_equivalent','negative':'business_error'}[kind]
    proof={'schema_version':1,'case':mode,'validity':validity,'requirement_ids':requirements,
           'tested_checker_invoked_to_define_validity':False,'data_scope':'additional_input_fixture' if is_fixture else 'original_pinned_inputs',
           'derivation':('Independent Decimal reconstruction from the declared public inputs establishes the exact required channel keys and rounded credit/revenue. The successfully dbt-built variant changes those required values.' if kind=='negative' else
                         'Independent Decimal reconstruction agrees with all required channel keys and values. Extra optional NULL columns are not prohibited by the output contract.'),
           'facts':{'comparison':comparison,'baseline_comparison':baseline,'coverage':expected['coverage'],'inputs_unchanged_after_variant':True},
           'input_artifacts':[ref(input_path),ref(LOGS/'original-inputs.json'),ref(LOGS/'independent-expectation.json')],
           'submission_artifacts':[ref(original_path),ref(before_path),ref(after_path),ref(MODEL)]}
    Path(os.environ['DBT_BENCH_PROOF']).write_text(encoded(proof)+'\n')
    print(encoded({'mode':mode,'validity':validity,'data_scope':proof['data_scope'],'mismatches':len(comparison['mismatches'])}))


if __name__=='__main__':main(sys.argv[1])

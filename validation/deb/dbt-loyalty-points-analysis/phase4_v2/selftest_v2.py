"""Executed original-input submission counterexamples and legal presentations."""
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from datetime import date,datetime,timedelta
from decimal import Decimal as D,ROUND_HALF_DOWN,ROUND_HALF_EVEN
import argparse,json
from dbt_bench import io
from run_revision_v3 import load,expected_for,ROOT,BASE
from review_support_v4 import verify_trial,raw_rows,outputs,STAGES,ck
from selftest_components_v3 import altered

def run(task,report,out):
    r=io.read_json(report);e,a=load(task);v=verify_trial(Path(r['batch']['path']).parent,r['trials'][0]['trial'])
    raw=raw_rows(v);expected=expected_for(task,e,raw);snapshot,o=outputs(v,'agent-submission');w=ck('pass','Component fixture: actual formal workflow is separately scored')
    baseline=a.evaluate(o,expected,w);assert baseline['reward']==1
    cases=[]
    def test(name,changed,proof,kind='negative'):
        answer=a.evaluate(changed,expected,w)
        cases.append({'case':name,'kind':kind,'independent_proof':proof,'checker_defined_validity':False,
            'audit_reward':answer['reward'],'expected_reward':0 if kind=='negative' else None if kind=='unreviewed' else 1,
            'failed_checks':[k for k,x in answer['checks'].items() if x['status']=='fail'],
            'scope':'Actually executed synthetic typed-submission fixture derived from the captured original-input output; not a new Harbor trial.'})
    projection={name:list(item['table'].columns) for name,item in o.items()}
    if task=='dbt-customer-lifetime-value':
        derived=e.derive(raw)['outputs']
        for name in projection:projection[name]=[c for c in projection[name] if c in derived['main.'+name][0]]
    for name,item in o.items():
        rows=item['rows'];key='order_id' if name=='int_clv_customer_orders' or name=='stg_orders__orders' else 'transaction_id' if name=='stg_lp_transactions' else 'program_id' if name in ('stg_lp_programs','int_loyalty_metrics','program_loyalty_summary') else 'customer_id' if 'customer_id' in rows[0] else 'clv_segment' if name=='clv_segment_summary' else 'cohort_quarter'
        test(name+'.duplicate',altered(o,name,rows=deepcopy(rows)+[deepcopy(rows[0])]),{'rule':'Exact original-input population and declared grain','before':len(rows),'after':len(rows)+1})
        test(name+'.missing',altered(o,name,rows=deepcopy(rows[1:])),{'rule':'Required original-input row removed','before':len(rows),'after':len(rows)-1})
        test(name+'.missing_key',altered(o,name,drop=key),{'rule':'Declared keyed grain loses key column','column':key})
        test(name+'.invalid_sql_type',altered(o,name,types={key:'BLOB'}),{'rule':'Declared key logical type is not BLOB','column':key})
        for col in projection[name]:
            eligible=[i for i,row in enumerate(rows) if row[col] is not None]
            if task=='dbt-rfm-customer-segmentation' and name=='rfm_segments' and col=='customer_name':
                eligible=[i for i in eligible if rows[i]['customer_id'] not in expected['facts']['unmatched_customers']]
            if not eligible:continue
            index=eligible[0];changed=deepcopy(rows);before=changed[index][col]
            if type(before) is bool:after=not before
            elif type(before) in (int,float,D):after=before+1000000
            elif isinstance(before,(date,datetime)):after=before+timedelta(days=20000)
            elif isinstance(before,str):after='__wrong_association__'+before
            else:continue
            changed[index][col]=after
            test(name+'.value.'+col,altered(o,name,rows=changed),{'rule':'One independently recomputed key-associated value changed outside all declared equivalent values','column':col,'row_key':str(rows[index][key]),'before':str(before),'after':str(after)})
        changed=dict(o);it=dict(item);t=it['table'];it['table']=replace(t,columns=tuple(reversed(t.columns)),physical_columns=tuple(reversed(t.physical_columns)));changed[name]=it
        test(name+'.column_order',changed,{'rule':'Identical named types/values/keys; physical column order is not specified'},'equivalent')
    if task=='dbt-rfm-customer-segmentation':
        rows=deepcopy(o['rfm_segments']['rows']);ids=set(expected['facts']['unmatched_customers'])
        for row in rows:
            if row['customer_id'] in ids:row['customer_name']='Missing master record: '+row['customer_id']
        test('orphan_only_nonempty_fallback',altered(o,'rfm_segments',rows=rows),{'rule':'Only independently raw-proven absent-master IDs receive a different nonempty default; every present source name remains unchanged','ids':sorted(ids)},'equivalent')
        for row in rows:
            if row['customer_id'] in ids:row['customer_name']=''
        test('orphan_empty_name',altered(o,'rfm_segments',rows=rows),{'rule':'An empty fallback fails explicit nonempty reviewed interpretation','ids':sorted(ids)})
    if task=='dbt-loyalty-points-analysis':
        rows=deepcopy(o['program_loyalty_summary']['rows'])
        for row in rows:row['redemption_rate']=float(expected['derived']['exact'][row['program_id']]['rate'])
        test('percentage_full_precision',altered(o,'program_loyalty_summary',rows=rows),{'rule':'Actual exact ratio evaluated as finite float; same numerator/denominator, only more precision'},'equivalent')
        for value,exact,want in [('2.34','2.345',True),('2.35','2.345',True),('2.33','2.345',False),('2.36','2.345',False),('2.34','2.3449',True),('2.35','2.3449',False),('2.35','2.3451',True),('2.34','2.3451',False)]:
            actual=a.permitted_rate(D(value),D(exact));cases.append({'case':'percentage_boundary_'+value+'_'+exact,'kind':'equivalent' if want else 'negative','independent_proof':{'exact_decimal':exact,'display':value,'nearest_cent_with_tie_choice':want},'checker_defined_validity':False,'audit_reward':int(actual),'expected_reward':int(want),'scope':'Hand-computable comparator fixture, not benchmark data.'})
        for exact,allowed in [('2.5',{D(2),D(3)}),('2.49',{D(2)}),('2.51',{D(3)})]:
            actual=a.nearest(D(exact),0);cases.append({'case':'integer_boundary_'+exact,'kind':'equivalent','independent_proof':{'exact':exact,'expected':sorted(map(str,allowed))},'checker_defined_validity':False,'audit_reward':int(actual==allowed),'expected_reward':1,'scope':'Hand-computable comparator fixture.'})
    if task=='dbt-customer-lifetime-value':
        holes={x['customer_id'] for x in e.derive(raw)['profile']['undefined_conditions']}
        def regenerated(mode,fallback):
            want=e.derive(raw,mode,{cid:fallback for cid in holes})['outputs'];changed=dict(o)
            for name,item in o.items():
                keys=('order_id',) if name=='int_clv_customer_orders' else ('clv_segment',) if name=='clv_segment_summary' else ('cohort_quarter',) if name=='clv_cohort_analysis' else ('customer_id','order_month') if name=='clv_monthly_trends' else ('customer_id',)
                prev={tuple(row[k] for k in keys):row for row in item['rows']};rows=[]
                for row in want['main.'+name]:
                    old=deepcopy(prev[tuple(row[k] for k in keys)])
                    old.update({k:value for k,value in row.items() if k in old});rows.append(old)
                changed=altered(changed,name,rows=rows)
            return changed
        for mode in (ROUND_HALF_DOWN,ROUND_HALF_EVEN):
            test('nearest_'+mode,regenerated(mode,'Declining'),{'rule':'Independent raw replay uses another conventional nearest tie policy at declared precision and feeds rounded values downstream','mode':mode},'equivalent')
        changed=regenerated('ROUND_HALF_UP','Churned')
        test('unreviewed_Churned_with_coherent_aggregates',changed,{'rule':'No predicate applies to these rows, but arbitrary enum membership and no-NULL do not establish that they are churned; correctly propagated counts cannot certify the unsupported business classification. Declining alone has independent blind/gold-agreement support.','ids':sorted(holes)},'unreviewed')
        test('unreviewed_Mature_default',regenerated('ROUND_HALF_UP','Mature'),{'rule':'Another unreviewed fallback remains unknown rather than a false failure or success.','ids':sorted(holes)},'unreviewed')
        name='clv_cohort_analysis';bad=deepcopy(changed[name]['rows']);index=next(i for i,row in enumerate(bad) if row['retention_rate']!=o[name]['rows'][i]['retention_rate']);before=bad[index]['retention_rate'];bad[index]['retention_rate']=o[name]['rows'][index]['retention_rate']
        test('default_aggregate_inconsistency',altered(changed,name,rows=bad),{'rule':'A valid changed lifecycle default must propagate to cohort retention; retained old cohort rate contradicts exact churn counts','before':str(before),'after':str(bad[index]['retention_rate'])})
    # Actual two clean initial snapshots are graded by the new checker.
    STAGES['initial']='agent/audit-initial';nop=[]
    for trial in r['trials']:
        iv=verify_trial(Path(r['batch']['path']).parent,trial['trial']);sr,initial=outputs(iv,'initial')
        answer=a.evaluate(initial,expected_for(task,e,raw_rows(iv)),ck('fail','Observed initial state precedes source installation/build'))
        nop.append({'trial':trial['trial'],'snapshot':sr,'audit':answer})
    result={'task_key':r['task_key'],'candidate':io.evidence_ref(report,ROOT),'snapshot':snapshot,'baseline_reward':baseline['reward'],
        'cases':cases,'nop':nop,'model_requests':0,'fixture_only':True,'code_refs':[io.evidence_ref(Path(__file__),ROOT)],
        'counts':{'negative':sum(c['kind']=='negative' for c in cases),'equivalent':sum(c['kind']=='equivalent' for c in cases),'unreviewed':sum(c['kind']=='unreviewed' for c in cases),
                  'expectation_mismatches':sum(c['audit_reward']!=c['expected_reward'] for c in cases)}}
    if out.exists():raise ValueError('Preserve revision fixtures')
    io.write_json(out,result);print(task,result['counts'],[x['audit']['reward'] for x in nop])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    x=p.parse_args();run(x.task,x.report,x.output)

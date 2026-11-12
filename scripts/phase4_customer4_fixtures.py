"""Execute independently specified positive/negative fixtures on formal captures."""
from pathlib import Path
from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal as D
from copy import deepcopy
import argparse,json
from dbt_bench import io
from audit_tables import CapturedTable
from phase4_deb_recompute import warning,cohorts,rfm2,compare_outputs,tiering_channel_proof,numbers_equal,Rounded,nearest,KEYS
from phase4_customer4_review import ROOT,ref,write,policy,verify_trial,raw_rows,outputs,workflow,STAGES

TARGETS={
 'dbt-customer-churn-cohorts':{
  'R-cohort-first':('customer_cohorts','signup_to_first_purchase_days'),
  'R-cohort-grain':('customer_cohorts','duplicate'),
  'R-retention-period':('cohort_retention','period_month'),
  'R-retention-rate':('cohort_retention','cumulative_revenue'),
  'R-clv-base':('customer_clv','total_transactions'),
  'R-churn':('customer_clv','churn_probability'),
  'R-prediction-tier':('customer_clv','customer_tier'),
  'R-boundaries':('customer_clv','customer_lifespan_months'),
  'R-idempotency':('cohort_retention','period_revenue')},
 'dbt-rfm-customer-segmentation-2':{
  'R-population-metrics':('rfm_segments','total_orders'),
  'R-quintiles-percentiles':('rfm_segments','frequency_score'),
  'R-quarter-velocity':('rfm_segments','orders_q4_2024'),
  'R-churn-ltv':('rfm_segments','churn_probability'),
  'R-summary':('rpt_segment_summary','total_revenue'),
  'R-recommendation':('rpt_customer_recommendations','recommended_action'),
  'R-cohort':('rfm_cohort_retention','cohort_size'),
  'R-rounding-boundaries':('rfm_segments','avg_order_value')},
 'dbt-customer-churn-early-warning':{
  'R-as-of':('rpt_customer_churn_early_warning_fixed','week_start'),
  'R-dates':('rpt_customer_churn_early_warning_fixed','first_order_date'),
  'R-recent':('rpt_customer_churn_early_warning_fixed','order_count_90d'),
  'R-lifetime':('rpt_customer_churn_early_warning_fixed','lifetime_orders'),
  'R-tier':('rpt_customer_churn_early_warning_fixed','churn_risk_tier'),
  'R-quality':('rpt_customer_churn_early_warning_fixed','duplicate'),
  'R-idempotency':('rpt_customer_churn_early_warning_fixed','week_start'),
  'R-reconciliation':('rpt_customer_churn_early_warning_fixed','lifetime_revenue'),
  'R-null-input-filter':('rpt_customer_churn_early_warning_fixed','customer_id')}
}

def updated(observed,name,rows,**kw):
    o=dict(observed);entry=dict(o[name]);entry['rows']=rows;entry['table']=replace(entry['table'],rows=rows,**kw);o[name]=entry;return o
def run(task,batch,trial,out):
    v=verify_trial(batch,trial);raw=raw_rows(v);snapshot,observed=outputs(v,'agent-submission');wf=workflow(v,policy(task))
    function=warning if task.endswith('warning') else cohorts if task.endswith('cohorts') else rfm2
    def expected(o):return function(raw,{n:x['rows'] for n,x in o.items() if 'rows' in x})[0]
    def score(o):return compare_outputs(o,expected(o))
    if wf['reward']!=1 or score(observed)['reward']!=1:raise ValueError('Need actually accepted independently established baseline')
    cases=[]
    for rule,(name,field) in TARGETS[task].items():
        rows=deepcopy(observed[name]['rows']);before=deepcopy(rows[0])
        if field=='duplicate':rows.append(deepcopy(rows[0]));after={'operation':'append_duplicate','key':rows[0].get('customer_id')}
        else:
            val=rows[0][field]
            rows[0][field]=val+timedelta(days=1000) if type(val)is date else val+1000000 if type(val) in (int,float,D) else '__independently_wrong_value__'
            after={'operation':'replace_one_cell','row_key':before.get('customer_id',before.get('cohort_month',before.get('rfm_segment'))),'field':field,'before':val,'after':rows[0][field]}
        # Validity follows the separately derived raw-input baseline plus the
        # specified non-equivalent edit, before invoking the tested checker.
        proof={'validity':'business_error','tested_checker_invoked_to_define_validity':False,'rule':rule,'original_snapshot':snapshot,
            'derivation':'Independent Python arithmetic from initial raw inputs establishes the unmodified keyed value. Duplicate multiplicity or a different date/category/key/count/value violates that declared rule.',
            'operation':after,'baseline_independent_expected':next(r for r in expected(observed)[name] if all(r[k]==before[k] for k in KEYS.get(name,('customer_id',)))),
            'original_input_refs':list(v['initial']['raw'].values()),
            'scope':'Output fixture on exact original data; not a new SQL/native trial. Idempotency fixture represents an output that changed between deterministic reruns.'}
        proofref=write(out/(rule+'-proof.json'),proof);result=score(updated(observed,name,rows));ar=write(out/(rule+'-audit.json'),result)
        cases.append({'requirement':rule,'independent_proof':proofref,'audit':ar,'detected':result['reward']==0,'table':name,'mutation':after})
    for rule,part in [('R-workflow','build'),('R-project-profile','profile'),('R-backend' if task.endswith('warning') else 'R-backend-echo','backend')]:
        # warning includes profile in its workflow requirement, not a separate
        # contract rule; retain this extra component test without inventing ID.
        z=dict(v)
        if part=='build':
            z['final_files']=dict(v['final_files']);path=next(x['path'] for x in wf['checks']['build']['models']);del z['final_files'][path]
        elif part=='profile':
            z['final_files']=dict(v['final_files']);del z['final_files']['profiles.yml']
        else:z['backend_shell']={**v['backend_shell'],'output':'snowflake'}
        proofref=write(out/(rule+'-proof.json'),{'validity':'business_error','tested_checker_invoked_to_define_validity':False,'requirement':rule,'change':part,'original_execution':ref(trial/'agent/audit-rebuild/execution.json'),
            'derivation':'The contract explicitly requires the delivered source/local profile/live backend read. The fixture removes that required artifact or records a different backend.', 'scope':'Captured provenance component fixture, not an actual new shell execution.'})
        result=workflow(z,policy(task));ar=write(out/(rule+'-audit.json'),result);cases.append({'requirement':rule,'independent_proof':proofref,'audit':ar,'detected':result['reward']==0})
    controls=[]
    for kind in ('row_order','column_order','extra_column'):
        o=dict(observed)
        for name,item in observed.items():
            t=item['table'];rows=deepcopy(item['rows'])
            if kind=='row_order':o=updated(o,name,list(reversed(rows)))
            elif kind=='column_order':o=updated(o,name,[dict(reversed(list(r.items()))) for r in rows],columns=tuple(reversed(t.columns)),physical_columns=tuple(reversed(t.physical_columns)))
            else:
                for r in rows:r['phase4_extra']='allowed'
                o=updated(o,name,rows,columns=t.columns+('phase4_extra',),physical_columns=t.physical_columns+('phase4_extra',),sql_types={**t.sql_types,'phase4_extra':'VARCHAR'},physical_sql_types={**t.physical_sql_types,'phase4_extra':'VARCHAR'})
        r=score(o);rr=write(out/('legal-'+kind+'-audit.json'),r);controls.append({'kind':kind,'audit':rr,'accepted':r['reward']==1,'derivation':'Declared contract permits physical row/column order and unrequested extra columns; keys, required values, SQL types and multiplicities unchanged.'})
    structural=[]
    first=next(iter(expected(observed)));item=observed[first];t=item['table'];key='customer_id' if 'customer_id' in t.columns else 'cohort_month'
    for kind in ('duplicate','missing_row','missing_column','wrong_type','key_value_association','null_key'):
        rows=deepcopy(item['rows']);kw={}
        if kind=='duplicate':rows.append(deepcopy(rows[0]))
        elif kind=='missing_row':rows.pop()
        elif kind=='missing_column':
            for r in rows:r.pop(key)
            kw=dict(columns=tuple(c for c in t.columns if c!=key),physical_columns=tuple(c for c in t.physical_columns if c!=key),sql_types={k:v for k,v in t.sql_types.items() if k!=key},physical_sql_types={k:v for k,v in t.physical_sql_types.items() if k!=key})
        elif kind=='wrong_type':kw=dict(sql_types={**t.sql_types,key:'DOUBLE'},physical_sql_types={**t.physical_sql_types,key:'DOUBLE'})
        elif kind=='null_key':rows[0][key]=None
        else:rows[0][key],rows[1][key]=rows[1][key],rows[0][key]
        r=score(updated(observed,first,rows,**kw));rr=write(out/('structural-'+kind+'-audit.json'),r);structural.append({'kind':kind,'detected':r['reward']==0,'audit':rr,'derivation':'Explicit identity, grain, required-column and type obligations; value/key swaps preserve column marginals but violate input-derived keyed values.'})
    STAGES['initial']='agent/audit-initial'
    snap,nop=outputs(v,'initial');r=compare_outputs(nop,expected(observed));nopref=write(out/'initial-audit.json',dict(r,candidate_snapshot=snap,task_key='deb__'+task))
    result={'schema_version':1,'task_key':'deb__'+task,'baseline_snapshot':snapshot,'cases':cases,'legal_controls':controls,'structural_cases':structural,
        'initial_audit':nopref,'initial_reward':r['reward'],'passed':all(x['detected'] for x in cases+structural) and all(x['accepted'] for x in controls) and r['reward']==0,
        'code_refs':[ref(Path(__file__)),ref(ROOT/'scripts/phase4_deb_recompute.py')],'model_requests':0,
        'scope':'Actual host-executed audit fixtures backed by immutable formal raw inputs and keyed outputs; one targeted error per rule group plus generic interface regressions. Native case evidence is separately retained or run through Harbor.'}
    write(out/'fixture-review.json',result);return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--batch',type=Path,required=True);p.add_argument('--trial',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=run(a.task,a.batch,a.trial,a.output);print({'task_key':r['task_key'],'passed':r['passed'],'rules':len(r['cases']),'structural':len(r['structural_cases'])})

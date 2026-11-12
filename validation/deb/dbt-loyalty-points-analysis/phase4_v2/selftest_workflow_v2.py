"""New source-declaration and SQL-form gate counterexamples, no model execution."""
from pathlib import Path
from copy import deepcopy
import argparse
from dbt_bench import io
from run_revision_v2 import ROOT,BASE,revision_workflow,walk
from review_support_v4 import verify_trial

def run(task,report,out):
    r=io.read_json(report);v=verify_trial(Path(r['batch']['path']).parent,r['trials'][0]['trial'])
    parser=io.read_json(BASE/'source-parser-v1.json');fixture=io.read_json(BASE/'source-fixtures-v1.json')
    baseline=revision_workflow(task,v,parser,fixture);assert baseline['reward']==1
    cases=[]
    def test(name,verified,parsed,proof):
        answer=revision_workflow(task,verified,parsed,fixture)
        cases.append({'case':name,'independent_proof':proof,'checker_defined_validity':False,
            'reward':answer['reward'],'rejected':answer['reward']==0,
            'failed_checks':[k for k,x in answer['checks'].items() if x['status']=='fail'],
            'scope':'Synthetic trusted-provenance/parsed-AST fixture based on actual successful capture; not claimed a modified model trial.'})
    if task in ('dbt-loyalty-points-analysis','dbt-rfm-customer-segmentation'):
        changed=deepcopy(v)
        for c in changed['commands']:
            if c['manifest']:
                for n in c['manifest']['sources'].values():n['identifier']='wrong_original_input'
        test('wrong_declared_raw_source',changed,parser,'All fresh source identifiers changed away from the explicitly named task sources.')
    if task=='dbt-rfm-customer-segmentation':
        parsed=deepcopy(parser)
        for n in walk(parsed['tasks'][task]['models']['rfm_scores']['ast']):
            if n.get('type')=='WINDOW_NTILE':n['orders']=n['orders'][:1]
        test('missing_ntile_secondary',v,parsed,'Original metric ties exist; removing every secondary key violates explicit deterministic NTILE requirement.')
        parsed=deepcopy(parser)
        for n in walk(parsed['tasks'][task]['models']['rfm_scores']['ast']):
            if n.get('type')=='WINDOW_NTILE':n['type']='WINDOW_ROW_NUMBER'
        test('not_ntile',v,parsed,'Required NTILE(5) replaced with row-number functions even if a crafted static result table were correct.')
    if task=='dbt-customer-lifetime-value':
        parsed=deepcopy(parser)
        for n in walk(parsed['tasks'][task]['models']['int_clv_customer_orders']['ast']):
            if n.get('type')=='WINDOW_LAG':n['type']='WINDOW_LEAD'
        test('lead_instead_of_lag',v,parsed,'LEAD uses the next order; task explicitly requires previous order via LAG.')
    result={'task_key':r['task_key'],'candidate':io.evidence_ref(report,ROOT),'baseline_reward':1,
            'cases':cases,'passed':all(c['rejected'] for c in cases),'model_requests':0,
            'code_refs':[io.evidence_ref(Path(__file__),ROOT),io.evidence_ref(Path(__file__).with_name('run_revision_v2.py'),ROOT)]}
    if out.exists():raise ValueError('Preserve revised workflow fixtures')
    io.write_json(out,result);print(task,result['passed'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    x=p.parse_args();run(x.task,x.report,x.output)

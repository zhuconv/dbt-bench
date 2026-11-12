"""Execute independent, read-only checks on four immutable formal DEB builds."""
from pathlib import Path
from dataclasses import asdict,is_dataclass
from collections import Counter
from copy import deepcopy
import argparse,json,sys,hashlib
from dbt_bench import io
from phase4_capture import verify_trial
from phase4_deb_recompute_v2 import warning,cohorts,rfm2,compare_outputs,tiering_channel_proof
ROOT=Path(__file__).resolve().parents[1]
SUPPORT=ROOT/'validation/deb/dbt-customer-cohort-retention/phase4_v1'
sys.path.insert(0,str(SUPPORT))
from review_support_v3 import raw_rows,outputs,workflow,STAGES

def default(x):
    if is_dataclass(x):return asdict(x)
    if isinstance(x,set):return sorted(x,key=str)
    return str(x)
def write(path,value):
    if path.exists():raise ValueError('Immutable artifact exists: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,sort_keys=True,default=default)+'\n')
    return io.evidence_ref(path,ROOT)
def ref(p):return io.evidence_ref(p,ROOT)
def row_bag(rows):
    return sorted(json.dumps({k:[type(v).__name__,str(v)] for k,v in r.items()},sort_keys=True) for r in rows)
def summary(observed):
    return {n:{'columns':list(v['table'].columns),'types':v['table'].sql_types,'rows':len(v['rows']),
        'bag_hash':io.digest(row_bag(v['rows'])),'relation_type':v['table'].relation_type} for n,v in observed.items() if 'table' in v}

def policy(task):
    models={'dbt-customer-churn-cohorts':['int_analytics__fact_sales','int_customer__customers','customer_cohorts','cohort_retention','customer_clv'],
        'dbt-rfm-customer-segmentation-2':['rfm_segments','rpt_segment_summary','rpt_customer_recommendations','rfm_cohort_retention'],
        'dbt-rfm-customer-tiering':['customer_rfm_scores','customer_tier_transitions','channel_revenue_attribution','customer_cohort_analysis'],
        'dbt-customer-churn-early-warning':['rpt_customer_churn_early_warning_fixed']}[task]
    p={'project_root':'/app/dbt_project' if task.endswith('warning') else '/app/dbt_models_duckdb','database':'/app/database/retail.duckdb','models':models,'profile':'retail_dw_master'}
    if task.endswith('warning'):p.update(profile_schema='analytics',upstream_models=['int_sales__orders_enriched'],required_paths={models[0]:'models/marts/customer/rpt_customer_churn_early_warning_fixed.sql'})
    elif task.endswith('-2'):p['required_paths']={n:'models/marts/rfm/'+n+'.sql' for n in models}
    elif task.endswith('cohorts'):p['required_paths']={n:('models/intermediate/' if n.startswith('int_') else 'models/marts/')+n+'.sql' for n in models}
    return p

def evaluate(task,v,observed,wf):
    raw=raw_rows(v)
    if task.endswith('tiering'):
        answer=tiering_channel_proof(raw,observed);facts={'scope':answer['scope']}
    else:
        function=warning if task.endswith('warning') else cohorts if task.endswith('cohorts') else rfm2
        expected,facts=function(raw,{n:x['rows'] for n,x in observed.items() if 'rows' in x})
        answer=compare_outputs(observed,expected)
    answer.update(workflow=wf)
    if wf['reward']==0:answer['reward']=0
    return answer,facts

def context(v):
    cap=v['capture'];trial=cap.trial;records=[]
    for path in ['agent/audit-initial/snapshot.json','agent/audit-submission/snapshot.json','verifier/mechanical-before/snapshot.json','verifier/mechanical-after/snapshot.json']:
        x=cap.read(trial/path);c=x['context'];records.append({'snapshot':ref(trial/path),'stage':x['stage'],'context':c})
        if c['duckdb_version']!='1.2.2' or c['dbt']['version']['returncode']!=0 or not c['databases'] or any(r['rows']!=[[1]] or not r['readable'] for r in c['databases']):raise ValueError('Actual agent/verifier context did not demonstrate compatible DB access')
    paths={n:trial/'agent/audit-initial-visibility'/(n+'.json') for n in ('facts','scan','structured')};facts,scan,structured=[cap.read(paths[n]) for n in paths]
    visible=all(facts['declared_absent_paths'].values()) and not any(facts['host_paths_visible'].values()) and scan['matches']==[] and scan['errors']==[] and structured==[]
    return {'contexts':records,'visibility':{n:ref(p) for n,p in paths.items()},'visibility_pass':visible,'visibility_scope':scan['scope'],'visibility_exclusions':scan['excluded_roots'],
        'model_requests':cap.read(trial/'agent/no-model.json')['model_requests'],'raw_errors':v['initial'].get('raw_errors',{}),'output_errors':v['final'].get('output_errors',{})}

def run(task,batch,out):
    manifest=io.read_json(batch/'manifest.json');label=next(k for k,v in manifest['tasks'].items() if v['task_key']=='deb__'+task)
    trials=[]
    for p in (batch/'jobs').rglob('result.json'):
        cp=p.with_name('config.json')
        if cp.exists() and Path(io.read_json(cp).get('task',{}).get('path','')).name==label:trials.append(p.parent)
    if len(trials)!=2:raise ValueError('Require two completed formal trials')
    records=[];scorerecords=[]
    for trial in sorted(trials):
        v=verify_trial(batch,trial);env=context(v)
        if env['raw_errors'] or env['output_errors']:raise ValueError('Declared data capture incomplete')
        wf=workflow(v,policy(task));stages=[]
        for stage in STAGES:
            snapshot,o=outputs(v,stage);answer,facts=evaluate(task,v,o,wf)
            answer.update(schema_version=1,task_key='deb__'+task,candidate_snapshot=snapshot,model_requests=0,
                native_rewards_modified=False,input_raw_refs=v['initial']['raw'],actual_execution=ref(trial/'agent/audit-rebuild/execution.json'))
            ar=write(out/trial.name/(stage+'-audit.json'),answer)
            stages.append({'stage':stage,'snapshot':snapshot,'audit_result':ar,'reward':answer['reward'],'outputs':summary(o)})
            scorerecords.append({'trial':trial.name,'stage':stage,'candidate_snapshot':snapshot,'audit_result':ar,'audit_reward':answer['reward'],
                'audit_field_paths':{'reward':['reward'],'candidate_snapshot':['candidate_snapshot'],'task_key':['task_key']}})
        records.append({'trial':trial.name,'label':label,'result':ref(trial/'result.json'),'native_rewards':v['result']['verifier_result']['rewards'],
            'execution':ref(trial/'agent/audit-rebuild/execution.json'),'workflow':wf,'facts':facts,'environment':env,'stages':stages,
            'evidence':list(v['capture'].evidence.values()),'same_stage_values':all(s['outputs']==stages[0]['outputs'] for s in stages)})
    report={'schema_version':1,'task_key':'deb__'+task,'label':label,'batch':ref(batch/'manifest.json'),'records':records,'candidate_scores':scorerecords,
        'repeat_equal':records[0]['stages'][0]['outputs']==records[1]['stages'][0]['outputs'],'all_context_values_equal':all(r['same_stage_values'] for r in records),
        'six_stage_rewards':[r['audit_reward'] for r in scorerecords],'model_requests':0,'native_rewards_modified':False,
        'code_refs':[ref(p) for p in [Path(__file__),ROOT/'scripts/phase4_deb_recompute_v2.py',ROOT/'scripts/phase4_capture.py',ROOT/'scripts/phase3_capture.py',SUPPORT/'review_support_v3.py',ROOT/'scripts/audit_tables.py',ROOT/'src/dbt_bench/comparison.py']]}
    write(out/'fresh-review.json',report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=run(a.task,a.batch,a.output);print({k:r[k] for k in ('task_key','six_stage_rewards','repeat_equal','all_context_values_equal')})

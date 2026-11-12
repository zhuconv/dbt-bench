"""Review current no-model custom witnesses; never turn unresolved meaning into success."""
from pathlib import Path
import sys,json,importlib,argparse,re
from dataclasses import replace
from dbt_bench import io
from phase4_custom_probe_v4 import verify,capture,B,ROOT,runtime,ref,save,snapshot_tables,Capture,outputs
from phase4_customer4_review_v3 import context,summary,write,row_bag
from review_support_v3 import workflow
MAP={'dbt-abc-classification':('02-recapture-v1','phase4_v1','expected_v1','audit_v2',['int_abc_product_revenue','abc_classification','abc_summary','abc_category_breakdown']), 'dbt-gl-reconciliation':('02-v2','audit_phase2_v1','expected_v2','audit_v2',['account_balances','trial_balance','out_of_balance_entries','unusual_balance_accounts','period_summary']), 'dbt-fulfillment-sla':('02-recapture-v1','phase4_v1','expected_v5','audit_v1',['int_shipment_times','fulfillment_sla','carrier_performance','warehouse_performance','fulfillment_summary'])}
def policy(task):
 models=MAP[task][-1];fresh=task=='dbt-gl-reconciliation';p={'project_root':'/app/dbt_project' if fresh else '/app/dbt_models_duckdb','database':'/app/database/retail.duckdb','profile':'dbt_project' if fresh else 'retail_dw_master','models':models}
 if fresh:p['profile_schema']='gl_analytics'
 else:p['required_paths']={n:('models/intermediate/' if n.startswith('int_') else 'models/marts/analytics/')+n+'.sql' for n in models}
 return p

def translate(v):return {'request':v['spec'],'execution':v['execution'],'final_files':v['final_files'],'commands':v['commands'],'backend_shell':v['echo'],'capture':Capture(v['trial'],ROOT),'initial':{},'final':{}}
def main(task,out):
 bn,folder,en,an,models=MAP[task];code=ROOT/'validation/deb'/task/folder;sys.path.insert(0,str(code));e=importlib.import_module(en);a=importlib.import_module(an);batch=B/('phase4-deb-operations-batch'+bn)
 if out.exists():raise ValueError('Immutable review exists')
 out.mkdir(parents=True);trials=[]
 for cp in (batch/'jobs').rglob('config.json'):
  if task in io.read_json(cp).get('task',{}).get('path',''):trials.append(cp.parent)
 if len(trials)!=2:raise ValueError('Two independent actual trials required')
 records=[];scores=[]
 for trial in sorted(trials):
  v=verify(batch,trial);z=translate(v);env=context(z);ic,raw,refs=capture(v,'initial');fc,after,fr=capture(v,'final');raw_equal={n:row_bag(rows) for n,rows in raw.items()}=={n:row_bag(rows) for n,rows in after.items()}
  if not raw_equal:raise ValueError('Input rows changed')
  wf=workflow(z,policy(task));wf['checks']['source_immutability']={'status':'pass','reason':'Declared input row capture before/after actual dbt is identical; all installed author source is actual fresh manifest code, not copied target artifacts.','evidence':refs+fr}
  if task=='dbt-abc-classification':
   # Token review excludes quoted strings/comments, preserving actual function calls.
   functions=[]
   for cmd in v['commands']:
    for uid,node in cmd['manifest']['nodes'].items():
     if node.get('resource_type')!='model' or node.get('name') not in models:continue
     sql=re.sub(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'",' ',node.get('compiled_code',''),flags=re.S)
     functions.extend({'model':node['name'],'function':m.lower(),'manifest':ref(cmd['manifest_path'])} for m in re.findall(r'\b(strftime|to_char)\s*\(',sql,flags=re.I))
   bad=[x for x in functions if x['function']=='strftime'];wf['checks']['explicit_date_format']={'status':'fail' if bad else 'pass' if any(x['function']=='to_char' for x in functions) else 'gap','reason':'Instruction explicitly requires TO_CHAR instead of strftime; actual compiled calls inspected after removing comments/string literals.','observed':functions}
  states=[x['status'] for x in wf['checks'].values()];wf['reward']=0 if 'fail' in states else None if 'gap' in states else 1;wf['status']='scored' if wf['reward'] is not None else 'unknown'
  wr=write(out/trial.name/'workflow.json',wf)
  expected,facts=e.build_variants(raw) if task.endswith('fulfillment-sla') else (e.derive(raw),{})
  if not facts:facts=expected.get('facts',expected.get('profile',{}))
  er=write(out/trial.name/'independent-expected.json',{'task_key':'deb__'+task,'expected':expected,'source_refs':refs,'code':ref(code/(en+'.py')),'model_requests':0})
  stages=[]
  for stage in ['agent-submission','verifier-before','verifier-after']:
   sr,o=outputs(z,stage);component={'status':'pass' if wf['reward']==1 else 'fail' if wf['reward']==0 else 'gap','reason':'Actual fresh project/backend/profile/model source-process checks','evidence':wr}
   if task.endswith('fulfillment-sla'):
    compiled={}
    for name,c in fc.get('compiled',{}).items():
     p=runtime(trial,c);compiled[name]=[dict(zip(c['columns'],map(__import__('audit_tables').decode,json.loads(line)))) for line in p.read_text().splitlines()]
    ar=a.evaluate('fulfillment',o,expected,workflow=component,compiled=compiled)
   else:ar=a.evaluate(o,expected,component)
   ar.update(task_key='deb__'+task,candidate_snapshot=sr,independent_expected=er,workflow_result=wr);rr=write(out/trial.name/(stage+'-audit.json'),ar)
   stages.append({'stage':stage,'snapshot':sr,'audit_result':rr,'reward':ar['reward'],'outputs':summary(o)})
   scores.append({'trial':trial.name,'stage':stage,'candidate_snapshot':sr,'audit_result':rr,'audit_reward':ar['reward'],'audit_field_paths':{'reward':['reward'],'candidate_snapshot':['candidate_snapshot'],'task_key':['task_key']}})
  records.append({'trial':trial.name,'label':v['label'],'result':ref(trial/'result.json'),'native_rewards':v['result']['verifier_result']['rewards'],'execution':ref(trial/'agent/audit-rebuild/execution.json'),'workflow':wr,'facts':facts,'environment':env,'stages':stages,'raw_unchanged':raw_equal,'raw_refs':refs+fr,'same_stage_values':all(s['outputs']==stages[0]['outputs'] for s in stages)})
 result={'task_key':'deb__'+task,'batch':ref(batch/'manifest.json'),'label':records[0]['label'],'records':records,'candidate_scores':scores,'repeat_equal':records[0]['stages'][0]['outputs']==records[1]['stages'][0]['outputs'],'all_context_values_equal':all(r['same_stage_values'] for r in records),'six_stage_rewards':[r['audit_reward'] for r in scores],'model_requests':0,'code_refs':[ref(p) for p in [Path(__file__),ROOT/'scripts/phase4_custom_probe_v4.py',ROOT/'scripts/phase4_customer4_review_v3.py',ROOT/'scripts/phase4_capture.py',ROOT/'scripts/phase3_capture.py',code/(en+'.py'),code/(an+'.py'),ROOT/'scripts/audit_tables.py',ROOT/'src/dbt_bench/comparison.py',ROOT/'validation/deb/dbt-customer-cohort-retention/phase4_v1/review_support_v3.py',B/'phase4-review-v1/operations/actual_core_v2.py']]}
 write(out/'fresh-review.json',result);print({k:result[k] for k in ['task_key','six_stage_rewards','repeat_equal','all_context_values_equal']})
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--output',type=Path,required=True);x=p.parse_args();main(x.task,x.output)

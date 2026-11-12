"""Check actual-workflow gate with independently constructed provenance omissions."""
from pathlib import Path
from copy import deepcopy
import argparse,json
from dbt_bench import io
from review_support_v4 import verify_trial,workflow
R=Path.cwd()
def run(report,out):
 r=io.read_json(report);v=verify_trial(Path(r['batch']['path']).parent,r['trials'][0]['trial']);policy=r['trials'][0]['workflow']['policy'];baseline=workflow(v,policy);cases=[]
 def evaluate(name,mutate,target,proof):
  item=deepcopy(v);mutate(item);a=workflow(item,policy);cases.append({'case':name,'independent_proof':proof,'tested_checker_invoked_to_define_validity':False,'actual_check':a['checks'][target],'rejected':a['checks'][target]['status']=='fail','scope':'Synthetic provenance fixture over verified actual transcript, not a fresh benchmark trial.'})
 evaluate('wrong_project',lambda x:x['request'].__setitem__('project_root','/app/wrong_project'),'project','Required actual project path replaced with distinct path')
 evaluate('wrong_profile_directory',lambda x:x['execution']['environment'].__setitem__('DBT_PROFILES_DIR','/app/wrong_profiles'),'profile','Actual profile environment points elsewhere')
 evaluate('wrong_backend',lambda x:x['backend_shell'].__setitem__('output','snowflake\n'),'backend','Actual backend read disagrees with pinned DuckDB run')
 required=policy['models'][0];found=next(c for c in reversed(v['commands']) if c['manifest'] is not None and c['run']['command'][1] in ('run','build') and '--project-dir' not in c['run']['command']);node=next(n for n in found['manifest']['nodes'].values() if n.get('resource_type')=='model' and n['name']==required);path=node['original_file_path']
 evaluate('missing_model_source',lambda x:x['final_files'].pop(path),'build','Required manifest model exists but its actual submitted SQL file is absent')
 def failed_result(x):
  c=next(c for c in reversed(x['commands']) if c['manifest'] is not None and c['run']['command'][1] in ('run','build') and '--project-dir' not in c['run']['command']);uid=next(uid for uid,n in c['manifest']['nodes'].items() if n.get('resource_type')=='model' and n['name']==required)
  for item in c['run_results']['results']:
   if item['unique_id']==uid:item['status']='error'
 evaluate('failed_required_model',failed_result,'build','Fresh actual required model status changed from success to error')
 result={'schema_version':1,'task_key':r['task_key'],'candidate_record':io.evidence_ref(report,R),'checker':io.evidence_ref(Path(__file__).with_name('review_support_v4.py'),R),'selftest':io.evidence_ref(Path(__file__),R),'baseline_workflow_reward':baseline['reward'],'cases':cases,'passed':all(c['rejected'] for c in cases),'model_requests':0}
 if out.exists():raise ValueError('Immutable workflow selftest exists')
 out.write_text(json.dumps(result,indent=2)+'\n');print(r['task_key'],result['passed'])
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.report,a.output)

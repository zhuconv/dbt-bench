"""Evaluate actual clean initial captures with no submitted-build witness."""
from pathlib import Path
import argparse,json
from dbt_bench import io
from run_components_v7 import load,expected_for,evaluate
import review_support_v4 as support
R=Path.cwd()
def run(report,out):
 r=io.read_json(report);task=r['task_key'].removeprefix('deb__');e,a,code=load(task);rows=[];support.STAGES['initial']='agent/audit-initial'
 for trial in r['trials']:
  v=support.verify_trial(Path(r['batch']['path']).parent,trial['trial']);expected,raw=expected_for(task,e,a,v);snapshot,o=support.outputs(v,'initial');answer=evaluate(task,a,o,expected,{'status':'fail','reason':'Actual observation was recorded before source installation and before all dbt commands; no delivered-build witness exists at this stage.'});rows.append({'trial':trial['trial'],'snapshot':snapshot,'audit':answer})
 value={'task_key':r['task_key'],'candidate':io.evidence_ref(report,R),'checks':rows,'model_requests':0,'scope':'Real independently initialized agent filesystem before submission installation. This audits the actual initial snapshots; native nop rewards are separately retained.'}
 if out.exists():raise ValueError('Immutable nop audit exists')
 out.write_text(json.dumps(value,indent=2,default=str)+'\n');print(r['task_key'],[x['audit']['reward'] for x in rows])
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.report,a.output)

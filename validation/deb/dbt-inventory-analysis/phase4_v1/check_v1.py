"""New profile interpretation only; legacy table and model checks are reused unchanged."""
import argparse,importlib.util,json
from pathlib import Path
HERE=Path(__file__).parent;OLD=HERE.parent/'audit_phase2_v1'
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
legacy=load('legacy_check',OLD/'check_v2.py');original_module=legacy.module
legacy.module=lambda name:load('phase4_project_workflow',HERE/'project_workflow_v1.py')if name=='project_workflow_v2'else original_module(name)
def run(request,root):
 result=legacy.run(request,root);result['checker_version']='phase4-selected-runtime-profile-v1';return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise ValueError('Existing result')
 try:result=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as exc:result={'task_key':'deb__'+HERE.parent.name,'target':'audit','status':'unknown','reward':None,'reason':str(exc),'evidence':json.loads(a.request.read_text())}
 a.output.write_text(json.dumps(result,indent=2)+'\n')

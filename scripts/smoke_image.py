#!/usr/bin/env python3
"""CPU reference smoke on the target architecture; never a model measurement."""
import argparse,json,platform,re,shutil,subprocess,sys
from copy import deepcopy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from dbt_bench import io
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',required=True);p.add_argument('--task-tree',type=Path,required=True)
p.add_argument('--sif',type=Path,required=True);p.add_argument('--sif-sha256',required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if platform.machine()!='x86_64':raise ValueError('Run this smoke on the target amd64 CPU host')
row=next(v for v in io.read_json(ROOT/'catalog/measurement-tasks.json')['tasks'] if v['task_key']==a.task)
if io.tree_hash(a.task_tree)!=row['task_tree_sha256'] or io.file_hash(a.sif)!=a.sif_sha256:raise ValueError('Task/image checksum differs')
out=a.output.resolve()
if out.exists():raise ValueError('Use a new smoke directory')
shutil.copytree(a.task_tree,out/'task',symlinks=True);toml=out/'task/task.toml'
s,n=re.subn(r'(?m)^docker_image\s*=\s*.*$', 'docker_image = '+json.dumps(str(a.sif.resolve())),toml.read_text())
if n!=1:raise ValueError('Image coordinate missing')
toml.write_text(s);c=deepcopy(row['config_template']);c.update(job_name='reference-smoke',jobs_dir=str(out/'jobs'),n_attempts=1)
c['tasks'][0]['path']=str(out/'task');c['agents']=[{'name':'oracle'}];io.write_json(out/'config.yaml',c)
result=subprocess.run([str(Path(sys.executable).with_name('harbor')),'run','-c',str(out/'config.yaml')],stdout=(out/'harbor.log').open('w'),stderr=subprocess.STDOUT)
trials=list((out/'jobs/reference-smoke').glob('*/result.json'));rewards=[(io.read_json(p).get('verifier_result') or {}).get('rewards') for p in trials]
passed=result.returncode==0 and len(rewards)==1 and rewards[0].get('reward')==1 if rewards and rewards[0] else False
io.write_json(out/'smoke-result.json',{'reference_passed':passed,'native_rewards':rewards,'harbor_returncode':result.returncode,'sif_sha256':a.sif_sha256,
 'model_requests':0,'cross_architecture_equivalence_test':False,'scope':'Target CPU environment/reference smoke only; no measurement or audit qualification'})
raise SystemExit(0 if passed else 1)

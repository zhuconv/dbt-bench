#!/usr/bin/env python3
"""Cancel the actual locked Qwen shell service without making model requests."""
import argparse,asyncio,json,logging,os,shlex,sys,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from dbt_bench.backends import harbor_process_env
from check_runtime_timeout_v2 import phase,noop
from native_mechanical_agent import SERVICE

async def main(args):
 from harbor.environments.singularity.singularity import SingularityEnvironment
 from harbor.agents.installed.qwen_code import QwenCode
 from harbor.models.task.task import Task
 from harbor.models.trial.paths import TrialPaths
 from harbor.trial.trial import Trial
 from harbor.trial.errors import AgentTimeoutError
 out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);task=Task(args.task);paths=TrialPaths(trial_dir=out/'environment');paths.mkdir()
 logger=logging.getLogger('qwen-timeout-v2');logger.setLevel(logging.DEBUG);handler=logging.FileHandler(out/'runtime.log');logger.addHandler(handler)
 os.environ['PATH']=harbor_process_env('apptainer')['PATH'];bundle=ROOT/'.cache/qwen-runtime/locked-v1/bundle.json'
 env=SingularityEnvironment(environment_dir=task.paths.environment_dir,environment_name='qwen-timeout-v2',session_id=out.name,trial_paths=paths,task_env_config=task.config.environment.model_copy(update={'docker_image':str(args.image.resolve())}),logger=logger,persistent_env={'DB_TYPE':'duckdb'},singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
 qwen=QwenCode(logs_dir=paths.agent_dir,model_name='openai/no-model-requests',version='0.26.5',logger=logger,installation_bundle=bundle,installation_bundle_sha256=io.file_hash(bundle))
 result={'model_requests':0,'scope':'Locked Qwen setup and actual ShellExecutionService only; QwenCode.run/model clients never called','script':io.evidence_ref(Path(__file__),ROOT),'bundle':io.evidence_ref(bundle,ROOT),'checks':[],'passed':False}
 try:
  await asyncio.wait_for(env.start(force_build=False),300);await asyncio.wait_for(qwen.setup(env),360)
  service=await qwen.exec_as_agent(env,command='sha256sum '+shlex.quote(SERVICE),timeout_sec=10);assert service.return_code==0;result['service']=service.stdout.strip()
  for pty in (False,True):
   stem='pty' if pty else 'pipe'
   code="import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('/app/qwen-child.pid').write_text(str(os.getpid()));p=Path('/app/qwen-submission');\nwhile True:\n p.write_text(str(time.monotonic()));time.sleep(.02)"
   command='/usr/local/bin/python3 -c '+shlex.quote(code)
   script=out/(stem+'.mjs');script.write_text('import { ShellExecutionService } from '+json.dumps(SERVICE)+';\nconst h=await ShellExecutionService.execute('+json.dumps(command)+',"/app",()=>{},AbortSignal.timeout(60000),'+str(pty).lower()+',{},{});await h.result;\n');await env.upload_file(script,'/tmp/qwen-cancel.mjs')
   async def action(**kw):return await qwen.exec_as_agent(env,command='. ~/.nvm/nvm.sh; node /tmp/qwen-cancel.mjs')
   caught=None;began=time.monotonic()
   try:
    with patch('harbor.trial.sync_trajectory.sync_trajectory',noop):
     await Trial._run_agent_phase(phase(env,action),target=SimpleNamespace(),instruction='CPU no-model shell cancellation',timeout_sec=2,user=None)
   except AgentTimeoutError as exc:caught=type(exc).__name__
   before=await env.exec('cat /app/qwen-submission');await asyncio.sleep(.2);after=await env.exec('cat /app/qwen-submission')
   check="from pathlib import Path;p=Path('/proc')/Path('/app/qwen-child.pid').read_text()/'stat';print('stopped' if not p.exists() or p.read_text().rsplit(')',1)[1].split()[0]=='Z' else 'alive')"
   alive=await env.exec('/usr/local/bin/python3 -c '+shlex.quote(check));await env.download_file('/app/qwen-submission',out/(stem+'-submission.txt'))
   row={'pty':pty,'exception':caught,'elapsed_seconds':time.monotonic()-began,'child_status':alive.stdout,'submission':io.evidence_ref(out/(stem+'-submission.txt'),ROOT),'passed':caught=='AgentTimeoutError' and bool(before.stdout) and before.stdout==after.stdout and alive.stdout=='stopped' and (out/(stem+'-submission.txt')).read_text()==after.stdout};result['checks'].append(row);io.write_json(out/'result.json',result);print(json.dumps(row),flush=True);assert row['passed']
  result['passed']=True
 except Exception as exc:
  import traceback
  result['error']={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
 finally:
  try:await asyncio.wait_for(env.stop(delete=True),30)
  except Exception as exc:result['cleanup_error']=str(exc);result['passed']=False
  handler.close();logger.removeHandler(handler);io.write_json(out/'result.json',result)
 print(json.dumps({'passed':result['passed'],'error':result.get('error')}),flush=True);return 0 if result['passed'] else 1
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',type=Path,required=True);p.add_argument('--image',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();raise SystemExit(asyncio.run(main(a)))

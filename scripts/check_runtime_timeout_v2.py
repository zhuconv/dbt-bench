#!/usr/bin/env python3
"""CPU-only real Apptainer phase, cancellation, and capture integration."""
import argparse
import asyncio
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shlex
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench import io
from dbt_bench.backends import harbor_process_env


@asynccontextmanager
async def noop(*args,**kwargs):yield


def phase(env,action):
    async def emitted(*args):pass
    return SimpleNamespace(agent=SimpleNamespace(run=action,extra_env={}),user_agent=None,agent_environment=env,
        _emit=emitted,_now=lambda:datetime.now(timezone.utc),_configure_web_tools=emitted,
        _network_plan=lambda *a,**k:SimpleNamespace(agent_env_baseline=None,agent_phase=None),
        _phase_network_policy=noop,_log_context=lambda *a,**k:nullcontext(),paths=SimpleNamespace(agent_dir=Path('/tmp')))


async def main(args):
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.trial.trial import Trial
    from harbor.trial.errors import AgentTimeoutError,VerifierTimeoutError
    import harbor
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    os.environ['PATH']=harbor_process_env('apptainer')['PATH']
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    task=Task(task_dir=args.task);environments=[];checks=[]
    logger=logging.getLogger('timeout-v2');logger.setLevel(logging.DEBUG)
    handler=logging.FileHandler(out/'runtime.log');logger.addHandler(handler)
    result={'model_requests':0,'scope':'Actual CPU Apptainer execution with Harbor phase wrappers; synthetic commands, not benchmark/model trials',
        'runtime_package':str(Path(harbor.__file__).parent),'script':io.evidence_ref(Path(__file__),ROOT),'checks':checks,'passed':False}
    def checkpoint():io.write_json(out/'result.json',result)
    def record(name,passed,**facts):
        checks.append(dict(name=name,passed=passed,**facts));checkpoint();print(json.dumps(checks[-1]),flush=True)
        if not passed:raise AssertionError(name)
    async def start(name):
        paths=TrialPaths(trial_dir=out/name);paths.mkdir()
        env=SingularityEnvironment(environment_dir=task.paths.environment_dir,environment_name='timeout-v2',session_id=name,
            trial_paths=paths,task_env_config=task.config.environment.model_copy(update={'docker_image':str(args.image.resolve())}),
            logger=logger,persistent_env={'DB_TYPE':'duckdb'},singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
        environments.append(env);await asyncio.wait_for(env.start(force_build=False),300);return env
    try:
        agent,verifier=await asyncio.gather(start('agent-context'),start('verifier-context'))
        async def long_agent():
            async def action(**kw):
                r=await agent.exec('printf before > /app/timeout-submission; sleep '+str(args.long_seconds)+'; printf after >> /app/timeout-submission')
                if r.return_code:raise AssertionError(r)
            began=time.monotonic()
            with patch('harbor.trial.sync_trajectory.sync_trajectory',noop):
                await Trial._run_agent_phase(phase(agent,action),target=SimpleNamespace(),instruction='CPU fixture',timeout_sec=4000,user=None)
            took=time.monotonic()-began
            captured=await agent.exec('cat /app/timeout-submission')
            record('agent_exec_exceeds_600_under_protocol_4000',took>=args.long_seconds and captured.stdout=='beforeafter',elapsed_seconds=took,phase_budget_sec=4000)
        async def long_verifier():
            async def action():return await verifier.exec('sleep '+str(args.long_seconds)+'; printf native-fixture-finished')
            began=time.monotonic();r=await Trial._verify_with_timeout(None,SimpleNamespace(verify=action),3000)
            took=time.monotonic()-began
            record('verifier_exec_exceeds_600_under_protocol_3000',took>=args.long_seconds and r.stdout=='native-fixture-finished',elapsed_seconds=took,phase_budget_sec=3000)
        await asyncio.gather(long_agent(),long_verifier())
        for role,env in (('agent',agent),('verifier',verifier)):
            child="import signal,time,os;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('/app/child.pid').write_text(str(os.getpid()));p=Path('/app/partial-submission');\nwhile True:\n p.write_text(str(time.monotonic()));time.sleep(.02)"
            parent="import subprocess,sys,time,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);subprocess.Popen([sys.executable,'-c',%r],start_new_session=True);time.sleep(60)"%child
            async def action(**kw):return await env.exec('/usr/local/bin/python3 -c '+shlex.quote(parent))
            began=time.monotonic();caught=None
            try:
                if role=='agent':
                    with patch('harbor.trial.sync_trajectory.sync_trajectory',noop):
                        await Trial._run_agent_phase(phase(env,action),target=SimpleNamespace(),instruction='CPU cancellation fixture',timeout_sec=1,user=None)
                else:await Trial._verify_with_timeout(None,SimpleNamespace(verify=action),1)
            except (AgentTimeoutError,VerifierTimeoutError) as e:caught=type(e).__name__
            value=await env.exec('cat /app/partial-submission');await asyncio.sleep(.2)
            after=await env.exec('cat /app/partial-submission')
            check="from pathlib import Path;p=Path('/proc')/Path('/app/child.pid').read_text()/'stat';print('stopped' if not p.exists() or p.read_text().rsplit(')',1)[1].split()[0]=='Z' else 'alive')"
            alive=await env.exec('/usr/local/bin/python3 -c '+shlex.quote(check))
            # Exercise the actual collection entrypoint before stopping container.
            await env.exec('cp /app/partial-submission /tmp/timeout-captured',timeout_sec=10)
            await env.download_file('/tmp/timeout-captured',out/(role+'-captured.txt'))
            record(role+'_budget_cancel_stops_detached_child_then_collects',caught==('AgentTimeoutError' if role=='agent' else 'VerifierTimeoutError') and bool(value.stdout) and value.stdout==after.stdout and alive.stdout=='stopped' and (out/(role+'-captured.txt')).read_text()==value.stdout,
                exception=caught,elapsed_seconds=time.monotonic()-began,child_status=alive.stdout,capture=io.evidence_ref(out/(role+'-captured.txt'),ROOT))
        # An HTTP client disappearing is not a command completion or a budget.
        import httpx
        ident='disconnect-fixture';url=f'http://localhost:{agent._server_port}'
        try:
            async with httpx.AsyncClient(timeout=.25) as c:
                await c.post(url+'/exec',json={'exec_id':ident,'command':'echo $$ > /app/disconnect.pid; sleep 60','timeout_sec':None})
        except httpx.ReadTimeout:pass
        await asyncio.sleep(.3)
        ack=await agent._http_client.post(url+'/exec/'+ident+'/cancel',timeout=10)
        record('connection_loss_cancel_ack',ack.status_code==200 and ack.json().get('stopped') is True)
        record('independent_environment_still_usable',(await verifier.exec('printf peer-alive')).stdout=='peer-alive')
        result['passed']=all(x['passed'] for x in checks)
    except Exception as e:
        import traceback
        result['error']={'type':type(e).__name__,'message':str(e),'traceback':traceback.format_exc()}
    finally:
        for env in environments:
            try:await asyncio.wait_for(env.stop(delete=True),30)
            except Exception as e:result.setdefault('cleanup_errors',[]).append(str(e));result['passed']=False
        handler.close();logger.removeHandler(handler);checkpoint()
    print(json.dumps({'passed':result['passed'],'error':result.get('error'),'output':str(out)}),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',type=Path,required=True);p.add_argument('--image',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--long-seconds',type=int,default=610)
    a=p.parse_args();raise SystemExit(asyncio.run(main(a)))

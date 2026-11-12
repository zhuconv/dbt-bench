#!/usr/bin/env python3
"""Exercise pinned native Qwen installation in a fresh Harbor task environment.

No model requests, task solutions or hidden tests are used. This records the
adapter launch context and resolved installation; it is not a Qwen tool smoke
or a frozen installation dependency lock.
"""
from pathlib import Path
import argparse,asyncio,importlib.metadata,importlib.util,json,logging,os,shlex,shutil,sys,traceback
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.backends import binding_identity,harbor_process_env,resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref,file_hash,read_json,write_json

INVENTORY=r'''
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys
out=Path('/logs/agent/installation-inventory');out.mkdir(exist_ok=False)
def command(argv):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=180)
 return {'argv':argv,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
commands={name:command(argv) for name,argv in {
 'node':['node','--version'],'npm':['npm','--version'],'qwen':['qwen','--version'],
 'npm_root':['npm','root','-g'],'npm_dependencies':['npm','ls','-g','--all','--json']}.items()}
for name,result in commands.items():(out/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n')
assert all(commands[k]['returncode']==0 for k in ['node','npm','qwen','npm_root'])
assert commands['qwen']['stdout'].strip()=='0.26.5'
package_root=Path(commands['npm_root']['stdout'].strip())
package=package_root/'@qwen-code/qwen-code';metadata=json.loads((package/'package.json').read_text())
assert metadata['version']=='0.26.5'
files=[]
for p in sorted(package.rglob('*')):
 if p.is_file():files.append({'path':str(p.relative_to(package)),'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
for label,p in [('qwen-package.json',package/'package.json'),('npm-package-lock.json',package_root/'.package-lock.json')]:
 if p.is_file():shutil.copyfile(p,out/label)
nvm=Path.home()/'.nvm'
revision=command(['git','-C',str(nvm),'rev-parse','HEAD']) if (nvm/'.git').exists() else None
runtime={name:{'path':shutil.which(name),'resolved':str(Path(shutil.which(name)).resolve()),
              'sha256':hashlib.sha256(Path(shutil.which(name)).read_bytes()).hexdigest()}
         for name in ['node','npm','qwen']}
report={'qwen_version':metadata['version'],'runtime':runtime,'nvm_revision':revision,
        'nvm_script_sha256':hashlib.sha256((nvm/'nvm.sh').read_bytes()).hexdigest(),
        'qwen_package_files':files,'dependency_tree_command_succeeded':commands['npm_dependencies']['returncode']==0,
        'scope':'Observed installation inventory; fixed-bundle identity, when used, is in the outer probe result'}
(out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'qwen_version':metadata['version'],'package_files':len(files),
                  'node':commands['node']['stdout'].strip(),'npm':commands['npm']['stdout'].strip(),
                  'dependency_tree_command_succeeded':report['dependency_tree_command_succeeded']}))
'''


async def probe(args):
    from harbor.agents.installed.qwen_code import QwenCode
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    if importlib.metadata.version('harbor')!='0.25.0':raise ValueError('Expected Harbor 0.25.0')
    if args.candidate_adapter:
        if not args.candidate_sha256 or file_hash(args.candidate_adapter)!=args.candidate_sha256:
            raise ValueError('Candidate adapter checksum required and must match')
        spec=importlib.util.spec_from_file_location('qwen_install_candidate',args.candidate_adapter)
        candidate=importlib.util.module_from_spec(spec);sys.modules[spec.name]=candidate;spec.loader.exec_module(candidate)
        QwenCode=candidate.QwenCode
    row=next(r for r in load_catalog(ROOT) if r['task_key']==args.task)
    task_path=resolve_task(ROOT,row,'apptainer')
    task=Task(task_dir=task_path)
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(Path(__file__),out/'script.py')
    result={'task_key':args.task,'started_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Native Qwen installation and adapter launch context only; no model or shell-tool invocation',
        'qwen_smoke_executed':False,'benchmark_trials':0,'checks':[],
        'source_lock':evidence_ref(ROOT/'sources.lock.yaml',ROOT),'script':evidence_ref(out/'script.py',ROOT),
        'backend_binding':binding_identity(ROOT,row,'apptainer')}
    package=Path(importlib.import_module('harbor').__file__).parent
    result['adapter_sources']=[evidence_ref(package/name,ROOT) for name in [
        'agents/installed/qwen_code.py','agents/installed/base.py','agents/installed/node_install.py']]
    if args.candidate_adapter:
        result['candidate_adapter']=evidence_ref(args.candidate_adapter,ROOT)
        result['production_adapter_unchanged']=True
    options={}
    if args.installation_bundle:
        if not args.bundle_sha256 or file_hash(args.installation_bundle)!=args.bundle_sha256:
            raise ValueError('Explicit installation bundle checksum required and must match')
        options={'installation_bundle':args.installation_bundle.resolve(),
                 'installation_bundle_sha256':args.bundle_sha256}
        result['installation_bundle']=evidence_ref(args.installation_bundle,ROOT)
    logger=logging.getLogger('qwen-setup-probe');logger.setLevel(logging.DEBUG)
    handler=logging.FileHandler(out/'environment.log');logger.addHandler(handler)
    paths=TrialPaths(trial_dir=out/'environment');paths.mkdir()
    os.environ['PATH']=harbor_process_env('apptainer')['PATH']
    env=SingularityEnvironment(environment_dir=task.paths.environment_dir,environment_name='qwen-setup-probe',
        session_id=out.name,trial_paths=paths,task_env_config=task.config.environment,
        persistent_env={'DB_TYPE':'duckdb'},logger=logger,
        singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
    agent=QwenCode(logs_dir=paths.agent_dir,model_name='openai/setup-probe-no-requests',version='0.26.5',logger=logger,**options)
    async def record(name,command,via_agent=False):
        execution=await (agent.exec_as_agent(env,command=command,timeout_sec=240) if via_agent
                         else env.exec(command,timeout_sec=240))
        value={'name':name,'command':command,'result':execution.model_dump(mode='json')}
        write_json(out/(name+'.json'),value);result['checks'].append(evidence_ref(out/(name+'.json'),ROOT))
        assert execution.return_code==0,name
        print(name+': passed',flush=True)
        return execution
    try:
        await asyncio.wait_for(env.start(force_build=False),timeout=300)
        if task.config.environment.healthcheck:
            await record('native-healthcheck',task.config.environment.healthcheck.command)
        await env.upload_file(ROOT/'scripts/context_probe.py','/tmp/dbt-bench-context-probe.py')
        command='python3 /tmp/dbt-bench-context-probe.py --db '+shlex.quote(args.db)
        before=await record('before-install-context',command)
        code=out/'inventory.py';code.write_text(INVENTORY)
        await env.upload_file(code,'/tmp/dbt-bench-qwen-inventory.py')
        await record('before-database-hash','sha256sum '+shlex.quote(args.db))
        print('native-qwen-setup: starting',flush=True)
        await asyncio.wait_for(agent.setup(env),timeout=1200)
        print('native-qwen-setup: completed',flush=True)
        if args.installation_bundle:
            # Exercise the same adapter path again, including its version check.
            # This verifies idempotence after NVM has actually been sourced.
            await asyncio.wait_for(agent.setup(env),timeout=1200)
            print('native-qwen-setup-replay: completed',flush=True)
            result['setup_replay_passed']=True
        after=await record('adapter-launch-context','. ~/.nvm/nvm.sh; '+command,True)
        if args.tool_context:
            # Invoke the actual locked CLI shell service, not a replacement shell
            # or model loop. Cover both native execution branches explicitly.
            service = '/root/.nvm/versions/node/v22.23.4/lib/node_modules/@qwen-code/qwen-code/chunks/chunk-NMY3PYNO.js'
            tool_cwd = json.loads(after.stdout)['cwd']
            if not isinstance(tool_cwd, str) or not tool_cwd.startswith('/'):
                raise ValueError('Missing absolute adapter working directory')
            result['native_tool_working_directory'] = tool_cwd
            tool_probe = out/'native-tool-context.mjs'
            tool_probe.write_text(
                'import { ShellExecutionService } from '+json.dumps(service)+';\n'
                'import { readFileSync } from "node:fs";\n'
                'import { createHash } from "node:crypto";\n'
                'const command='+json.dumps(command)+';\n'
                'const pty=process.argv[2]==="pty";\n'
                'const handle=await ShellExecutionService.execute(command,'+json.dumps(tool_cwd)+',()=>{},AbortSignal.timeout(120000),pty,{},{});\n'
                'const execution=await handle.result;\n'
                'console.log(JSON.stringify({requested_pty:pty,execution_method:execution.executionMethod,exit_code:execution.exitCode,'
                'service_sha256:createHash("sha256").update(readFileSync('+json.dumps(service)+')).digest("hex"),'
                'context:JSON.parse(execution.output)}));\n'
                'if(execution.exitCode!==0)process.exitCode=1;\n')
            await env.upload_file(tool_probe,'/tmp/native-tool-context.mjs')
            for method in ['pipe','pty']:
                await record('native-tool-'+method+'-context','. ~/.nvm/nvm.sh; node /tmp/native-tool-context.mjs '+method,True)
            result['native_tool_context_probe']=evidence_ref(tool_probe,ROOT)
            result['scope']='Native Qwen installation, launch context and actual locked shell-service pipe/PTY probes; no model requests in this diagnostic'
        await record('installation-inventory','. ~/.nvm/nvm.sh; python3 /tmp/dbt-bench-qwen-inventory.py',True)
        await record('after-database-hash','sha256sum '+shlex.quote(args.db))
        await record('known-hidden-paths','test ! -e /solution && test ! -e /tests')
        for data in [json.loads(before.stdout),json.loads(after.stdout)]:
            assert not data.get('duckdb_error') and data['databases'] and not any(d.get('error') for d in data['databases'])
        before_hash=read_json(out/'before-database-hash.json')['result']['stdout'].split()[0]
        after_hash=read_json(out/'after-database-hash.json')['result']['stdout'].split()[0]
        result['initial_database_unchanged']=before_hash==after_hash
        assert before_hash==after_hash
        result['installation_passed']=True
    except Exception as exc:
        result.update(installation_passed=False,error={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
    finally:
        try:
            await env.download_dir('/logs/agent',out/'agent-logs')
        except Exception as exc:result['download_error']=str(exc)
        try:await env.stop(delete=True)
        except Exception as exc:result['cleanup_error']=str(exc)
        handler.flush();handler.close();logger.removeHandler(handler)
        result['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        result['environment_log']=evidence_ref(out/'environment.log',ROOT)
        result['passed']=result.get('installation_passed',False) and not result.get('download_error') and not result.get('cleanup_error')
        write_json(out/'result.json',result)
    print(json.dumps({'passed':result['passed'],'qwen_smoke_executed':False,'output':str(out/'result.json')}))
    return 0 if result['passed'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',required=True)
    parser.add_argument('--db',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--candidate-adapter',type=Path,help='Explicit test-only adapter source; installed Harbor is unchanged')
    parser.add_argument('--candidate-sha256')
    parser.add_argument('--installation-bundle',type=Path)
    parser.add_argument('--bundle-sha256')
    parser.add_argument('--tool-context',action='store_true',help='Also execute the read-only probe through the locked Qwen 0.26.5 shell service')
    return asyncio.run(probe(parser.parse_args()))


if __name__=='__main__':raise SystemExit(main())

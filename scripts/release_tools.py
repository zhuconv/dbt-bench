"""Portable execution coordinates around the same finite native Harbor jobs.

No scoring definition, model parameters, task budgets, attempt counts or seeds
are supplied by local deployment settings. Central import verifies accepted ledger results separately.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from dbt_bench import io
from dbt_bench.local_config import ROOT, required


def asset_index():
    return {r['id']:r for r in io.read_json(ROOT/'release/assets.json')['assets']}


def asset(asset_id, settings, *, verify=True):
    index=asset_index()
    if asset_id in index:entry=index[asset_id]
    else:
        from platform_support import platform_name
        matches=[v for v in io.read_json(ROOT/'images/platform-images.json')['images'] if v.get('asset_id')==asset_id and v.get('status')=='published' and v['platform']==platform_name(settings)]
        if len(matches)!=1:raise ValueError('Unknown published platform asset')
        entry={'hash_algorithm':'sha256','sha256':matches[0]['sif_sha256']}
    mapping=io.read_json(settings['asset_map'])
    if asset_id not in mapping:raise ValueError('Missing immutable asset mapping: '+asset_id)
    path=Path(mapping[asset_id]).resolve()
    if verify:
        actual=io.tree_hash(path) if entry['hash_algorithm'].endswith('tree_hash') else io.file_hash(path)
        if actual!=entry['sha256']:raise ValueError('Asset hash differs: '+asset_id)
    return path


def task_index():
    return {r['task_key']:r for r in io.read_json(ROOT/'catalog/measurement-tasks.json')['tasks']}


def runtime_check(settings):
    import harbor
    package=Path(harbor.__file__).parent.resolve()
    if Path(sys.executable).absolute()!=Path(settings['runtime_python']).absolute():
        raise ValueError('Use the configured patched runtime Python')
    from platform_support import runtime_inventory
    inventory=runtime_inventory(settings,ROOT)
    for row in io.read_json(inventory):
        if io.file_hash(package/row['path'])!=row['sha256']:
            raise ValueError('Runtime content differs: '+row['path'])
    return {'harbor_version':__import__('importlib.metadata',fromlist=['version']).version('harbor'),'python_version':sys.version,'package_inventory':io.evidence_ref(inventory,ROOT),
            'python':settings['runtime_python'],'package':str(package)}


def same_task_semantics(original, relocated):
    old,new=tomllib.loads(original),tomllib.loads(relocated)
    old['environment']['docker_image']='coordinate';new['environment']['docker_image']='coordinate'
    if old!=new:raise ValueError('Relocation altered native task semantics')


def config_for(template, run, task, settings):
    c=deepcopy(template)
    c['job_name']=Path(run).name;c['jobs_dir']=str(Path(run)/'jobs');c['tasks'][0]['path']=str(task)
    # Build provenance changes the manifest hash. Reuse the existing strict
    # payload/lock verifier, then bind this exact manifest for native Harbor.
    from platform_support import bind_bundle
    bind_bundle(c, settings, ROOT)
    return c


def qwen_installation_identity(config):
    """Payload equivalence evidence, separate from an execution/manifest ID.

    Call after config_for has verified every payload against sources.lock.yaml.
    This field alone does not authorize cross-account result import.
    """
    kwargs=config['agents'][0]['kwargs']
    manifest=io.read_json(kwargs['installation_bundle'])
    content={key:manifest[key] for key in ('kind','platform','qwen_version','node_version','npm_version')}
    content['files']={name:manifest['files'][name]['sha256'] for name in ('archive','inventory','installer')}
    content['locks']=manifest['locks']
    return {'schema_version':1,'policy':'exact_locked_payload_v1',
            'content':content,'content_id':io.digest(content),
            'manifest_sha256':kwargs['installation_bundle_sha256']}


def seal(path, settings):
    files=[ROOT/'scripts/platform_support.py',ROOT/'scripts/run_release_batch.py',ROOT/'scripts/release_tools.py',ROOT/'src/dbt_bench/io.py',
           ROOT/'src/dbt_bench/local_config.py',ROOT/'src/dbt_bench/prepare.py',
           ROOT/'src/dbt_bench/catalog.py',ROOT/'src/dbt_bench/backends.py',
           *sorted((ROOT/'runtime/qwen-code').glob('*.json')),ROOT/'scripts/run_gh_smoke.py',ROOT/'scripts/verify_container_cleanup.py',
           ROOT/'scripts/review_tools.py',ROOT/'release/harbor-package-files.json',
           ROOT/'release/batches.json',ROOT/'catalog/measurement-tasks.json',ROOT/'release/assets.json',
           ROOT/'configs/difficulty-v1/measurement-rules.json',ROOT/'configs/difficulty-v1/split-rules.json',
           ROOT/'catalog/splits.json',ROOT/'sources.lock.yaml',
           ROOT/'inference/qwen3.6-35b-a3b.json',ROOT/'inference/qwen-code-settings.json',
           ROOT/'inference/uv.lock',ROOT/'runtime/uv.lock',ROOT/'release/model-files.json']
    files+=sorted((ROOT/'runtime/qwen-code/linux-x64').glob('*.json'))
    files += [ROOT/'release/harbor-amd64-package-files.json',ROOT/'inference/x86_64/uv.lock']
    files+=[ROOT/'images/platform-images.json'] if (ROOT/'images/platform-images.json').exists() else []
    io.write_json(path/'preflight.json',{'manifest':io.evidence_ref(path/'manifest.json',ROOT),
        'files':[io.evidence_ref(p,ROOT) for p in files], 'deployment':settings,'training_prohibited':True})


def preflight(directory, root=ROOT):
    evidence=io.read_json(directory/'preflight.json')
    if evidence['deployment']!=required():raise ValueError('Local deployment changed after preparation')
    for ref in [evidence['manifest'],*evidence['files']]:
        if not io.evidence_ok(ref,ROOT):raise ValueError('Prepared dependency changed: '+ref['path'])


def validate_member(entry, batch, identity, root=ROOT):
    run=Path(entry['run']);preflight(run)
    if (run/'launch.json').exists():raise ValueError('Existing member invocation must not be restarted')
    if not io.evidence_ok(entry['manifest'],ROOT):raise ValueError('Member manifest changed')
    m=io.read_json(run/'manifest.json');i=m['identity'];c=io.read_json(run/'config.yaml');row=task_index()[i['task_key']]
    from platform_support import image_identity, code_identity
    if (i['image_identity']!=image_identity(row,required(),ROOT) or i['qualified_review_asset']!=row['qualified_review_asset']
            or i['code_identity']['content_id']!=code_identity(ROOT)['content_id']):
        raise ValueError('Prepared image/code/qualification binding changed')
    if (io.digest(i)!=m['experiment_id'] or io.file_hash(run/'config.yaml')!=m['config_hash']
            or c!=i['config'] or c!=config_for(row['config_template'],run,i['config']['tasks'][0]['path'],required())
            or i['attempt_ids']!=[1,2,3,4] or i['server_seed']!=identity['server_seed']
            or i['shared_model_batch']!=str(batch) or i['task_version']!=row['task_version']
            or i['qwen_installation']!=qwen_installation_identity(c)):
        raise ValueError('Member protocol/configuration changed')
    task=Path(c['tasks'][0]['path'])
    if io.tree_hash(task)!=i['actual_task_hash'] or io.tree_hash(task/'tests')!=row['native_tests_sha256']:
        raise ValueError('Relocated task or native verifier changed')
    asset(i['image_identity']['asset_id'],required())
    return m,c,{}


def validate_batch(directory, root=ROOT, *, claimed_state=None, journal=None):
    directory=Path(directory);preflight(directory)
    if claimed_state is None and any((directory/n).exists() for n in ('launch.json','launch-result.json','runtime-status.json')):
        raise ValueError('Already started; prepare a separate adjudicated recovery, never restart')
    if claimed_state is not None and io.read_json(directory/'launch.json')!=claimed_state:raise ValueError('Launch claim changed')
    m=io.read_json(directory/'manifest.json');i=m['identity'];runtime_check(required())
    template=next(b for b in io.read_json(ROOT/'release/batches.json')['batches'] if b['id']==i['batch_id'])
    if io.digest(i)!=m['experiment_id'] or i['server_seed']!=template['server_seed'] or i['walltime_sec']!=template['walltime_sec'] or not m['training_prohibited']:
        raise ValueError('Batch protocol identity changed')
    planned=[];keys=[]
    for entry in i['members']:
        if journal is not None:journal.active=Path(entry['run'])
        member,_,_=validate_member(entry,directory,i);planned+=member['planned'];keys.append(member['identity']['task_key'])
        if journal is not None:journal.active=None
    if keys!=template['task_keys'] or planned!=i['planned'] or len({(r['task_key'],r['attempt']) for r in planned})!=len(planned):
        raise ValueError('Member order or planned slots changed')
    return m


def allocation(job,expected_walltime):
    settings=required()
    raw=subprocess.run(['scontrol','show','job','-o',job],capture_output=True,text=True,check=True,timeout=30).stdout
    f=dict(x.split('=',1) for x in raw.split() if '=' in x)
    from run_release_batch import slurm_seconds
    actual=slurm_seconds(f.get('TimeLimit'))
    if f.get('Account','').casefold()!=settings['allocation'].casefold() or f.get('Partition')!=settings['partition'] or actual!=expected_walltime or f.get('NumNodes')!='1' or f.get('NumTasks')!='1':
        raise ValueError('Actual allocation differs from prepared local deployment')
    return {'raw':raw,'fields':f,'actual_walltime_sec':actual,'time_min_sec':None,'policy':'exact prepared deployment'}


def environment(directory,job,root=ROOT):
    settings=required();env={**os.environ,'OPENAI_API_KEY':'local-no-auth','PYTHONDONTWRITEBYTECODE':'1',
        'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'scripts'),'APPTAINER_TMPDIR':'/tmp/dbt-difficulty-'+job,'OMP_NUM_THREADS':'8',
        'HF_HOME':str(Path(settings['cache_root'])/'huggingface'),'APPTAINER_CACHEDIR':str(Path(settings['cache_root'])/'apptainer')}
    env.pop('LD_PRELOAD',None)
    env['PATH']=str(Path(settings['runtime_python']).parent)+os.pathsep+settings['apptainer_bin']+os.pathsep+env.get('PATH','')
    Path(env['APPTAINER_TMPDIR']).mkdir(exist_ok=True)
    for name in ('HF_HOME','APPTAINER_CACHEDIR'):Path(env[name]).mkdir(parents=True,exist_ok=True)
    return env


def model_environment(directory,env,root=ROOT):
    s=required();result={**env,'CC':s['cc'],'CXX':s['cxx'],'CUDA_HOME':s['cuda_home'],'CUDA_PATH':s['cuda_home'],
        'PATH':str(Path(s['vllm_executable']).parent)+os.pathsep+str(Path(s['cuda_home'])/'bin')+os.pathsep+env['PATH'],
        'VLLM_CACHE_ROOT':str(directory/'cache/vllm'),'TRITON_CACHE_DIR':str(directory/'cache/triton'),
        'FLASHINFER_WORKSPACE_BASE':str(directory/'cache/flashinfer'),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
    for name in ('VLLM_CACHE_ROOT','TRITON_CACHE_DIR','FLASHINFER_WORKSPACE_BASE'):Path(result[name]).mkdir(parents=True,exist_ok=True)
    return result


def model_command(directory,manifest,root=ROOT):
    import hashlib
    s=required();spec=io.read_json(ROOT/'inference/qwen3.6-35b-a3b.json');model=io.read_json(ROOT/'release/model-files.json');snapshot=Path(s['model_snapshot'])
    if model['revision']!=spec['revision']:raise ValueError('Model revision differs')
    for item in model['files']:
        p=snapshot/item['path']
        if p.stat().st_size!=item['size']:raise ValueError('Model size differs: '+item['path'])
        actual=io.file_hash(p) if 'sha256' in item else hashlib.sha1(f'blob {p.stat().st_size}\0'.encode()+p.read_bytes()).hexdigest()
        if actual!=item.get('sha256',item.get('git_blob_sha1')):raise ValueError('Model content differs: '+item['path'])
    inference_python=Path(s['vllm_executable']).with_name('python')
    probe='import importlib.metadata as m,json,platform,sys;print(json.dumps({"python":sys.version,"machine":platform.machine(),"packages":{n:m.version(n) for n in ("vllm","torch","torchaudio","torchvision")}}))'
    versions=json.loads(subprocess.run([str(inference_python),'-c',probe],check=True,capture_output=True,text=True,timeout=60).stdout)
    expected={'vllm':'0.19.0','torch':'2.10.0+cu128','torchaudio':'2.10.0+cu128','torchvision':'0.25.0+cu128'}
    if versions['packages']!=expected:raise ValueError('Actual inference package versions differ')
    from platform_support import PLATFORMS,platform_name
    if versions['machine']!=PLATFORMS[platform_name(s)]:raise ValueError('Inference runtime architecture differs')
    io.write_json(directory/'inference-runtime.json',versions)
    command=[s['vllm_executable'],'serve',str(snapshot),'--served-model-name',spec['served_model_name'],*spec['server_args'],
        '--override-generation-config',json.dumps(spec['generation_defaults']),'--seed',str(manifest['identity']['server_seed'])]
    io.write_json(directory/'server-command.json',{'argv':command,'model_inventory':io.evidence_ref(ROOT/'release/model-files.json',ROOT),
        'shared_model_service':True,'model_requests_outside_measurement':0,'training_prohibited':True})
    return command,spec['served_model_name']


def prepare(batch_id,output):
    settings=required()
    from platform_support import check_host, image_identity, code_identity
    check_host(settings);runtime=runtime_check(settings);code=code_identity(ROOT);template=next(b for b in io.read_json(ROOT/'release/batches.json')['batches'] if b['id']==batch_id)
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new output directory')
    output.mkdir(parents=True)
    refs=[];planned=[];rows=task_index()
    for key in template['task_keys']:
        row=rows[key];source=asset(row['task_asset'],settings);image_id=image_identity(row,settings,ROOT);image=asset(image_id['asset_id'],settings)
        run=output/'members'/key;run.mkdir(parents=True);task=run/'task';shutil.copytree(source,task,symlinks=True)
        before=(task/'task.toml').read_text()
        after,n=re.subn(r'(?m)^docker_image\s*=\s*.*$', 'docker_image = '+json.dumps(str(image)),before)
        if n!=1:raise ValueError('Expected exactly one native image coordinate')
        same_task_semantics(before,after);(task/'task.toml').write_text(after)
        config=config_for(row['config_template'],run,task,settings)
        from harbor.models.job.config import JobConfig
        JobConfig.model_validate(config)
        identity={'code_identity':code,'image_identity':image_id,'qualified_review_asset':row['qualified_review_asset'],'task_key':key,'task_version':row['task_version'],'config':config,'attempt_ids':[1,2,3,4],
            'shared_model_batch':str(output),'server_seed':template['server_seed'],'runtime_patch':runtime,
            'qwen_installation':qwen_installation_identity(config),
            'canonical_task_hash':row['task_tree_sha256'],'actual_task_hash':io.tree_hash(task),
            'deployment':settings,'purpose':'difficulty_only_no_training','training_prohibited':True,
            'relocation':{'changed_files':['task.toml'] if before!=after else [],'allowed_field':'environment.docker_image',
                'before_task_toml_sha256':io.file_hash(source/'task.toml'),'after_task_toml_sha256':io.file_hash(task/'task.toml')}}
        slots=[{'task_key':key,'attempt':n} for n in (1,2,3,4)]
        io.write_json(run/'config.yaml',config)
        io.write_json(run/'manifest.json',{'schema_version':1,'identity':identity,'experiment_id':io.digest(identity),
            'config_hash':io.file_hash(run/'config.yaml'),'planned':slots,'training_prohibited':True})
        seal(run,settings);refs.append({'run':str(run),'manifest':io.evidence_ref(run/'manifest.json',ROOT)});planned+=slots
    identity={'batch_id':batch_id,'server_seed':template['server_seed'],'walltime_sec':template['walltime_sec'],'members':refs,
        'runtime_patch':runtime,'planned':planned,'deployment':settings,'source_experiment_id':template['source_experiment_id']}
    io.write_json(output/'manifest.json',{'schema_version':1,'identity':identity,'experiment_id':io.digest(identity),
        'purpose':'difficulty_only_no_training','training_prohibited':True})
    seal(output,settings);validate_batch(output)
    return output

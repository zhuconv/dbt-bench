"""Immutable worker export and accepted-main import. No scoring or execution."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dbt_bench import io

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'distributed-import-v1-user-accepted-amd64-arm64'


def git(repo, *args):
    return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()


def normalized_config(config):
    c=deepcopy(config)
    c['job_name']='JOB';c['jobs_dir']='JOBS'
    c['tasks'][0]['path']='TASK'
    k=c['agents'][0]['kwargs']
    k['installation_bundle']='BUNDLE';k['installation_bundle_sha256']='MANIFEST'
    return c


def checked_manifest(path):
    m=io.read_json(path)
    if io.digest(m['identity'])!=m['experiment_id']:
        raise ValueError('Manifest identity hash differs')
    return m


def duration(section):
    if not section or not section.get('started_at') or not section.get('finished_at'):return None
    def timestamp(v):
        if isinstance(v,(int,float)):return v
        d=datetime.fromisoformat(v.replace('Z','+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    value=timestamp(section['finished_at'])-timestamp(section['started_at'])
    if value<0:raise ValueError('Negative execution duration')
    return value


def task_slots(run, task_key):
    """Sequential, no-retry Harbor trials; never assign past an unexplained hole."""
    m=checked_manifest(run/'manifest.json');launch=io.read_json(run/'launch-result.json')
    c=io.read_json(run/'config.yaml')
    if c['n_concurrent_trials']!=1 or c['retry']['max_retries']!=0 or c['n_attempts']!=4:
        raise ValueError('Slot mapping requires four sequential attempts without retries')
    if not launch.get('finished_at') or launch.get('finalization_status')!='recorded_after_cleanup':
        raise ValueError('Member is not finalized; launch-result alone is not sufficient')
    paths=list((run/'jobs'/c['job_name']).glob('*/result.json'))
    pairs=[(p,io.read_json(p)) for p in paths]
    pairs.sort(key=lambda x:x[1].get('started_at') or '')
    if c!=m['identity']['config'] or io.file_hash(run/'config.yaml')!=m['config_hash']:
        raise ValueError('Native job config changed')
    for path,value in pairs:
        trial=value.get('config') or {}
        if (trial.get('task')!=c['tasks'][0] or trial.get('agent')!=c['agents'][0]
                or trial.get('environment')!=c['environment'] or trial.get('verifier')!=c['verifier']
                or any(trial.get(k)!=c[k] for k in ('timeout_multiplier','agent_timeout_multiplier',
                    'verifier_timeout_multiplier','agent_setup_timeout_multiplier','environment_build_timeout_multiplier','install_only'))):
            raise ValueError('Actual native trial task/agent/environment/budget differs')
    if len(pairs)>4 or len({v.get('id') for _,v in pairs})!=len(pairs):
        raise ValueError('Duplicate or excess native trials')
    if any(not v.get('id') or not v.get('started_at') or not v.get('finished_at') for _,v in pairs):
        raise ValueError('Incomplete native result; owner must adjudicate slot mapping')
    dirs={p.parent for p,_ in pairs}
    orphan=[p for p in (run/'jobs'/c['job_name']).iterdir() if p.is_dir() and p not in dirs] if (run/'jobs'/c['job_name']).is_dir() else []
    if orphan:raise ValueError('Unfinished trial directories; preserve raw member for owner adjudication')
    for (_,a),(_,b) in zip(pairs,pairs[1:]):
        if a['finished_at']>b['started_at']:raise ValueError('Overlapping/ambiguous sequential trials')
    result=[]
    for n in range(1,5):
        if n>len(pairs):
            result.append({'slot':n,'status':'not_started','native_reward':None,'exception_info':None,
                           'duration_sec':None,'submission':None,'audit_reward':None,'joint_success':None})
            continue
        p,v=pairs[n-1];submission=p.parent/'artifacts/difficulty-submission'
        capture_status='missing';capture_error=None
        if submission.is_dir():
            from difficulty_submission import verify_capture
            try:verify_capture(p.parent,task_key);capture_status='verified'
            except (ValueError,KeyError,OSError) as exc:capture_status='invalid';capture_error=str(exc)
        result.append({'slot':n,'status':'terminal','trial_id':v['id'],'trial_name':v.get('trial_name'),
            'native_result':str(p.relative_to(run)), 'native_result_sha256':io.file_hash(p),
            'native_reward':(v.get('verifier_result') or {}).get('rewards'),'task_checksum':v.get('task_checksum'),'agent_info':v.get('agent_info'),
            'exception_info':v.get('exception_info'),'error_policy':v.get('error_policy'),
            'termination':{k:v[k] for k in v if 'termination' in k or 'valid' in k},
            'started_at':v.get('started_at'),'finished_at':v.get('finished_at'),
            'duration_sec':duration(v),'phase_duration_sec':{k:duration(v.get(k)) for k in
                ('environment_setup','agent_setup','agent_execution','verifier')},
            'submission':{'path':str(submission.relative_to(run)),'tree_sha256':io.tree_hash(submission),
                'capture_status':capture_status,'capture_error':capture_error} if submission.is_dir() else None,
            'audit_reward':None,'joint_success':None,'validity':'pending_central_adjudication'})
    return result


def bundle_proof(bundle, destination):
    m=io.read_json(bundle);destination.mkdir(parents=True)
    shutil.copyfile(bundle,destination/'bundle.json')
    # The huge install archive is reconstructible; retain exact inventory and installer.
    for name in ('inventory','installer'):
        item=m['files'][name];p=io.contained(bundle.parent,item['path'])
        if io.file_hash(p)!=item['sha256']:raise ValueError('Qwen payload changed')
        shutil.copyfile(p,destination/Path(item['path']).name)


def safe_pack(stage, target):
    def normalize(info):
        info.uid=info.gid=info.mtime=0;info.uname=info.gname='';info.pax_headers={};return info
    # tree_manifest rejects escaping symlinks. tar never follows symlinks.
    io.tree_manifest(stage)
    with target.open('xb') as raw, gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w',dereference=False) as tar:
            for p in sorted(stage.iterdir()):tar.add(p,arcname=p.name,filter=normalize)


def safe_unpack(archive, destination, *, max_bytes=100*1024**3):
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();names=set();total=0
        for m in members:
            name=PurePosixPath(m.name)
            if (name.is_absolute() or '..' in name.parts or str(name)!=m.name or m.name in names
                    or not (m.isfile() or m.isdir() or m.issym())):
                raise ValueError('Unsafe/duplicate archive member')
            names.add(m.name);total+=m.size
            if total>max_bytes:raise ValueError('Archive expanded size exceeds limit')
            if m.issym():
                link=PurePosixPath(m.linkname)
                if link.is_absolute():raise ValueError('Absolute archive symlink')
                resolved=(destination/m.name).parent.joinpath(m.linkname).resolve()
                if not resolved.is_relative_to(destination.resolve()):raise ValueError('Escaping archive symlink')
        # Disallow writing through links, even when both paths remain inside the archive.
        links={m.name for m in members if m.issym()}
        if any(any(str(p) in links for p in PurePosixPath(m.name).parents) for m in members):
            raise ValueError('Archive writes through a symlink')
        tar.extractall(destination,filter='data')


def export_task(batch, key, output, root=ROOT):
    from release_tools import asset, task_index
    batch=Path(batch).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Export is immutable; use a new directory')
    b=checked_manifest(batch/'manifest.json');entries=[]
    for entry in b['identity']['members']:
        m=checked_manifest(Path(entry['run'])/'manifest.json')
        if m['identity']['task_key']==key:entries.append((entry,m))
    if len(entries)!=1:raise ValueError('Task must occur exactly once in batch')
    entry,m=entries[0];run=Path(entry['run']);i=m['identity'];row=task_index()[key]
    if io.file_hash(run/'manifest.json')!=entry['manifest']['sha256']:raise ValueError('Member changed')
    slots=task_slots(run,key)
    if io.tree_hash(run/'task')!=i['actual_task_hash']:raise ValueError('Prepared task changed')
    # Catch drift before creating any public output; the archive carries these bindings.
    from platform_support import bind_bundle, image_identity
    check=deepcopy(i['config']);bind_bundle(check,i['deployment'],root)
    if check!=i['config'] or i['image_identity']!=image_identity(row,i['deployment'],root):
        raise ValueError('Prepared Qwen/image identity changed')
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='.export-',dir=output) as d:
        stage=Path(d);shutil.copytree(run,stage/'member',symlinks=True)
        (stage/'batch').mkdir();(stage/'proof').mkdir()
        (stage/'proof/batch-members').mkdir()
        for position,other in enumerate(b['identity']['members']):
            source_manifest=Path(other['run'])/'manifest.json'
            if io.file_hash(source_manifest)!=other['manifest']['sha256']:raise ValueError('Batch member manifest changed')
            shutil.copyfile(source_manifest,stage/'proof/batch-members'/f'{position:03}.json')
        # Only task-independent launch metadata; no live model cache or other tasks.
        for p in batch.iterdir():
            if p.is_file() and (p.suffix in ('.json','.log','.out','.err','.py','.txt','.sh')):
                shutil.copyfile(p,stage/'batch'/p.name)
        source=asset(row['task_asset'],i['deployment'])
        shutil.copyfile(source/'task.toml',stage/'proof/canonical-task.toml')
        bundle=Path(i['config']['agents'][0]['kwargs']['installation_bundle'])
        bundle_proof(bundle,stage/'proof/qwen')
        from platform_support import runtime_inventory
        runtime_rel=str(runtime_inventory(i['deployment'],root).relative_to(root))
        for path in ('release/model-files.json','inference/qwen3.6-35b-a3b.json',runtime_rel):
            target=stage/'proof'/Path(path).name;shutil.copyfile(root/path,target)
        # Source receipt was sealed at preparation, not guessed from current HEAD.
        code=i.get('code_identity')
        if not code:raise ValueError('Missing prepared code identity; historical runs need an explicit legacy adapter')
        summary={'schema_version':1,'format':'dbt-bench-task-export-v1','batch':b['identity']['batch_id'],
            'task_key':key,'task_version':i['task_version'],'slots':slots,
            'identity':{'experiment_id':m['experiment_id'],'batch_experiment_id':b['experiment_id'],
                'code':code,'platform':i['deployment'].get('platform','linux-arm64'),
                'model_revision':io.read_json(root/'release/model-files.json')['revision'],
                'runtime':i['runtime_patch'],'inference_runtime':io.read_json(batch/'inference-runtime.json'),'qwen_installation':i['qwen_installation'],
                'image':i['image_identity'],'server_seed':i['server_seed'],
                'native_budgets':row['native_budgets'],'qualified_review_asset':i['qualified_review_asset'],'canonical_task_sha256':row['task_tree_sha256']},
            'member_launch':io.read_json(run/'launch-result.json'),
            'audit_status':'pending_central_same_submission_audit',
            'architecture_policy':'user_accepts_amd64_arm64_equivalence_without_cross_arch_validation',
            'training_prohibited':True}
        io.write_json(stage/'summary.json',summary)
        io.write_json(stage/'inventory.json',io.tree_manifest(stage))
        archive=output/(summary['batch']+'--'+key+'.tar.gz');safe_pack(stage,archive)
    io.write_json(output/'summary.json',summary)
    receipt={'summary':str(output/'summary.json'),'archive':str(archive),'sha256':io.file_hash(archive),
             'bytes':archive.stat().st_size,'training_prohibited':True}
    io.write_json(output/'export-receipt.json',receipt);return receipt


def verify_import(stage, summary, batch, row, root=ROOT):
    """Validate bytes and protocol independently of worker-calculated scores/IDs."""
    inventory=io.read_json(stage/'inventory.json')
    actual=[v for v in io.tree_manifest(stage) if v['path']!='inventory.json']
    if actual!=inventory:raise ValueError('Archive file inventory mismatch')
    embedded=io.read_json(stage/'summary.json')
    external={k:v for k,v in summary.items() if k not in ('worker','published_at','raw_archive')}
    if embedded!=external:raise ValueError('Published summary differs from archived summary')
    run=stage/'member';m=checked_manifest(run/'manifest.json');i=m['identity']
    b=checked_manifest(stage/'batch/manifest.json');bi=b['identity'];key=row['task_key']
    if (summary['batch']!=batch['id'] or summary['task_key']!=key or i['task_key']!=key
            or i['task_version']!=row['task_version'] or i['qualified_review_asset']!=row['qualified_review_asset'] or i['canonical_task_hash']!=row['task_tree_sha256']
            or bi['server_seed']!=batch['server_seed'] or i['server_seed']!=batch['server_seed']
            or bi['walltime_sec']!=batch['walltime_sec'] or i['attempt_ids']!=[1,2,3,4]
            or not m['training_prohibited'] or not summary['training_prohibited']):
        raise ValueError('Task/version/seed/slot identity differs')
    if len(bi['members'])!=len(batch['task_keys']):raise ValueError('Batch member count differs')
    for position,(entry,expected_key) in enumerate(zip(bi['members'],batch['task_keys'])):
        path=stage/'proof/batch-members'/f'{position:03}.json';other=checked_manifest(path)
        if (io.file_hash(path)!=entry['manifest']['sha256'] or other['identity']['task_key']!=expected_key
                or other['identity']['server_seed']!=batch['server_seed'] or other['identity']['attempt_ids']!=[1,2,3,4]):
            raise ValueError('Actual batch member order or seed differs')
    expected=[{'task_key':k,'attempt':n} for k in batch['task_keys'] for n in (1,2,3,4)]
    if bi['planned']!=expected or m['planned']!=[v for v in expected if v['task_key']==key]:
        raise ValueError('Declared ordered batch/slots differ')
    members=[v for v in bi['members'] if v['manifest']['sha256']==io.file_hash(run/'manifest.json')]
    if len(members)!=1:raise ValueError('Batch does not bind member')
    config=io.read_json(run/'config.yaml')
    if (config!=i['config'] or io.file_hash(run/'config.yaml')!=m['config_hash']
            or normalized_config(config)!=normalized_config(row['config_template'])):
        raise ValueError('Protocol config/budgets/sampling differs')
    if io.tree_hash(run/'task')!=i['actual_task_hash'] or io.tree_hash(run/'task/tests')!=row['native_tests_sha256']:
        raise ValueError('Task/native verifier content differs')
    from release_tools import same_task_semantics
    canonical=stage/'proof/canonical-task.toml'
    same_task_semantics(canonical.read_text(),(run/'task/task.toml').read_text())
    tree=io.tree_manifest(run/'task')
    for v in tree:
        if v['path']=='task.toml':v['sha256']=io.file_hash(canonical)
    if io.digest(tree)!=row['task_tree_sha256']:raise ValueError('Canonical task/data mismatch')
    from platform_support import runtime_inventory
    runtime_rel=str(runtime_inventory(i['deployment'],root).relative_to(root))
    for rel in ('release/model-files.json','inference/qwen3.6-35b-a3b.json',runtime_rel):
        if io.file_hash(stage/'proof'/Path(rel).name)!=io.file_hash(root/rel):raise ValueError('Model/runtime lock differs')
    from platform_support import verify_import_platform
    verify_import_platform(i,stage/'proof/qwen',row,root)
    si=summary['identity']
    if (si['code']!=i['code_identity'] or si['image']!=i['image_identity']
            or si['runtime']!=i['runtime_patch'] or si['qwen_installation']!=i['qwen_installation']
            or si['platform']!=i['deployment'].get('platform','linux-arm64')
            or si['qualified_review_asset']!=row['qualified_review_asset'] or si['native_budgets']!=row['native_budgets'] or si['server_seed']!=batch['server_seed']
            or si['canonical_task_sha256']!=row['task_tree_sha256']
            or si['model_revision']!=io.read_json(root/'release/model-files.json')['revision']):
        raise ValueError('Summary identity differs from verified raw identity')
    spec=io.read_json(root/'inference/qwen3.6-35b-a3b.json')
    server=io.read_json(stage/'batch/server-command.json')
    expected=[i['deployment']['vllm_executable'],'serve',i['deployment']['model_snapshot'],
        '--served-model-name',spec['served_model_name'],*spec['server_args'],
        '--override-generation-config',json.dumps(spec['generation_defaults']),'--seed',str(batch['server_seed'])]
    actual=list(server['argv'])
    try:
        index=expected.index('--override-generation-config')+1
        actual[index]=json.dumps(json.loads(actual[index]),sort_keys=True)
        expected[index]=json.dumps(json.loads(expected[index]),sort_keys=True)
    except (IndexError,TypeError,ValueError):raise ValueError('Invalid server generation config')
    if actual!=expected:raise ValueError('Actual server command/model/sampling/seed differs')
    runtime=io.read_json(stage/'batch/inference-runtime.json')
    from platform_support import PLATFORMS
    packages={'vllm':'0.19.0','torch':'2.10.0+cu128','torchaudio':'2.10.0+cu128','torchvision':'0.25.0+cu128'}
    if runtime!=si['inference_runtime'] or runtime['packages']!=packages or runtime['machine']!=PLATFORMS[si['platform']]:
        raise ValueError('Inference runtime/version/platform mismatch')
    if task_slots(run,key)!=summary['slots']:raise ValueError('Slots differ from actual native results/submissions')
    if io.read_json(run/'launch-result.json')!=summary['member_launch']:raise ValueError('Member launch facts differ')
    if summary['identity']['experiment_id']!=m['experiment_id'] or summary['identity']['batch_experiment_id']!=b['experiment_id']:
        raise ValueError('Summary execution IDs differ')
    return m


def download_raw(raw, target):
    url=raw.get('url','');sha=raw.get('sha256','');size=raw.get('bytes')
    if (not re.fullmatch(r'https://github\.com/[^/]+/[^/]+/releases/download/raw-v1/[^/]+',url)
            or not re.fullmatch('[0-9a-f]{64}',sha) or type(size)!=int or size<=0
            or not url.endswith('/'+raw.get('name',''))):raise ValueError('Invalid raw archive locator')
    with urllib.request.urlopen(url,timeout=120) as response,target.open('xb') as f:
        total=0
        while chunk:=response.read(4*1024*1024):
            total+=len(chunk)
            if total>size:raise ValueError('Archive exceeds declared size')
            f.write(chunk)
    if total!=size or io.file_hash(target)!=sha:raise ValueError('Raw archive checksum/size mismatch')


def import_ledger(repo, output, remote='upstream', root=ROOT):
    repo=Path(repo).resolve();output=Path(output).resolve()
    if not re.fullmatch('[A-Za-z0-9_-]+',remote):raise ValueError('Invalid remote name')
    url=git(repo,'remote','get-url',remote).removesuffix('.git')
    if url not in ('https://github.com/zhuconv/dbt-bench','git@github.com:zhuconv/dbt-bench'):
        raise ValueError('Import only owner main; fork/PR worktrees are not acceptance')
    # Caller fetches first. Read committed blobs exclusively, never working-tree JSON.
    commit=git(repo,'rev-parse',f'refs/remotes/{remote}/main^{{commit}}')
    names=git(repo,'ls-tree','-r','--name-only',commit,'ledger/results').splitlines()
    batches={b['id']:b for b in io.read_json(root/'release/batches.json')['batches']}
    rows={r['task_key']:r for r in io.read_json(root/'catalog/measurement-tasks.json')['tasks']}
    output.mkdir(parents=True,exist_ok=True);receipts=[]
    import fcntl
    with (output/'.import.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        for name in names:
            match=re.fullmatch(r'ledger/results/(batch-\d+)/([^/]+)/summary.json',name)
            if not match:raise ValueError('Unexpected ledger result path')
            bid,key=match.groups();summary=json.loads(git(repo,'show',commit+':'+name))
            if bid not in batches or key not in batches[bid]['task_keys']:raise ValueError('Unassigned task')
            worker=summary.get('worker','')
            if not re.fullmatch('[A-Za-z0-9._-]+',worker):raise ValueError('Invalid worker')
            claim=json.loads(git(repo,'show',f'{commit}:ledger/claims/{bid}/{worker}.json'))
            if claim.get('batch')!=bid or claim.get('worker')!=worker or claim.get('state') not in ('running','finished'):
                raise ValueError('Accepted summary has no matching executed owner claim')
            dest=output/bid/key;summary_hash=io.digest(summary)
            if dest.exists():
                old=io.read_json(dest/'import-receipt.json')
                if old['summary_sha256']!=summary_hash:raise ValueError('Duplicate/conflicting slot ownership')
                if io.file_hash(dest/'raw.tar.gz')!=summary['raw_archive']['sha256']:
                    raise ValueError('Previously imported archive changed')
                verify_import(dest/'raw',summary,batches[bid],rows[key],root)
                receipts.append(old);continue
            with tempfile.TemporaryDirectory(prefix='.import-',dir=output) as d:
                temp=Path(d);download_raw(summary['raw_archive'],temp/'raw.tar.gz')
                raw=temp/'raw';raw.mkdir();safe_unpack(temp/'raw.tar.gz',raw)
                manifest=verify_import(raw,summary,batches[bid],rows[key],root)
                receipt={'schema_version':1,'policy':POLICY,'accepted_main_commit':commit,
                    'ledger_path':name,'summary_sha256':summary_hash,'worker':worker,'batch':bid,'task_key':key,
                    'experiment_id':manifest['experiment_id'],'slots':[1,2,3,4],
                    'raw_sha256':summary['raw_archive']['sha256'],'audit_status':'pending_central_same_submission_audit',
                    'qualified_review_asset':rows[key]['qualified_review_asset'],'training_prohibited':True}
                io.write_json(temp/'accepted-summary.json',summary);io.write_json(temp/'import-receipt.json',receipt)
                io.write_json(temp/'path-map.json',{'policy':'companion_only_original_files_unchanged',
                    'original_member':manifest['identity']['config']['jobs_dir'].removesuffix('/jobs'),
                    'local_member':str(dest/'raw/member'),
                    'original_batch':manifest['identity'].get('shared_model_batch'),
                    'local_batch_snapshot':str(dest/'raw/batch')})
                dest.parent.mkdir(parents=True,exist_ok=True);os.rename(temp,dest)
                receipts.append(receipt)
        records=[]
        for receipt in receipts:
            path=output/receipt['batch']/receipt['task_key'];s=io.read_json(path/'accepted-summary.json')
            for slot in s['slots']:
                records.append({**slot,'batch':receipt['batch'],'task_key':receipt['task_key'],
                    'attempt':slot['slot'],'worker':receipt['worker'],'experiment_id':receipt['experiment_id'],
                    'trial_path':str(path/'raw/member'/Path(slot['native_result']).parent) if slot.get('native_result') else None,
                    'import_receipt':str(path/'import-receipt.json'),'training_prohibited':True})
        trial_ids=[r['trial_id'] for r in records if r.get('trial_id')]
        if len(trial_ids)!=len(set(trial_ids)):raise ValueError('Duplicate trial IDs across accepted tasks')
        io.write_jsonl(output/'trials.jsonl',records)
    return {'accepted_main_commit':commit,'tasks':len(receipts),'slots':len(records),
            'index':str(output/'trials.jsonl'),'audit_status':'pending','training_prohibited':True}

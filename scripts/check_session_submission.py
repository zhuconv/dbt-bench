#!/usr/bin/env python3
"""Rebuild retained session dbt sources on pristine inputs in native Harbor.

This diagnostic never transfers the agent database or prior compiled outputs.
It preserves original scores and does not certify an audit checker or a trace.
"""
import argparse,asyncio,hashlib,importlib.util,json,logging,os,shlex,shutil,sys,traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.backends import harbor_process_env,binding_identity
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref,file_hash,read_json,write_json
from dbt_bench.submission import inspect_submission

KEY='deb__dbt-session-attribution'
SOURCE_EXCLUDED={'logs','target','dbt_packages','.git'}


def source_files(project):
    files={}
    for p in sorted(project.rglob('*')):
        rel=p.relative_to(project)
        if rel.parts[0] in SOURCE_EXCLUDED:continue
        if p.is_symlink():raise ValueError('Source symlink requires separate transfer review: '+str(rel))
        if p.is_file():
            if p.suffix in {'.duckdb','.wal'}:raise ValueError('A submitted database is not a pristine input: '+str(rel))
            files[rel.as_posix()]=file_hash(p)
    if not {'dbt_project.yml','profiles.yml','models/marts/attribution_report.sql'}<=files.keys():
        raise ValueError('Required explicit submitted project sources absent')
    return files


async def run(args):
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier
    if args.project not in ['/app/dbt_transforms','/app/dbt_models_duckdb']:
        raise ValueError('Use an explicitly named DuckDB project from this task instruction')
    spec=read_json(args.spec);trial=args.trial.resolve();raw=read_json(trial/'result.json')
    if spec['task_key']!=KEY or spec['artifacts']!=[{'source':'/app','destination':'submission/workspace','exclude':[]}]:
        raise ValueError('Complete declared /app capture is required')
    capture=inspect_submission(trial,spec)
    if not capture['complete']:raise ValueError('Incomplete capture: '+str(capture['errors']))
    original=Task(task_dir=Path(raw['config']['task']['path']))
    if original.checksum!=raw['task_checksum']:raise ValueError('Original task checksum changed')
    row=next(r for r in load_catalog(ROOT) if r['task_key']==KEY)
    for rel in ['instruction.md','tests/test.sh','tests/test_outputs.py']:
        if file_hash(original.paths.task_dir/rel)!=file_hash(ROOT/row['run_path']/rel):raise ValueError('Native task variant changed: '+rel)
    source=trial/'artifacts/submission/workspace'/args.project.removeprefix('/app/')
    files=source_files(source)
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(Path(__file__),out/'runner.py');write_json(out/'submission-receipt.json',capture)
    write_json(out/'source-inventory.json',{'project':args.project,'files':files,'excluded_generated_top_level':sorted(SOURCE_EXCLUDED),
                'capture':evidence_ref(out/'submission-receipt.json',ROOT)})
    code_dir=ROOT/'validation/deb/dbt-session-attribution'
    for name,path in [('collector.py',code_dir/'collect_submission.py'),('independent.py',code_dir/'semantics/independent.py')]:shutil.copyfile(path,out/name)
    logger=logging.getLogger('session-pristine-rebuild');logger.setLevel(logging.DEBUG)
    handler=logging.FileHandler(out/'environment.log');logger.addHandler(handler)
    paths=TrialPaths(trial_dir=out/'environment');paths.mkdir();os.environ['PATH']=harbor_process_env('apptainer')['PATH']
    env=SingularityEnvironment(environment_dir=original.paths.environment_dir,environment_name='session-pristine-rebuild',
        session_id=out.name,trial_paths=paths,task_env_config=original.config.environment,persistent_env={'DB_TYPE':'duckdb'},
        logger=logger,singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
    report={'schema_version':1,'kind':'session_pristine_source_reconstruction','benchmark_trials':0,'model_requests':0,
        'original_native_result':raw.get('verifier_result'),'source_trial':evidence_ref(trial/'result.json',ROOT),
        'source_lock':evidence_ref(ROOT/'sources.lock.yaml',ROOT),'backend_binding':binding_identity(ROOT,row,'apptainer'),
        'submission_spec':evidence_ref(args.spec,ROOT),'captured_snapshot_hash':capture['snapshot_hash'],
        'submitted_project':args.project,'model_selection':'+attribution_report' if args.include_ancestors else 'attribution_report',
        'source_inventory':evidence_ref(out/'source-inventory.json',ROOT),
        'agent_database_transferred':False,'prior_build_outputs_transferred':False,'passed':False,'audit_reward':None,
        'scope':'Fresh native image and pristine input relations; only explicit captured dbt source files are uploaded. A successful rebuild does not resolve workflow/path contradictions or certify arbitrary semantic behavior.'}
    async def execute(label,command,timeout=180,require_success=True):
        result=await env.exec(command,timeout_sec=timeout);write_json(out/(label+'.json'),result.model_dump(mode='json'))
        print(label+': '+str(result.return_code),flush=True)
        if require_success and result.return_code:raise RuntimeError(label+': '+(result.stderr or '')+(result.stdout or ''))
        return result
    try:
        await asyncio.wait_for(env.start(force_build=False),timeout=300)
        if original.config.environment.healthcheck:await execute('healthcheck',original.config.environment.healthcheck.command)
        await execute('initial-hidden-paths','test ! -e /tests && test ! -e /solution')
        for name in ['collector.py','independent.py']:await env.upload_file(out/name,'/tmp/session-rebuild-'+name if name=='collector.py' else '/tmp/independent.py')
        await env.upload_file(ROOT/'scripts/context_probe.py','/tmp/context_probe.py')
        await execute('initial-context','python3 /tmp/context_probe.py --db /app/database/retail.duckdb')
        collector='python3 /tmp/session-rebuild-collector.py '
        await execute('initial-inputs',collector+'initial '+shlex.quote(args.project))
        # This diagnosed actual model project did not exist in the native image.
        # For the initialized native project, replacing/deleting extra source files
        # requires a reviewed transfer plan rather than an implicit merge.
        await execute('require-fresh-project','test ! -e '+shlex.quote(args.project))
        for name in files:
            target=args.project+'/'+name
            await execute('mkdir-'+hashlib.sha256(name.encode()).hexdigest()[:8],'mkdir -p '+shlex.quote(str(Path(target).parent)))
            await env.upload_file(source/name,target)
        await execute('before-build',collector+'before-build '+shlex.quote(args.project))
        selection='+attribution_report' if args.include_ancestors else 'attribution_report'
        command='cd '+shlex.quote(args.project)+' && dbt run --no-partial-parse --profiles-dir '+shlex.quote(args.project)+' --select '+selection
        build=await execute('dbt-build',command,timeout=600,require_success=False)
        report['dbt_exit_code']=build.return_code
        await execute('after-build',collector+'after-build '+shlex.quote(args.project))
        await execute('verifier-context','python3 /tmp/context_probe.py --db /app/database/retail.duckdb')
        verifier=Verifier(task=original,trial_paths=paths,environment=env,logger=logger,override_env={'DB_TYPE':'duckdb'})
        reward=await asyncio.wait_for(verifier.verify(),timeout=original.config.verifier.timeout_sec)
        report['reconstruction_native_result']=reward.model_dump(mode='json')
        await env.download_dir('/logs/agent/session-rebuild',out/'snapshots')
        snapshots={phase:read_json(out/'snapshots'/phase/'snapshot.json') for phase in ['initial','before-build','after-build']}
        for phase,snap in snapshots.items():
            assert snap['read_only'] and not snap['dbt_invoked']
            for name,sha in snap['files'].items():assert file_hash(out/'snapshots'/phase/name)==sha
        assert snapshots['initial']['source_inventory']=={}
        assert snapshots['before-build']['source_inventory']==snapshots['after-build']['source_inventory']==files
        initial_hash=snapshots['initial']['files']['inputs.json']
        assert initial_hash=='f2f343c8a75c59759bd3a0cef97426e743c9d7538ec4cc6ccaed4c7493b4a536'
        assert all(s['files']['inputs.json']==initial_hash for s in snapshots.values())
        semantics_spec=importlib.util.spec_from_file_location('rebuild_independent',out/'independent.py');sem=importlib.util.module_from_spec(semantics_spec);semantics_spec.loader.exec_module(sem)
        expected=sem.calculate(read_json(out/'snapshots/initial/inputs.json'));assert not expected['unresolved']
        output=read_json(out/'snapshots/after-build/output.json')
        comparison=sem.compare(expected['expected'],output) if output is not None else {'equal':False,'reason':'Output absent after source build'}
        write_json(out/'independent-comparison.json',{'expected':expected,'comparison':comparison,'checker_validation':'Diagnostic only; not a certified audit'})
        report['inputs_preserved']=True;report['exact_source_files']=len(files)
        report['independent_comparison']=evidence_ref(out/'independent-comparison.json',ROOT)
        report['native_score_reproduced']=reward.rewards==(raw.get('verifier_result') or {}).get('rewards')
        report['passed']=build.return_code==0 and comparison['equal'] and report['native_score_reproduced']
    except Exception as exc:report['error']={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
    finally:
        try:await env.stop(delete=True)
        except Exception as exc:report.update(passed=False,cleanup_error=str(exc))
        handler.flush();handler.close();logger.removeHandler(handler)
        report['environment_log']=evidence_ref(out/'environment.log',ROOT);report['runner']=evidence_ref(out/'runner.py',ROOT)
        report['checker_sources']=[evidence_ref(out/n,ROOT) for n in ['collector.py','independent.py']]
        write_json(out/'result.json',report)
    print(json.dumps({k:report.get(k) for k in ['passed','dbt_exit_code','native_score_reproduced','error']}))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trial',type=Path,required=True);p.add_argument('--spec',type=Path,required=True)
    p.add_argument('--project',required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--include-ancestors',action='store_true',help='Separate stronger diagnostic; the actual model trial selected only attribution_report')
    raise SystemExit(asyncio.run(run(p.parse_args())))

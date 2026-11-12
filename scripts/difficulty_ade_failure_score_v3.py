"""Evidence-based ADE source-contract failures; never generic error-string scoring."""
import argparse
import hashlib
from datetime import datetime,timezone
from pathlib import Path
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_submission_v2 import verify_capture
from prepare_difficulty_audit_v2 import source_plan
from difficulty_ade_score_v4 import assert_delivered_sources,inventory_map
from difficulty_ade_registry import ENTRIES,contract_record
from difficulty_ade_inputs import validate_initial


def load(reference):
    if not io.evidence_ok(reference,ROOT):raise ValueError('Evidence changed: '+reference['path'])
    return io.read_json(ROOT/reference['path'])


def stamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    return (t.replace(tzinfo=timezone.utc)if t.tzinfo is None else t).timestamp()


def independent_failure(policy,source_names,manifest=None,run_results=None,probe=None):
    # Current qualified ADE workflow requires these named models. An alias is
    # not guessed to be the required model; all captured package sources are
    # included, making this a conservative absence test.
    names={Path(n).stem for n in source_names if Path(n).suffix in ('.sql','.py')}
    missing=sorted(set(policy['models'])-names)
    if missing:return {'rule_ids':['ADE.required_named_dbt_model.present'],
        'missing_models':missing,'reason':'Complete delivered source inventory contains no SQL/Python model with a contract-required name.'}
    if manifest is None or run_results is None or probe is None:return None
    if probe.get('error')or not probe.get('input_unchanged'):return None
    if probe['invocation_id']!=manifest['metadata']['invocation_id']or probe['invocation_id']!=run_results['metadata']['invocation_id']:
        raise ValueError('Independent failure probe belongs to another invocation')
    nodes=manifest['nodes'];required={k for k,n in nodes.items()if n.get('resource_type')=='model'and n['name']in policy['models']}
    relevant=set(required);pending=list(required)
    while pending:
        key=pending.pop()
        for ancestor in nodes.get(key,{}).get('depends_on',{}).get('nodes',[]):
            if ancestor not in relevant:relevant.add(ancestor);pending.append(ancestor)
    failed={r['unique_id']for r in run_results['results']if r['status']=='error'}
    proved=[]
    for proof in probe['proofs']:
        if not proof.get('proved_failure')or proof['node_id']not in relevant&failed:continue
        if proof.get('typed_exception')not in ('ParserException','BinderException','CatalogException'):continue
        node=nodes[proof['node_id']]
        if proof.get('manifest_source_sha256')!=node['checksum']['checksum']or proof['compiled_sql_sha256']!=hashlib.sha256(node['compiled_code'].encode()).hexdigest():
            raise ValueError('Failure probe source differs from fresh compiled node')
        proved.append(proof)
    if not proved:return None
    return {'rule_ids':['ADE.required_dbt_model.compiles_on_fixed_inputs'],
            'typed_source_failures':proved,
            'reason':'Fresh failed dbt nodes on required-model dependency paths independently fail the pinned DuckDB parser/read-only binder; this is not inferred from log strings.'}


def score(audit_dir,output,diagnostic=None,validation=None,validation_only=False):
    audit_dir,output=Path(audit_dir),Path(output)
    if output.exists():raise ValueError('Preserve existing failure evidence')
    batch=audit_dir/'batch';bm=io.read_json(batch/'manifest.json')
    if bm['kind']!='difficulty_submission_reconstruction'or bm['model_requests']!=0:raise ValueError('Wrong audit path')
    binding=load(bm['source_binding']);key=binding['task_key'];entry_policy=ENTRIES[key]
    original=load(binding['original_result']);original_trial=(ROOT/binding['original_result']['path']).parent
    capture,archive,files=verify_capture(original_trial,key)
    if (original.get('verifier_result')or{}).get('rewards')!={'reward':0.0}:
        raise ValueError('This bounded failure path requires an actual original native zero')
    qualified=load(binding['qualified_review'])
    if not qualified['eligible_for_sampling']or qualified['audit']['conclusion']!='validated':raise ValueError('Unqualified task')
    overlay=load(qualified['audit']['overlay'])
    if any(not io.evidence_ok(r,ROOT)for r in overlay['code_refs']):raise ValueError('Qualified checker changed')
    qb=load(qualified['phase3_identity']['batch_manifest']);qt=Path(qualified['phase3_identity']['batch_manifest']['path']).parent/'tasks'/qualified['phase3_identity']['label']
    if io.tree_hash(qt)!=qb['tasks'][qt.name]['validation_copy_hash']:raise ValueError('Qualified task changed')
    config=load(bm['config']);results=list((Path(config['jobs_dir'])/config['job_name']).glob('*/result.json'))
    if len(results)!=1 or config['n_attempts']!=1:raise ValueError('Expected one actual reconstruction')
    rp=results[0];raw=io.read_json(rp);trial=rp.parent;task=Path(raw['config']['task']['path']);entry=bm['tasks'][task.name]
    if not raw.get('finished_at')or raw.get('exception_info'):raise ValueError('Interrupted/exceptional reconstruction is not a source failure')
    if io.tree_hash(task)!=entry['validation_copy_hash']or not io.evidence_ok(entry['native_verifier'],ROOT):raise ValueError('Actual reconstruction changed')
    request=load(entry['request']);ep=trial/'agent/audit-rebuild/execution.json';ex=io.read_json(ep);base=ep.parent
    if ex.get('error')or ex['task_key']!=key or ex['model_requests']!=0 or ex['spec_sha256']!=entry['request']['sha256']or ex['driver_sha256']!=io.file_hash(task/'audit-rebuild/audit_rebuild_driver.py'):
        raise ValueError('Setup/driver error or changed source execution, not a proved model failure')
    no_model=io.read_json(trial/'agent/no-model.json')
    if no_model['model_requests']or no_model['solution_uploaded']is not False:raise ValueError('Unexpected model/reference access')
    if not ex['runs']or any(r.get('timeout')or r.get('returncode')is None for r in ex['runs']):raise ValueError('Interrupted build remains unknown')
    if [r['command']for r in ex['runs']]!=request['commands'][:len(ex['runs'])]:raise ValueError('Unexpected build commands')
    baseline=load(qualified['reproduction']['trials'][0]['execution']);original_requests=load(qb['requests'])
    base_request=next(r for r in original_requests['tasks']if r['task_key']==key)
    submission,names,plan=source_plan(binding['extracted'],baseline,base_request['rebuild'])
    if plan!=binding['plan']:raise ValueError('Source plan changed')
    assert_delivered_sources(ex,names,binding['extracted'])
    final=inventory_map(ex,'final_files')
    if any(final.get(n,{}).get('exists')for n in submission['delete_paths']):raise ValueError('Missing source was restored')
    here=ROOT/'validation/ade'/key.removeprefix('ade__')/'phase3_v1';policy_path=here/entry_policy['policy'];policy=io.read_json(policy_path)
    if ex['initial_input_hashes']!=[{'path':entry_policy['database'],'sha256':policy['initial_database_sha256']}]:raise ValueError('Initial inputs differ')
    def hook(phase,name):
        found=[]
        for h in ex[phase+'_capture_hooks']:
            argv=h['command'];flag='--output'if '--output'in argv else '--output-dir'
            p=base/Path(argv[argv.index(flag)+1]).name/name
            if p.exists():
                if h['returncode']!=0:raise ValueError('Required independent probe failed')
                found.append(p)
        if len(found)!=1:raise ValueError('Independent probe absent/ambiguous: '+name)
        return found[0]
    initial=hook('initial','inputs.json');validate_initial(initial,ROOT/policy['inputs']['path'])
    observations=[io.evidence_ref(ep,ROOT),io.evidence_ref(rp,ROOT),io.evidence_ref(initial,ROOT),binding['extracted']['capture']]
    finding=independent_failure(policy,names)
    if finding is None:
        def artifact(name):
            row=final[name];p=base/'final-files'/row['file']
            if not row['exists']or row.get('error')or io.file_hash(p)!=row['sha256']:raise ValueError('Fresh dbt artifact missing/changed')
            return p
        mp=artifact('phase3_target/manifest.json');rrp=artifact('phase3_target/run_results.json')
        manifest=io.read_json(mp);runs=io.read_json(rrp)
        if diagnostic is None:
            pp=hook('final','sql-failure-probe.json');probe=io.read_json(pp)
        else:
            diagnostic=Path(diagnostic);dr=io.read_json(diagnostic)
            if dr['kind']!='independent_syntax_diagnostic_no_trial_rerun'or dr['returncode']!=0 or dr['model_requests']!=0 or dr['fresh_rebuild_result']!=io.evidence_ref(rp,ROOT):
                raise ValueError('Unbound independent syntax diagnostic')
            for name in ('image','script','probe','log'):
                if not io.evidence_ok(dr[name],ROOT):raise ValueError('Independent diagnostic evidence changed')
            prepared=load(binding['prepared_model_run'])
            if dr['image']['sha256']!=prepared['identity']['backend_bindings'][key]['image_sha256']or dr['script']['sha256']!=io.file_hash(ROOT/'scripts/difficulty_ade_failure_probe_v2.py'):
                raise ValueError('Diagnostic used different parser code/image')
            pp=ROOT/dr['probe']['path'];probe=io.read_json(pp)
            if probe.get('syntax_only')is not True or any(p.get('proved_failure')and p.get('typed_exception')!='ParserException'for p in probe['proofs']):
                raise ValueError('External diagnostic may prove only data-independent parser errors')
            observations.append(io.evidence_ref(diagnostic,ROOT))
        if probe['manifest_sha256']!=io.file_hash(mp)or probe['run_results_sha256']!=io.file_hash(rrp):raise ValueError('Typed probe not bound to fresh dbt artifacts')
        run=ex['runs'][-1]
        if not all(stamp(run['started_at_utc'])<=stamp(d['metadata']['generated_at'])<=stamp(run['finished_at_utc'])for d in (manifest,runs)):
            raise ValueError('dbt artifacts are not fresh for actual failing command')
        for proof in probe.get('proofs',[]):
            name=proof['original_file_path']
            if name not in names or final[name]['sha256']!=proof.get('source_sha256'):raise ValueError('Typed probe not bound to actual delivered source')
        finding=independent_failure(policy,names,manifest,runs,probe)
        observations.extend(io.evidence_ref(p,ROOT)for p in (mp,rrp,pp))
    if finding is None:raise ValueError('Source failure not independently proved; retain unknown without retrying behavior')
    if validation_only:
        if binding['fixture_only'] is not True:raise ValueError('Validation mode is restricted to explicit non-model fixtures')
        output.mkdir(parents=True)
        value={'kind':'failure_evaluator_control_observation','task_key':key,'audit_reward':0.0,
            'fixture_only':True,'evaluator':io.evidence_ref(Path(__file__),ROOT),'finding':finding,
            'source_binding':bm['source_binding'],'observations':observations,'model_requests':0}
        io.write_json(output/'validation-observation.json',value)
        return value
    if validation is None:raise ValueError('Direct failure requires a completed evaluator conformance manifest')
    validation=Path(validation);cert=io.read_json(validation)
    if cert.get('kind')!='failure_evaluator_validation'or not cert.get('complete')or cert.get('model_requests')!=0 or cert.get('evaluator')!=io.evidence_ref(Path(__file__),ROOT):
        raise ValueError('Failure evaluator validation is incomplete or bound to different code')
    required={'healthy_source','missing_capture','invalid_sql','missing_model'}
    if {c['id']for c in cert['controls']}!=required or any(not c['passed']or c['expected']!=c['observed']or not c['evidence']for c in cert['controls']):
        raise ValueError('Independent failure controls incomplete')
    for control in cert['controls']:
        if any(not io.evidence_ok(r,ROOT)for r in control['evidence']):raise ValueError('Failure control evidence changed')
    output.mkdir(parents=True)
    def save(name,value):
        p=output/name;io.write_json(p,value);return io.evidence_ref(p,ROOT)
    delivery_ref=save('submission-contract.json',contract_record(qualified))
    request_ref=save('request.json',{'task_key':key,'source_binding':bm['source_binding'],'observations':observations,'finding':finding})
    score_ref=save('audit-result.json',{'schema_version':1,'task_key':key,'target':'audit','reward':0.0,'status':'scored',
        'finding':finding,'model_requests':0,'training_prohibited':True,'native_rewards_modified':False})
    rules=[delivery_ref,io.evidence_ref(policy_path,ROOT),qualified['audit']['overlay'],io.evidence_ref(Path(__file__),ROOT)]
    evaluation_ref=save('failure-evaluation.json',{'schema_version':1,'evaluation_kind':'direct_contract_failure',
        'task_key':key,'task_version':binding['task_version'],'original_result':binding['original_result'],
        'qualified_review':binding['qualified_review'],'capture':binding['extracted']['capture'],
        'checker':io.evidence_ref(Path(__file__),ROOT),'qualified_checker':io.evidence_ref(here/entry_policy['checker'],ROOT),'failure_evaluator_validation_ref':io.evidence_ref(validation,ROOT),'checker_result':score_ref,'reward':0.0,'status':'scored',
        'rule_ids':finding['rule_ids'],'rule_evidence':rules,'observations':observations,'complete_capture':True,'model_requests':0})
    prepared=load(binding['prepared_model_run']);image=Path(tomllib.loads((task/'task.toml').read_text())['environment']['docker_image'])
    if io.file_hash(image)!=prepared['identity']['backend_bindings'][key]['image_sha256']:raise ValueError('Reconstruction image changed')
    paths=[Path(__file__),ROOT/'scripts/difficulty_ade_failure_probe_v2.py',ROOT/'scripts/difficulty_ade_prepare_audit_v2.py',
           ROOT/'scripts/difficulty_ade_registry.py',ROOT/'scripts/difficulty_ade_inputs.py',ROOT/'scripts/difficulty_ade_score_v4.py',
           ROOT/'scripts/prepare_difficulty_audit_v2.py',ROOT/'scripts/difficulty_submission.py',ROOT/'scripts/difficulty_submission_v2.py',ROOT/'src/dbt_bench/io.py']
    dependencies=save('adapter-dependencies.json',{'code_refs':[io.evidence_ref(p,ROOT)for p in paths]+bm['tools']+[bm['preparation_code']],
        'checker_dependencies':overlay['code_refs'],'image':io.evidence_ref(image,ROOT)})
    receipt={'schema_version':1,'task_key':key,'task_version':binding['task_version'],'original_result':binding['original_result'],
        'original_native_rewards':(original.get('verifier_result')or{}).get('rewards'),
        'source_binding':bm['source_binding'],'qualified_review':binding['qualified_review'],
        'checker_version':'ade-source-contract-failure-v3','checker':io.evidence_ref(Path(__file__),ROOT),
        'qualified_checker':io.evidence_ref(here/entry_policy['checker'],ROOT),'failure_evaluator_validation_ref':io.evidence_ref(validation,ROOT),
        'checker_request':request_ref,'audit_result':score_ref,'audit_reward':0.0,'audit_status':'scored',
        'rebuild_result':None,'evidence_kind':'direct_contract_failure','failure_evaluation_ref':evaluation_ref,
        'adapter':io.evidence_ref(Path(__file__),ROOT),'adapter_dependencies':dependencies,
        'fixture_only':binding['fixture_only'],'training_prohibited':True,'model_requests':0,
        'scope':'Contract failure proved from complete actual source and independently typed SQL/absence facts. Raw native reward unchanged; no behavior retry.'}
    save('receipt.json',receipt)
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--audit-dir',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--diagnostic',type=Path)
    p.add_argument('--validation',type=Path);p.add_argument('--validation-only',action='store_true')
    a=p.parse_args();r=score(a.audit_dir,a.output,a.diagnostic,a.validation,a.validation_only);print({k:r.get(k)for k in ('task_key','audit_reward','audit_status','evidence_kind','fixture_only')})

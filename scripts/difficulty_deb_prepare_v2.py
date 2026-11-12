#!/usr/bin/env python3
"""Prepare exact captured DEB source with two observed same-project builds."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_submission import extract_source
from difficulty_submission_v2 import verify_capture
from prepare_audit_rebuild import prepare as prepare_rebuild


def source_plan(extracted, baseline_execution, rebuild):
    """Discard generated artifacts; preserve delivered source and deletions."""
    project=Path(extracted['source_dir'])
    if not (project/'dbt_project.yml').is_file():
        raise ValueError('Actual submission has no dbt_project.yml; do not restore a baseline project')
    config=yaml.safe_load((project/'dbt_project.yml').read_text())
    generated={str(Path(x)) for x in rebuild.get('fresh_artifact_paths',[])}
    generated.update({'target','logs',str(config.get('target-path','target')),str(config.get('log-path','logs'))})
    suffixes={'.sql','.py','.yml','.yaml','.json','.toml','.csv'}
    def keep(name):
        p=Path(name)
        return (bool(p.parts) and not any(p.is_relative_to(Path(x)) for x in generated)
                and p.parts[0] not in ('.qwen','.dbt') and p.suffix.lower() in suffixes)
    captured={r['path']:r for r in extracted['source_files'] if keep(r['path'])}
    baseline={}
    for tree in baseline_execution['initial_trees']:
        for row in tree['files']:
            name=str(Path(tree['relative_path'])/row['path'])
            if keep(name):baseline[name]=row['sha256']
    for row in baseline_execution['initial_files']:
        if row.get('exists') and keep(row['relative_path']):baseline[row['relative_path']]=row['sha256']
    files=[{'path':name,'source':row['captured_source']} for name,row in sorted(captured.items())
           if baseline.get(name)!=row['sha256']]
    deleted=sorted(set(baseline)-set(captured))
    # Individual file deletions avoid Apptainer overlay directory rmtree faults.
    submission={'files':files,'delete_paths':deleted,'reset_paths':[]}
    return submission,sorted(captured),{'changed_source_files':len(files),'deleted_source_files':len(deleted),
                                      'captured_source_files':len(captured),'generated_dirs_discarded':sorted(generated)}



def ensure_repeat_build(commands):
    """Repeat the final actual build, never an unrelated upstream project."""
    def normalized(argv):
        result=[];i=0
        while i<len(argv):
            if argv[i] in ('--target-path','--log-path'):
                if i+1>=len(argv):raise ValueError('Incomplete artifact path option')
                i+=2;continue
            result.append(argv[i]);i+=1
        return result
    builds=[(i,c) for i,c in enumerate(commands) if len(c)>1 and c[0]=='dbt' and c[1] in ('run','build')]
    if not builds:raise ValueError('Qualified request has no actual dbt materialization command')
    last=builds[-1][1];same=[i for i,c in builds if normalized(c)==normalized(last)]
    result=deepcopy(commands)
    if len(same)<2:result.append(deepcopy(last))
    return result,{'original_commands':commands,'final_materialization_command':last,
        'existing_matching_build_indices':same,'appended_repeat':len(same)<2,
        'same_workspace':True,'purpose':'Audit delivered-source idempotence only; never evidence the model performed another dbt command.'}


def collector_hooks(request, key):
    """Register typed current collectors; explicit per-command capture indices."""
    rebuild=request['rebuild'];task=key.removeprefix('deb__')
    from difficulty_deb_operations_collect import RAW
    changes={'original_hooks':deepcopy(rebuild.get('capture_hooks',[])), 'replacement':None}
    if task in RAW:
        collector=io.evidence_ref(ROOT/'scripts/difficulty_deb_operations_collect.py',ROOT)
        def hook(phase,index=None,target='target'):
            row={'phase':phase,'source':collector,'args':['--task',task,'--phase','initial' if phase=='initial' else 'final','--output','{output}','--target-path',target]}
            if index is not None:row['command_index']=index
            return row
        hooks=[hook('initial')];last_target='target'
        for index,command in enumerate(rebuild['commands']):
            if len(command)>1 and command[0]=='dbt' and command[1] in ('run','build','test'):
                last_target=command[command.index('--target-path')+1] if '--target-path' in command else 'target'
                hooks.append(hook('after_command',index,last_target))
        hooks.append(hook('final',target=last_target));rebuild['capture_hooks']=hooks
        changes['replacement']=collector
    else:
        # The earlier preparer appended the final build but did not duplicate
        # its per-command hook. Preserve the original hook source and arguments.
        commands=rebuild['commands'];last=len(commands)-1
        if len(commands[last])>1 and commands[last][:2] in (['dbt','run'],['dbt','build']):
            hooks=rebuild.get('capture_hooks',[])
            if not any(h.get('phase')=='after_command' and h.get('command_index')==last for h in hooks):
                prior=[(i,c) for i,c in enumerate(commands[:last]) if c==commands[last]]
                if prior:
                    index=prior[-1][0]
                    clones=[deepcopy(h) for h in hooks if h.get('phase')=='after_command' and h.get('command_index')==index]
                    for h in clones:h['command_index']=last
                    hooks.extend(clones)
    changes['current_hooks']=deepcopy(rebuild.get('capture_hooks',[]))
    changes['scope']='Read-only capture adaptation only; original native/checker/source/budget unchanged.'
    return changes


def prepare(run,trial,output,*,fixture=False):
    run,trial,output=map(Path,(run,trial,output))
    if output.exists() or not str(output.resolve()).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH audit directory')
    m=io.read_json(run/'manifest.json');key=m['identity']['task_key']
    cfg=io.read_json(trial/'config.json')
    if Path(cfg['task']['path']).resolve()!=Path(m['identity']['config']['tasks'][0]['path']).resolve():
        raise ValueError('Capture belongs to a different prepared task')
    if not fixture and trial.parent.resolve()!=(run/'jobs'/run.name).resolve():
        raise ValueError('Actual model result is outside its planned run')
    if not fixture and cfg['agent']['name']!='qwen-coder':
        raise ValueError('Reference execution cannot become a model attempt')
    production=m['identity'].get('reviewed_production') or m['identity']['phase3_production']
    candidate=production[key]['pool_row']
    qualified_review=production[key].get('qualified_review') or candidate['phase3_record']
    if not io.evidence_ok(qualified_review,ROOT):raise ValueError('Qualified review changed')
    record=io.read_json(ROOT/qualified_review['path'])
    reviewed_identity=record.get('phase4_identity') or record['phase3_identity']
    if not io.evidence_ok(reviewed_identity['batch_manifest'],ROOT):raise ValueError('Reviewed batch changed')
    original_batch=io.read_json(ROOT/reviewed_identity['batch_manifest']['path'])
    original_requests=io.read_json(ROOT/original_batch['requests']['path'])
    request=deepcopy(next(x for x in original_requests['tasks'] if x['task_key']==key))
    base=Path(original_batch['base_batch']['path']).parent
    output.mkdir(parents=True)
    verify_capture(trial,key)
    extracted=extract_source(trial,key,output/'captured-source')
    baseline=io.read_json(ROOT/record['reproduction']['trials'][0]['execution']['path'])
    request['rebuild']['submission'],all_source,plan=source_plan(extracted,baseline,request['rebuild'])
    original_commands=deepcopy(request['rebuild']['commands'])
    request['rebuild']['commands'],idempotence=ensure_repeat_build(original_commands)
    collector_plan=collector_hooks(request,key)
    request['rebuild']['capture_files']=sorted(set(request['rebuild']['capture_files'])|set(all_source))
    project=yaml.safe_load((Path(extracted['source_dir'])/'dbt_project.yml').read_text())
    for field,default in (('model-paths',['models']),('macro-paths',['macros'])):
        paths=project.get(field,default)
        if not isinstance(paths,list) or any(not isinstance(p,str) or Path(p).is_absolute() or '..' in Path(p).parts for p in paths):
            raise ValueError('Uncaptured external or invalid source-directory declaration')
        request['rebuild']['capture_trees']=sorted(set(request['rebuild']['capture_trees'])|set(paths))
    request.pop('phase3',None)
    request.pop('phase4',None)
    request['label']=key+'--difficulty-rebuild'
    source_manifest=output/'source-binding.json'
    io.write_json(source_manifest,{'schema_version':1,'task_key':key,'task_version':candidate['task_version'],
        'actual_model_trial':not fixture,'fixture_only':fixture,
        'original_result':io.evidence_ref(trial/'result.json',ROOT),
        'prepared_model_run':io.evidence_ref(run/'manifest.json',ROOT),
        'qualified_review':qualified_review,'extracted':extracted,'plan':plan,
        'model_requests':0,'training_prohibited':True,'idempotence_plan':idempotence,
        'preparer':io.evidence_ref(Path(__file__),ROOT),'collector_plan':collector_plan,
        'scope':'Rebuild exactly captured delivered source on clean fixed inputs. Rebuild commands do not prove the original model performed any prescribed step.'})
    req_path=output/'requests.json'
    io.write_json(req_path,{'schema_version':1,'model_requests':0,'n_attempts':1,'tasks':[request]})
    batch=output/'batch';prepare_rebuild(base,req_path,batch,concurrent=1)
    bm=io.read_json(batch/'manifest.json');entry=bm['tasks'][request['label']]
    target=batch/'tasks'/request['label']
    production=Path(production[key]['run_path'])
    tests_before=io.tree_hash(target/'tests')
    shutil.copyfile(production/'instruction.md',target/'instruction.md')
    if io.tree_hash(target/'tests')!=tests_before:
        raise ValueError('Native verifier unexpectedly changed')
    entry['validation_copy_hash']=io.tree_hash(target)
    bm.update(kind='difficulty_submission_reconstruction',source_binding=io.evidence_ref(source_manifest,ROOT),
              training_prohibited=True,fixture_only=fixture)
    io.write_json(batch/'manifest.json',bm)
    return {'task_key':key,'batch':str(batch),'source_plan':plan,'model_requests':0,'fixture_only':fixture}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--trial',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--fixture',action='store_true')
    a=p.parse_args();print(prepare(a.run,a.trial,a.output,fixture=a.fixture))

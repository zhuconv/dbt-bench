#!/usr/bin/env python3
"""Prepare one clean no-model rebuild of a captured difficulty submission."""
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
    extracted=extract_source(trial,key,output/'captured-source')
    baseline=io.read_json(ROOT/record['reproduction']['trials'][0]['execution']['path'])
    request['rebuild']['submission'],all_source,plan=source_plan(extracted,baseline,request['rebuild'])
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
        'model_requests':0,'training_prohibited':True,
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

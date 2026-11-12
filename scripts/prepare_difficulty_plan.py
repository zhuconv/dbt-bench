"""Prepare all remaining authorized attempt configs; never submit or run jobs."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io


def plan_tasks(tasks, reused):
    eligible=io.unique_index([r for r in tasks if r['eligible_for_measurement']], 'task_key')
    occupied=set(); prior=[]
    for run in reused:
        key=run['task_key']
        if key not in eligible or run['task_version']!=eligible[key]['task_version']:
            raise ValueError('Reused run has a foreign task or version')
        for n in run['attempt_ids']:
            if type(n) is not int or n not in (1,2,3,4) or (key,n) in occupied:
                raise ValueError('Reused runs have invalid/duplicate attempt slots')
            occupied.add((key,n))
        prior.append(run)
    remaining=[]
    for key,row in sorted(eligible.items()):
        ids=[n for n in (1,2,3,4) if (key,n) not in occupied]
        size={'ade':4,'spider2':2,'deb':1}[row['source']]
        for i in range(0,len(ids),size):
            group=ids[i:i+size]
            remaining.append({'task_key':key,'task_version':row['task_version'],'source':row['source'],
                              'attempt_ids':group,'name':key+'--a'+'-'.join(map(str,group))})
    return {'reused':prior,'remaining':remaining,'planned_attempts':len(eligible)*4,
            'existing_attempts':len(occupied),'new_attempts':sum(len(r['attempt_ids']) for r in remaining)}


def prepare(pool, reused, output):
    from prepare_difficulty_run_v3 import prepare as prepare_run
    from seal_difficulty_preflight_v2 import seal
    pool,output=Path(pool),Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Use a new SCRATCH plan directory')
    population=io.read_json(pool)
    if not population['phase4_review_complete'] or not population['training_prohibited']:
        raise ValueError('Require the final measurement-only population')
    entries=[]
    for directory in reused:
        directory=Path(directory)
        if (directory/'superseded-by.json').exists():raise ValueError('Cannot reuse a superseded preparation')
        m=io.read_json(directory/'manifest.json'); ident=m['identity']
        if (m['experiment_id']!=io.digest(ident) or m['config_hash']!=io.file_hash(directory/'config.yaml')
                or ident['purpose']!='difficulty_only_no_training'
                or ident['measurement_rules']!=population['measurement_rules']):
            raise ValueError('Reused configuration identity differs')
        preflight=io.read_json(directory/'preflight.json')
        for field in ('runner','slurm_script','manifest'):
            if not io.evidence_ok(preflight[field],ROOT):raise ValueError('Reused preflight changed')
        entries.append({'task_key':ident['task_key'],'task_version':ident['task_version'],
            'attempt_ids':ident['attempt_ids'],'run':str(directory),'manifest':io.evidence_ref(directory/'manifest.json',ROOT)})
    plan=plan_tasks(population['tasks'],entries)
    output.mkdir(parents=True)
    io.write_json(output/'plan.json',{**plan,'population':io.evidence_ref(pool,ROOT),
        'builder':io.evidence_ref(Path(__file__),ROOT),'model_requests':0,'submitted_jobs':0,
        'training_prohibited':True,'scope':'Static native Harbor configurations only; no scheduler or model calls.'})
    results=[]
    for row in plan['remaining']:
        directory=output/'runs'/row['name']
        try:
            prepare_run(directory,row['source'],row['task_key'],row['attempt_ids'],pool)
            if row['task_key']=='ade__ade-bench-f1009':
                from difficulty_ade_prepare_clock_v2 import augment_basis
                from difficulty_ade_preflight import seal as clock_seal
                augment_basis(directory);clock_seal(directory)
            else:seal(directory)
            result={**row,'run':str(directory),'prepared':True,
                'manifest':io.evidence_ref(directory/'manifest.json',ROOT),
                'preflight':io.evidence_ref(directory/'preflight.json',ROOT)}
        except Exception as exc:
            result={**row,'run':str(directory),'prepared':False,
                    'error':{'type':type(exc).__name__,'message':str(exc)}}
        results.append(result)
        io.write_json(output/'records'/(row['name']+'.json'),result)
        print(row['task_key'],row['attempt_ids'],result['prepared'],result.get('error',''),flush=True)
    summary={'plan':io.evidence_ref(output/'plan.json',ROOT),'reused':entries,'new_runs':results,
             'prepared_runs':sum(r['prepared'] for r in results),'failed_runs':sum(not r['prepared'] for r in results),
             'planned_attempts':plan['planned_attempts'],'model_requests':0,'submitted_jobs':0,'training_prohibited':True}
    io.write_json(output/'summary.json',summary)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool',type=Path,required=True);p.add_argument('--reuse-run',type=Path,action='append',default=[])
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();s=prepare(a.pool,a.reuse_run,a.output)
    print({k:s[k] for k in ('prepared_runs','failed_runs','planned_attempts','model_requests','submitted_jobs')})

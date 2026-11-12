#!/usr/bin/env python3
"""Prepare a fixed-pool difficulty job; native Qwen and verifier stay unchanged."""
import argparse
import base64
from copy import deepcopy
import json
from pathlib import Path
import shlex
import shutil
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
from dbt_bench import io
from dbt_bench.prepare import check_harbor
from prepare_measured_production import prepare as prepare_production, select
from review_tools import cached_hashes


def capture_spec(record, root=ROOT):
    identity=record.get('phase4_identity') or record['phase3_identity']
    if not io.evidence_ok(identity['batch_manifest'],root):
        raise ValueError('Reviewed build manifest changed')
    batch=io.read_json(root/identity['batch_manifest']['path'])
    entry=batch['tasks'][identity['label']]
    if not io.evidence_ok(entry['request'],root):
        raise ValueError('Reviewed source/path declaration changed')
    request=io.read_json(root/entry['request']['path'])
    task=(root/entry['request']['path']).parent.parent
    observer=io.read_json(task/'mechanical/spec.json')
    project=request['project_root']
    if project not in ('/app','/workspace') and not project.startswith('/app/'):
        raise ValueError('Unreviewed project path')
    result = {'schema_version':1,'task_key':record['task_key'],
            'project_root':project,'database_paths':observer['database_paths'],
            'reviewed_path_evidence':[entry['request'],io.evidence_ref(task/'mechanical/spec.json',root)],
            'training_prohibited':True}
    if record['task_key'] == 'deb__dbt-loyalty-points-analysis':
        policy = io.read_json(root/'configs/difficulty-v1/capture-paths.json')
        if not io.evidence_ok(policy['review'], root):
            raise ValueError('Supplementary path review changed')
        reviewed = io.read_json(root/policy['review']['path'])
        item = next(x for x in reviewed['rows'] if x['task_key'] == record['task_key'])
        if (item['task_version'] != record['task_version']
                or item['actual_instruction']['sha256'] != identity['instruction_sha256']
                or item['explicit_primary_project'] != project):
            raise ValueError('Supplementary path review does not match this task version')
        result['additional_project_roots'] = [{'path': p, 'purpose': 'readonly_path_equivalence_investigation',
                                               'review': policy['review']}
                                             for p in item['additional_paths_for_readonly_investigation']]
    return result


def hook_command(script, spec):
    encoded=base64.b64encode(Path(script).read_bytes()).decode('ascii')
    code="import base64;exec(compile(base64.b64decode("+repr(encoded)+"),'difficulty_collect.py','exec'))"
    return shlex.join(['python3','-c',code,'--spec-json',json.dumps(spec,sort_keys=True),
                       '--output','/tmp/dbt-bench-difficulty-submission'])


def prepare(output, source, key, attempt_ids, pool, root=ROOT):
    root,output=Path(root),Path(output)
    if not attempt_ids or len(set(attempt_ids))!=len(attempt_ids) or any(type(x)!=int or x not in (1,2,3,4) for x in attempt_ids):
        raise ValueError('Use distinct planned attempt slots from1..4')
    if output.exists() or not str(output.resolve()).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH run directory')
    rules=io.read_json(root/'configs/difficulty-v1/measurement-rules.json')
    selected,record=select(root,pool,source,key)
    row=selected['frozen_pool_row']
    spec=capture_spec(record,root)
    output.mkdir(parents=True)
    serve=io.read_json(root/'inference/qwen3.6-35b-a3b.json')
    with cached_hashes():
        prepare_production(root,output/'production',pool=pool,source=source,key=key,
                           model='openai/'+serve['served_model_name'],endpoint='http://127.0.0.1:8000/v1',
                           deployment=serve['model']+'@'+serve['revision'],attempts=len(attempt_ids))
    base=io.read_json(output/'production/manifest.json')
    config=deepcopy(base['identity']['config'])
    production=Path(config['tasks'][0]['path'])
    task=output/'tasks'/key;shutil.copytree(production,task)
    tools=output/'tools';tools.mkdir()
    shutil.copyfile(root/'scripts/difficulty_collect_v3.py',tools/'difficulty_collect_v3.py')
    shutil.copyfile(__file__,tools/'prepare_difficulty_run_v3.py')
    command=hook_command(tools/'difficulty_collect_v3.py',spec)
    original=(task/'task.toml').read_text()
    (task/'task.toml').write_text(original+'\n[[verifier.collect]]\ncommand = '+json.dumps(command)+
                                 '\ntimeout_sec = 600\n')
    parsed=tomllib.loads((task/'task.toml').read_text())
    assert len(parsed['verifier']['collect'])==len(tomllib.loads(original).get('verifier',{}).get('collect',[]))+1
    assert io.tree_hash(task/'tests')==io.tree_hash(production/'tests')
    config.update(job_name=output.name,jobs_dir=str(output/'jobs'),tasks=[{'path':str(task)}],
                  artifacts=[{'source':'/tmp/dbt-bench-difficulty-submission','destination':'difficulty-submission'}],
                  retry={'max_retries':0},n_attempts=len(attempt_ids),n_concurrent_trials=1)
    config['agents'][0]['kwargs']['config']=io.read_json(root/'inference/qwen-code-settings.json')
    config=check_harbor(config,[task])
    identity=deepcopy(base['identity'])
    identity.update(config=config,purpose='difficulty_only_no_training',
        measurement_rules=io.evidence_ref(root/'configs/difficulty-v1/measurement-rules.json',root),
        attempt_ids=attempt_ids,task_key=key,task_version=row['task_version'],
        capture_spec=spec,production_manifest=io.evidence_ref(output/'production/manifest.json',root),
        actual_task_hash=io.tree_hash(task),native_tests_hash=io.tree_hash(task/'tests'),
        observation_change='Native Harbor post-agent pre-verifier collect hook only; instruction, agent and tests unchanged.',
        generation={'server':io.evidence_ref(root/'inference/qwen3.6-35b-a3b.json',root),
                    'client':config['agents'][0]['kwargs']['config']},
        tools=[io.evidence_ref(p,root) for p in sorted(tools.iterdir())])
    # Per-job seed separates stochastic streams if four planned attempts need
    # separate allocations. Within a job Harbor starts independent clean trials.
    identity['server_seed']=int(io.digest({'task':key,'attempt_ids':attempt_ids,'version':rules['version']})[:8],16)
    io.write_json(output/'config.yaml',config)
    manifest={'schema_version':1,'experiment_id':io.digest(identity),'identity':identity,
              'config_hash':io.file_hash(output/'config.yaml'),
              'planned':[{'task_key':key,'attempt':n} for n in attempt_ids],
              'training_prohibited':True,'prepared_model_requests':0}
    io.write_json(output/'manifest.json',manifest)
    return {'task_key':key,'attempts':attempt_ids,'config':str(output/'config.yaml'),'model_requests':0}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool',type=Path,required=True)
    p.add_argument('--source',choices=['ade','spider2','deb'],required=True)
    p.add_argument('--task',required=True);p.add_argument('--attempt',action='append',type=int,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(prepare(a.output,a.source,a.task,a.attempt,a.pool))

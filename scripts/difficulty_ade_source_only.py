"""Real ADE source-only delivery control: install SQL, let native tests build it."""
from copy import deepcopy
from pathlib import Path
import argparse
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_ade_conformance import command
from prepare_difficulty_run import prepare as prepare_basis
from prepare_difficulty_audit_v2 import prepare as prepare_audit


def run(output):
    output = Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Use a fresh SCRATCH fixture directory')
    output.mkdir(parents=True)
    key = 'ade__ade-bench-ana-eng005'
    basis = output/'basis'; prepare_basis(basis,'ade',key,[1])
    manifest = io.read_json(basis/'manifest.json')
    record_ref = manifest['identity']['phase3_production'][key]['pool_row']['phase3_record']
    qualified = io.read_json(ROOT/record_ref['path'])
    bm = io.read_json(ROOT/qualified['phase3_identity']['batch_manifest']['path'])
    requests = io.read_json(ROOT/bm['requests']['path'])
    request = next(x for x in requests['tasks'] if x['task_key']==key)
    submission = request['rebuild']['submission']
    if submission['delete_paths'] or submission['reset_paths'] or len(submission['files'])!=1:
        raise ValueError('Source-only control expects exactly the reviewed SQL file')
    source = submission['files'][0]
    if source['path']!='models/warehouse/fact_inventory.sql' or not io.evidence_ok(source['source'],ROOT):
        raise ValueError('Reviewed control source changed')
    task = Path(manifest['identity']['config']['tasks'][0]['path'])
    native_hash = io.tree_hash(task/'tests')
    # Fixture-only oracle payload has no dbt invocation or database payload.
    shutil.rmtree(task/'solution'); (task/'solution').mkdir()
    shutil.copyfile(ROOT/source['source']['path'],task/'solution/fact_inventory.sql')
    (task/'solution/solve.sh').write_text('#!/bin/bash\nset -euo pipefail\ncp /solution/fact_inventory.sql /app/models/warehouse/fact_inventory.sql\n')
    assert io.tree_hash(task/'tests')==native_hash
    manifest['identity'].update(purpose='audit_conformance_only',actual_task_hash=io.tree_hash(task),
        source_only_control={'source':source['source'],'delivery_commands':['cp'],
          'dbt_invocations':0,'database_payloads':0,'native_tests_unchanged':True,
          'generator':io.evidence_ref(Path(__file__),ROOT)})
    manifest.update(experiment_id=io.digest(manifest['identity']),fixture_only=True)
    io.write_json(basis/'manifest.json',manifest)
    oracle=output/'oracle';oracle.mkdir()
    config=deepcopy(manifest['identity']['config'])
    config.update(agents=[{'name':'oracle'}],n_attempts=1,n_concurrent_trials=1,
                  job_name='source-only',jobs_dir=str(oracle/'jobs'))
    io.write_json(oracle/'config.json',config)
    command([str(ROOT/'runtime/.venv/bin/harbor'),'run','-c',str(oracle/'config.json')],output,'oracle')
    paths=list((oracle/'jobs/source-only').glob('*/result.json'))
    if len(paths)!=1:raise ValueError('Expected exactly one actual source-only control')
    trial=paths[0].parent
    prepare_audit(basis,trial,output/'rebuild',fixture=True)
    command([str(ROOT/'runtime/.venv/bin/harbor'),'run','-c',str(output/'rebuild/batch/config.yaml')],output,'rebuild')
    command([sys.executable,str(ROOT/'scripts/difficulty_ade_score_v3.py'),'--audit-dir',str(output/'rebuild'),'--output',str(output/'scoring')],output,'score')
    receipt=io.read_json(output/'scoring/receipt.json')
    capture=io.read_json(trial/'artifacts/difficulty-submission/capture.json')
    baseline=io.read_json(ROOT/'validation/ade/ade-bench-ana-eng005/phase3_v1/policy-v3.json')
    db=next(x for x in capture['databases']if x['path']=='/app/analytics_engineering.duckdb')
    result=io.read_json(paths[0])
    outcome={'task_key':key,'fixture_only':True,'training_prohibited':True,'model_requests':0,
        'original_result':io.evidence_ref(paths[0],ROOT),'receipt':io.evidence_ref(output/'scoring/receipt.json',ROOT),
        'native_reward':(result.get('verifier_result')or{}).get('rewards'),
        'audit_reward':receipt['audit_reward'],'audit_status':receipt['audit_status'],
        'original_database_equals_initial':db['sha256']==baseline['initial_database_sha256'],
        'agent_dbt_invocations':0,'source_only_payload':io.evidence_ref(task/'solution/solve.sh',ROOT),
        'scope':'Actual legal source-only delivery. Native verifier, then exact-source clean audit, performs the build; neither build is claimed as an agent command.'}
    io.write_json(output/'outcome.json',outcome)
    return outcome

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    print(run(p.parse_args().output))

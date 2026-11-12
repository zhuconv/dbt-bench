"""Add explicit SQL-session observations to fresh, never-launched F1009 runs."""
from copy import deepcopy
import base64
import json
from pathlib import Path
import shlex
import shutil
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io


def augment_basis(run):
    run=Path(run);manifest=io.read_json(run/'manifest.json')
    if manifest['identity']['task_key']!='ade__ade-bench-f1009' or any((run/p).exists()for p in ('jobs','launch-intent.json','launch-result.json')):
        raise ValueError('Only a fresh unlaunched F1009 preparation can gain the clock observer')
    if 'ade_sql_session_observer' in manifest['identity']:raise ValueError('Clock observer already registered')
    task=Path(manifest['identity']['config']['tasks'][0]['path'])
    source=ROOT/'scripts/difficulty_ade_session_collect.py';dest=run/'tools/difficulty_ade_session_collect.py'
    shutil.copyfile(source,dest)
    code="import base64;exec(compile(base64.b64decode("+repr(base64.b64encode(dest.read_bytes()).decode())+"),'difficulty_ade_session_collect.py','exec'))"
    command=shlex.join(['python3','-c',code,'--project-root','/app','--database','/app/f1.duckdb',
        '--capture-root','/tmp/dbt-bench-difficulty-submission','--output','/tmp/dbt-bench-difficulty-submission/dbt-session-clock.json'])
    original=(task/'task.toml').read_text();before=io.tree_hash(task/'tests')
    (run/'clock-before-manifest.json').write_text((run/'manifest.json').read_text())
    (task/'task.toml').write_text(original+'\n[[verifier.collect]]\ncommand = '+json.dumps(command)+'\ntimeout_sec = 120\n')
    assert len(tomllib.loads((task/'task.toml').read_text())['verifier']['collect'])==len(tomllib.loads(original)['verifier']['collect'])+1
    assert io.tree_hash(task/'tests')==before
    manifest['identity'].update(actual_task_hash=io.tree_hash(task),ade_sql_session_observer={
        'source':io.evidence_ref(dest,ROOT),'preparer':io.evidence_ref(Path(__file__),ROOT),
        'kind':'post-agent-pre-native-readonly-dbt-session-clock','does_not_parse_or_run_model_source':True})
    manifest['identity']['tools'].append(io.evidence_ref(dest,ROOT))
    manifest['experiment_id']=io.digest(manifest['identity'])
    io.write_json(run/'manifest.json',manifest)
    return manifest


def augment_audit(audit):
    audit=Path(audit);batch=audit/'batch';manifest=io.read_json(batch/'manifest.json')
    if (batch/'jobs').exists()or len(manifest['tasks'])!=1:raise ValueError('Only one fresh unlaunched rebuild can gain session hooks')
    label,entry=next(iter(manifest['tasks'].items()))
    if entry['task_key']!='ade__ade-bench-f1009':raise ValueError('Wrong task for clock observer')
    task=batch/'tasks'/label;payload=task/'audit-rebuild';path=payload/'request.json';request=io.read_json(path)
    history=audit/'clock-preparation-before';history.mkdir()
    shutil.copyfile(batch/'manifest.json',history/'manifest.json');shutil.copyfile(path,history/'request.json')
    request_doc=io.read_json(audit/'requests.json');shutil.copyfile(audit/'requests.json',history/'requests.json')
    source=ROOT/'scripts/difficulty_ade_session_collect.py'
    for phase in ('initial','final'):
        index=len(request['capture_hooks']);name=f'capture-hook-{index:02d}.py'
        shutil.copyfile(source,payload/name)
        args=['--project-root','/app','--database','/app/f1.duckdb','--output-dir','{output}']
        request['capture_hooks'].append({'phase':phase,'args':args,'payload_path':name,'sha256':io.file_hash(source)})
        request_doc['tasks'][0]['rebuild']['capture_hooks'].append({'phase':phase,'args':args,'source':io.evidence_ref(source,ROOT)})
    io.write_json(path,request);io.write_json(audit/'requests.json',request_doc)
    entry.update(request=io.evidence_ref(path,ROOT),validation_copy_hash=io.tree_hash(task))
    manifest.update(requests=io.evidence_ref(audit/'requests.json',ROOT),ade_sql_session_observer={
        'source':io.evidence_ref(source,ROOT),'preparer':io.evidence_ref(Path(__file__),ROOT),
        'prior_unlaunched_preparation':io.evidence_ref(history/'manifest.json',ROOT)})
    io.write_json(batch/'manifest.json',manifest)
    return manifest

"""Audit original DEB submissions without changing native scores or model calls.

The adapter verifies clean reconstruction provenance, observes the original DB
in its locked image, and calls the frozen checker. Missing model-action or
task-specific provenance remains unknown; an audit rebuild never proves those
actions occurred in the model trial. New model-source shapes are not whitelisted.
"""
import argparse
from collections import Counter
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import json
from datetime import datetime, date, time
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from difficulty_deb_binding_v3 import verify
from difficulty_deb_registry_v2 import load
from difficulty_deb_events import inspect_trial
from difficulty_deb_workflow_v3 import direct_workflow, declared_action_gate, spec_workflow
from audit_tables import decode

APPTAINER = '/opt/apps/tacc-apptainer/1.4.1/bin/apptainer'


def typed_bag(path):
    """Exact typed values; timestamp spelling and JSON whitespace are immaterial."""
    def token(value):
        if isinstance(value, (datetime, date, time)):
            return type(value).__name__, value.isoformat()
        if isinstance(value, Decimal):
            return 'Decimal', value
        if isinstance(value, list):
            return 'list', tuple(token(v) for v in value)
        if isinstance(value, dict):
            return 'dict', tuple(sorted((token(k), token(v)) for k, v in value.items()))
        return type(value).__name__, value
    return Counter(tuple(token(decode(v)) for v in json.loads(line))
                   for line in path.read_text().splitlines())


def save(path, value):
    if path.exists():
        raise ValueError('Never replace difficulty evidence: ' + str(path))
    io.write_json(path, value)
    return io.evidence_ref(path, ROOT)


def formal_request(qualified):
    review = load(qualified['record']['formal_candidate_evaluation']['review'])
    records = [r for r in review['records'] if r.get('stage') == 'agent-submission']
    if not records:
        raise ValueError('Qualified candidate lacks an actual submission request')
    for row in records:
        for field in ('spec', 'request'):
            if row.get(field):
                return load(row[field])
    # Some publication wrappers retain only the actual checker result; its
    # evidence/request field still binds the original immutable input projection.
    audit = load(records[0]['audit_result'])
    for field in ('input_refs', 'evidence', 'request'):
        if isinstance(audit.get(field), dict):
            return audit[field]
    raise ValueError('Qualified request not retained in a supported format')


def observe_submission(context, output):
    d = context['fresh']; binding = context['binding']; key = binding['task_key']
    capture = context['original_capture']
    output.mkdir(parents=True, exist_ok=False)
    copies = output / 'database-copies'; copies.mkdir()
    mounts=[];hashes=[]
    required=set(context['request']['observation']['database_paths'])
    entries=io.unique_index(capture['databases'],'path')
    expected_hashes={}
    for db in required:
        if db not in entries or entries[db].get('exists') is not True:
            raise ValueError('Required original database is absent/uncaptured; no image database fallback permitted: '+db)
        for name in (db,db+'.wal'):
            if name not in entries or type(entries[name].get('exists')) is not bool:
                raise ValueError('Required database/WAL inventory incomplete: '+name)
            expected_hashes[name]=entries[name]['sha256'] if entries[name]['exists'] else None
    # Bind isolated parent directories, so an absent captured WAL cannot reveal
    # any file from the initial image. Only the explicitly captured bytes exist.
    parents={str(Path(db).parent) for db in required}
    for index,parent in enumerate(sorted(parents)):
        directory=copies/str(index);directory.mkdir()
        for name,digest in expected_hashes.items():
            if str(Path(name).parent)!=parent or digest is None:continue
            item=entries[name];source=context['original_trial']/'artifacts/difficulty-submission'/item['file']
            destination=directory/Path(name).name;shutil.copyfile(source,destination)
            if io.file_hash(destination)!=digest:raise ValueError('Copied actual DB/WAL changed')
            hashes.append({'source':io.evidence_ref(source,ROOT),'copy':io.evidence_ref(destination,ROOT),'database_path':name})
        mounts+=['--bind',str(directory)+':'+parent+':ro']
    observation = deepcopy(context['request']['observation'])
    observation['files'] = []  # Do not read image source in place of actual submission source.
    observation['observation_sql_types'] = True
    raw_queries = {}
    for name, item in d['initial']['raw'].items():
        sql = item.get('source_sql')
        if sql is None:
            raise ValueError('Trusted initial raw projection lacks its actual query: ' + name)
        raw_queries[name] = {'database': '/app/database/retail.duckdb', 'sql': sql}
    compiled = []
    for command in d['commands']:
        if command['manifest'] is None:
            continue
        nodes=command['manifest']['nodes']
        declared={item['relation'].lower() for item in observation['outputs']}
        selected={uid for uid,node in nodes.items() if node.get('resource_type')=='model'
            and (node.get('schema','')+'.'+node.get('alias',node.get('name',''))).lower() in declared}
        if context['qualified']['style']=='batch03':
            # This frozen engine checks all actual model nodes and their run_results.
            selected={uid for uid,node in nodes.items() if node.get('resource_type')=='model'}
        pending=list(selected)
        while pending:
            uid=pending.pop()
            for parent in nodes[uid].get('depends_on',{}).get('nodes',[]):
                if parent in nodes and nodes[parent].get('resource_type')=='model' and parent not in selected:
                    selected.add(parent);pending.append(parent)
        for uid, node in nodes.items():
            if uid in selected and isinstance(node.get('compiled_code'), str):
                compiled.append({'task_key': key, 'unique_id': uid, 'model': node['name'],
                    'trial': context['trial'].name,
                    'manifest': io.evidence_ref(command['manifest_path'], ROOT), 'sql': node['compiled_code']})
    request = {'observation': observation, 'raw_queries': raw_queries, 'compiled_sql': compiled,
               'fixture_only': binding['fixture_only'], 'model_requests': 0}
    request_ref = save(output / 'probe-request.json', request)
    image = context['production'][key]['image']
    if not io.evidence_ok(image, ROOT):
        raise ValueError('Locked task image changed')
    argv = [APPTAINER, 'exec', '--cleanenv', '--containall', '--no-mount',
            'home,tmp,cwd,hostfs,bind-paths', '--pwd', '/', '--bind', str(ROOT) + ':/repo:ro',
            '--bind', str(output) + ':/audit:rw', *mounts, str(ROOT / image['path']),
            '/usr/local/bin/python3', '/repo/scripts/difficulty_deb_probe.py',
            '--request', '/audit/probe-request.json', '--output', '/audit/observed']
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600,
                          env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'})
    (output / 'probe.log').write_text(proc.stdout + proc.stderr)
    process = save(output / 'process.json', {'argv': argv, 'returncode': proc.returncode,
        'log': io.evidence_ref(output / 'probe.log', ROOT), 'request': request_ref,
        'image': image, 'copies': hashes, 'model_requests': 0,
        'code_refs': [io.evidence_ref(ROOT / 'scripts' / name, ROOT) for name in
                      ('difficulty_deb_score_v14.py', 'difficulty_deb_probe.py', 'mechanical_observer.py', 'context_probe.py')]})
    if proc.returncode:
        raise ValueError('Read-only actual submission probe failed; see ' + str(output / 'process.json'))
    probe = io.read_json(output / 'observed/probe.json')
    if (not probe['database_and_wal_unchanged'] or probe['hashes_before']!=expected_hashes or probe['hashes_after']!=expected_hashes):
        raise ValueError('Probe did not read the complete original DB/WAL state unchanged')
    raw_status = {}
    for name, item in probe['raw'].items():
        path = output / 'observed' / item['file']
        if io.file_hash(path) != item['sha256']:
            raise ValueError('Raw observation changed')
        initial = d['initial']['raw'][name]
        raw_status[name] = {
            'same_columns': [x.lower() for x in initial['columns']] == [x.lower() for x in item['columns']],
            'same_rows': typed_bag(d['capture'].runtime(initial)) == typed_bag(path),
            'same_sql_types': [x[1] for x in initial['sql_schema']] == [x[1] for x in item['sql_schema']],
            'initial': {'path': str(d['capture'].runtime(initial)), 'sha256': initial['sha256']},
            'actual': io.evidence_ref(path, ROOT)}
    return {'snapshot': io.evidence_ref(output / 'observed/snapshot.json', ROOT),
            'probe': io.evidence_ref(output / 'observed/probe.json', ROOT),
            'process': process, 'raw_status': raw_status}


def minimal_checker_request(context, observed, events=None, output=None):
    key = context['binding']['task_key']; style = context['qualified']['style']
    if style.startswith('phase4_'):
        return {'task_key':key,'audit_dir':str(context['batch'].parent),'snapshot':observed['snapshot'],
            'original_snapshot':observed['snapshot'],'reconstruction_snapshot':io.evidence_ref(context['trial']/'agent/audit-submission/snapshot.json',ROOT),
            'probe':observed['probe'],'original_events':io.evidence_ref(output/'original-qwen-actions.json',ROOT),
            'stage':'agent-submission','output_dir':str(output)}
    from difficulty_deb_phase3_delivery import resolve as resolve_delivery
    delivery,delivery_ref,delivery_manifest=resolve_delivery(context,ROOT)
    original_snapshot=observed['snapshot']
    reconstruction_snapshot=io.evidence_ref(context['trial']/'agent/audit-submission/snapshot.json',ROOT)
    observed=dict(observed)
    if delivery['scoring_basis']=='submitted_source_reconstruction':observed['snapshot']=reconstruction_snapshot
    request = {'task_key': key, 'snapshot': observed['snapshot'], 'stage': 'agent-submission',
               'candidate_snapshot': observed['snapshot'], 'candidate_stage': 'agent-submission'}
    request.update(original_snapshot=original_snapshot,reconstruction_snapshot=reconstruction_snapshot,probe=observed['probe'],
        delivery_contract_ref=delivery_ref,delivery_manifest_ref=delivery_manifest,scoring_basis=delivery['scoring_basis'],
        submission_kind=delivery['submission_kind'])
    d = context['fresh']
    initial = context['baseline']['initial_input_hashes']
    database = next(x for x in initial if x['path'] == '/app/database/retail.duckdb')
    request['initial_database_sha256'] = database['sha256']
    if style == 'spec':
        old = formal_request(context['qualified'])
        original = old.get('original_input')
        if original is None:
            raise ValueError('Qualified fixed-input projection is unavailable')
        load(original)
        request['original_input'] = original
        if key=='deb__dbt-coupon-effectiveness':
            request['compiled_outputs']={name:{'data':io.evidence_ref(d['capture'].runtime(value),ROOT),
                'columns':value['columns'],'row_count':value['rows'],
                'compiled_sql':io.evidence_ref(d['capture'].runtime(value['sql']),ROOT)}
                for name,value in d['final']['compiled'].items()}
        if events is not None:
            request['trusted_workflow'] = spec_workflow(context, observed, events, output, ROOT)
    if style == 'request' and events is not None:
        request['workflow'] = direct_workflow(context, observed['snapshot'], events, output, ROOT)
    if style == 'batch03':
        parsed = load(observed['probe'])
        request.update(batch=str(context['batch']), trial=str(context['trial']),
            parsed_capture=save(output / 'parsed-capture.json', {'records': parsed['parsed'],
                'duckdb_version': parsed['duckdb_version'], 'source_probe': observed['probe']}))
    # Workflow deliberately absent until backed by the original model events.
    # Old author facts, author SQL hashes and generated candidate artifacts are
    # never copied into the request. Frozen checker output retains the gap.
    return request


def score(audit_dir, output):
    output = Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Use a new SCRATCH output directory')
    context = verify(audit_dir)
    output.mkdir(parents=True)
    binding = context['binding']; key = binding['task_key']
    events = inspect_trial(context['original_trial'], ROOT)
    event_ref = save(output / 'original-qwen-actions.json', events)
    observation = observe_submission(context, output / 'submission')
    save(output / 'submission-binding.json', observation)
    request = minimal_checker_request(context, observation, events, output)
    request_ref = save(output / 'checker-request.json', request)
    checker = context['qualified']['checker']
    argv = [sys.executable, str(ROOT / 'scripts/difficulty_deb_worker_v6.py'),
        '--checker', str(checker), '--request', str(output / 'checker-request.json'),
        '--root', str(ROOT), '--style', context['qualified']['style'],
        '--output', str(output / 'frozen-checker-result.json')]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600,
                          env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'})
    (output / 'checker.log').write_text(proc.stdout + proc.stderr)
    save(output / 'checker-process.json', {'argv': argv, 'returncode': proc.returncode,
        'log': io.evidence_ref(output / 'checker.log', ROOT), 'model_requests': 0})
    if proc.returncode:
        raise ValueError('Frozen checker execution failed')
    result = io.read_json(output / 'frozen-checker-result.json')
    action_gate = (result.get('original_action_gate') or {'status':'unknown','reward':None,'reason':'Phase4 handler has not attested required original model actions.'}) if context['qualified']['phase']==4 else declared_action_gate(key, events, context['fresh']['request']['project_root'])
    if context['qualified']['phase']==3 and request['scoring_basis']=='submitted_source_reconstruction':
        from difficulty_deb_actions import source_action_gate
        action_gate=source_action_gate(key,events,context['fresh']['request']['project_root'])
    if context['qualified']['phase']==3:
        result.update(delivery_contract_ref=request['delivery_contract_ref'],scoring_basis=request['scoring_basis'],
            mode=request['scoring_basis'],submission_kind=request['submission_kind'],evaluated_snapshot=request['snapshot'],
            original_snapshot=request['original_snapshot'],
            scope='Evaluate the exact delivered source reconstruction or actual database according to the hash-bound reviewed task delivery. Original model actions remain separate.')
    result['original_action_gate'] = action_gate
    if result.get('reward') != 0 and action_gate['reward'] != 1:
        result.update(reward=action_gate['reward'], status='unknown' if action_gate['reward'] is None else 'scored')
    result['actual_submission_capture'] = binding['extracted']['capture']
    result['actual_original_actions'] = event_ref
    raw_valid = all(r['same_columns'] and r['same_rows'] and r['same_sql_types']
                    for r in observation['raw_status'].values())
    result['original_input_identity'] = {'complete': raw_valid, 'relations': observation['raw_status'],
        'scope': 'Exact typed values and SQL types of every qualified raw projection in the actual submitted DB.'}
    if not raw_valid:
        result.update(reward=None, status='unknown',
            reason='The actual submitted raw input state differs from qualified fixed inputs; original native score retained, task validity requires review.')
    result['adapter_limitations'] = ([] if key == 'deb__dbt-fix-cac-payback-waterfall' or context['qualified']['phase']==4 else
        ['Task-specific original-action/lineage bridge is not yet complete; missing evidence remains unknown.'])
    result_ref = save(output / 'audit-result.json', result)
    dependency_names = ['difficulty_deb_score_v14.py', 'difficulty_deb_binding_v3.py',
        'difficulty_deb_registry_v2.py', 'difficulty_deb_probe.py', 'difficulty_deb_events.py',
        'difficulty_deb_workflow_v3.py', 'difficulty_deb_worker_v6.py', 'difficulty_submission.py',
        'prepare_difficulty_audit_v2.py', 'phase3_capture.py', 'mechanical_observer.py', 'context_probe.py']
    dependency_names += ['phase4_capture.py','difficulty_submission_v2.py','difficulty_deb_prepare.py']
    if context['qualified']['phase']==4:
        dependency_names.append('difficulty_deb_'+context['qualified']['style']+{'phase4_customer':'_v4','phase4_pipeline':'_v3','phase4_operations':'_v3'}.get(context['qualified']['style'],'')+'.py')
    adapter_refs=list(result.get('adapter_code_refs',[]))
    if context['qualified']['phase']==3:
        adapter_refs += [request['delivery_contract_ref'],request['delivery_manifest_ref'],
            io.evidence_ref(ROOT/'scripts/difficulty_deb_phase3_delivery.py',ROOT),io.evidence_ref(ROOT/'scripts/difficulty_deb_actions.py',ROOT)]
    if binding.get('preparer'):adapter_refs.append(binding['preparer'])
    for hook in binding.get('collector_plan',{}).get('current_hooks',[]):
        if hook.get('source'):adapter_refs.append(hook['source'])
    # An actual helper/preparer is part of the scoring provenance even when
    # it is not itself the frozen semantic checker.
    adapter_refs=list({(x['path'],x['sha256']):x for x in adapter_refs}.values())
    for item in adapter_refs:
        if not io.evidence_ok(item,ROOT):raise ValueError('Changed extra adapter dependency')
    code_refs=[io.evidence_ref(ROOT / 'scripts' / n, ROOT) for n in dependency_names]
    code_refs += [item for item in adapter_refs if item not in code_refs]
    dependencies = save(output / 'adapter-dependencies.json', {'code_refs': code_refs,
        'checker_dependencies': context['qualified']['overlay']['code_refs'],
        'image': context['production'][key]['image']})
    receipt = {'schema_version': 1, 'adapter_family': 'deb', 'task_key': key, 'task_version': binding['task_version'],
        'original_result': binding['original_result'], 'original_native_rewards':
            (context['original'].get('verifier_result') or {}).get('rewards'),
        'original_capture': binding['extracted']['capture'],
        'source_binding': context['manifest']['source_binding'],
        'rebuild_result': context['rebuild_result'], 'qualified_review': binding['qualified_review'],
        'checker_version': context['qualified']['record']['audit']['checker_version'],
        'checker': io.evidence_ref(checker, ROOT), 'checker_request': request_ref,
        'audit_result': result_ref, 'audit_reward': result['reward'], 'audit_status': result['status'],
        'fixture_only': binding['fixture_only'], 'model_requests': 0, 'training_prohibited': True,
        'adapter': io.evidence_ref(Path(__file__), ROOT),
        'adapter_dependencies': dependencies,
        'delivery_contract_ref':result.get('delivery_contract_ref'),'scoring_basis':result.get('scoring_basis','actual_database'),'evaluated_snapshot':result.get('evaluated_snapshot',observation['snapshot']),
        'scope': 'Frozen qualified checker applied according to the hash-bound delivery contract. Original pre-native DB and exact-source reconstruction are retained separately; reconstruction never proves original model actions.'}
    save(output / 'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); result = score(a.audit_dir, a.output)
    print({k: result[k] for k in ('task_key', 'audit_reward', 'audit_status', 'fixture_only')})

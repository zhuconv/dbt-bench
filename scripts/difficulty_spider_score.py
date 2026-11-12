"""Score the actual Spider submission; never substitute a clean rebuild's DB."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from audit_tables import decode
from difficulty_spider_registry import load
from difficulty_spider_binding import verify
from difficulty_spider_workflow import original_actions, fresh_graph
from mechanical_observer import encode

APPTAINER = '/opt/apps/tacc-apptainer/1.4.1/bin/apptainer'


def save(path, value):
    if path.exists():
        raise ValueError('Preserve previous evidence: ' + str(path))
    io.write_json(path, json.loads(json.dumps(value, default=encode, allow_nan=False)))
    return io.evidence_ref(path, ROOT)


def typed_bag(path):
    def token(v):
        if isinstance(v, (datetime, date, time)):
            return type(v).__name__, v.isoformat()
        if isinstance(v, Decimal):
            return 'Decimal', v
        if isinstance(v, list):
            return 'list', tuple(token(x) for x in v)
        if isinstance(v, dict):
            return 'dict', tuple(sorted((k, token(x)) for k, x in v.items()))
        return type(v).__name__, v
    return Counter(tuple(token(decode(x)) for x in json.loads(line)) for line in path.read_text().splitlines())


def original_event_evidence(trial):
    paths = list((trial / 'agent/qwen-sessions').glob('**/chats/*.jsonl'))
    if len(paths) != 1:
        return {'reward': None, 'status': 'unknown', 'reason': 'Exactly one complete main Qwen session not established', 'source_logs': []}
    events = [json.loads(x) for x in paths[0].read_text().splitlines() if x.strip()]
    return {**original_actions(events), 'source_logs': [io.evidence_ref(paths[0], ROOT)]}


def observe(context, output):
    output.mkdir(parents=True, exist_ok=False)
    raw = context['qualified']['raw']
    raw_ref = context['qualified']['raw_ref']
    copies = output / 'database-copies'
    copies.mkdir()
    mounts = []
    copy_refs = []
    database_paths = context['request']['observation']['database_paths']
    for database in database_paths:
        entries = [x for x in context['original_capture']['databases'] if x['path'] == database]
        if len(entries) != 1 or not entries[0]['exists']:
            raise ValueError('Actual submitted database absent; do not fall back to image input')
    for item in context['original_capture']['databases']:
        if not item['exists']:
            continue
        source = context['original_trial'] / 'artifacts/difficulty-submission' / item['file']
        copied = copies / Path(item['file']).name
        shutil.copyfile(source, copied)
        if io.file_hash(copied) != item['sha256']:
            raise ValueError('Submission database copy differs')
        mounts += ['--bind', str(copied) + ':' + item['path'] + ':ro']
        copy_refs.append({'source': io.evidence_ref(source, ROOT), 'copy': io.evidence_ref(copied, ROOT)})
    observation = deepcopy(context['request']['observation'])
    observation['files'] = []
    observation['observation_sql_types'] = True
    queries = [{'database': raw['database'], 'relation': t['relation'], 'query': t['query']} for t in raw['tables']]
    request = {'observation': observation, 'raw_queries': queries,
               'fixture_only': context['binding']['fixture_only'], 'model_requests': 0}
    request_ref = save(output / 'request.json', request)
    image = context['production'][context['binding']['task_key']]['image']
    if not io.evidence_ok(image, ROOT):
        raise ValueError('Locked image changed')
    argv = [APPTAINER, 'exec', '--cleanenv', '--containall', '--no-mount', 'home,tmp,cwd,hostfs,bind-paths',
            '--pwd', '/', '--bind', str(ROOT) + ':/repo:ro', '--bind', str(output) + ':/audit:rw',
            *mounts, str(ROOT / image['path']), '/usr/local/bin/python3', '/repo/scripts/difficulty_spider_probe.py',
            '--request', '/audit/request.json', '--output', '/audit/observed']
    process = subprocess.run(argv, capture_output=True, text=True, timeout=900,
                             env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'})
    (output / 'probe.log').write_text(process.stdout + process.stderr)
    save(output / 'process.json', {'argv': argv, 'returncode': process.returncode,
        'request': request_ref, 'image': image, 'copies': copy_refs,
        'log': io.evidence_ref(output / 'probe.log', ROOT), 'model_requests': 0})
    if process.returncode:
        raise ValueError('Actual DB observation failed; inspect retained process log')
    probe = io.read_json(output / 'observed/probe.json')
    if not probe['database_and_wal_unchanged']:
        raise ValueError('Submitted DB/WAL changed')
    actual = io.unique_index(probe['raw'], 'relation')
    raw_folder = (ROOT / raw_ref['path']).parent
    identity = []
    for table in raw['tables']:
        observed = actual[table['relation']]
        old = io.contained(raw_folder, table['file'])
        new = io.contained(output / 'observed', observed['file'])
        if io.file_hash(old) != table['sha256'] or io.file_hash(new) != observed['sha256']:
            raise ValueError('Raw projection artifact changed')
        identity.append({'relation': table['relation'], 'same_columns': table['columns'] == observed['columns'],
                         'same_types': table['sql_types'] == observed['sql_types'],
                         'same_values': typed_bag(old) == typed_bag(new),
                         'qualified_input': io.evidence_ref(old, ROOT), 'actual_input': io.evidence_ref(new, ROOT)})
    sources = io.unique_index(context['binding']['extracted']['source_files'], 'path')
    seeds = []
    for seed in raw.get('seed_files', []):
        source = seed['source']
        relative = 'dbt_packages/' + source.split('/dbt_packages/', 1)[1] if '/dbt_packages/' in source else None
        seeds.append({'source': source, 'captured_relative': relative,
                      'same': relative in sources and sources[relative]['sha256'] == seed['sha256']})
    return {'snapshot': io.evidence_ref(output / 'observed/snapshot.json', ROOT),
            'probe': io.evidence_ref(output / 'observed/probe.json', ROOT),
            'raw_identity': identity, 'seed_identity': seeds,
            'inputs_valid': all(all(x[k] for k in ('same_columns', 'same_types', 'same_values')) for x in identity)
                            and all(x['same'] for x in seeds)}


def score(audit_dir, output):
    output = Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Use a new SCRATCH output directory')
    context = verify(audit_dir)
    output.mkdir(parents=True)
    binding = context['binding']
    observation = observe(context, output / 'actual-submission')
    observed_ref = save(output / 'observed-binding.json', observation)
    actions = original_event_evidence(context['original_trial'])
    action_ref = save(output / 'original-actions.json', actions)
    graph = fresh_graph(context['fresh'], context['qualified']['raw'], context['request']['observation'], context['baseline'])
    graph_ref = save(output / 'fresh-source-graph.json', graph)
    request = {'task_key': binding['task_key'], 'task_version': binding['task_version'],
               'qualified_review': binding['qualified_review'], 'candidate_snapshot': observation['snapshot'],
               'actual_probe': observation['probe'],
               'fixture_only': binding['fixture_only'], 'model_requests': 0}
    request_ref = save(output / 'checker-request.json', request)
    argv = [sys.executable, str(ROOT / 'scripts/difficulty_spider_worker.py'),
            '--request', str(output / 'checker-request.json'), '--output', str(output / 'table-result.json')]
    process = subprocess.run(argv, capture_output=True, text=True, timeout=900,
                             env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'})
    (output / 'checker.log').write_text(process.stdout + process.stderr)
    save(output / 'checker-process.json', {'argv': argv, 'returncode': process.returncode,
        'log': io.evidence_ref(output / 'checker.log', ROOT), 'model_requests': 0})
    if process.returncode:
        raise ValueError('Frozen table component failed; inspect retained log')
    table = io.read_json(output / 'table-result.json')
    values = [table['reward'], graph['reward'], actions['reward']]
    reward = 0 if 0 in values else 1 if all(x == 1 for x in values) else None
    if not observation['inputs_valid']:
        reward = None
    result = {'task_key': binding['task_key'], 'target': 'audit', 'reward': reward,
              'status': 'scored' if reward is not None else 'unknown',
              'table_component': io.evidence_ref(output / 'table-result.json', ROOT),
              'source_workflow': graph_ref, 'actual_model_actions': action_ref,
              'actual_submission': observed_ref, 'fixture_only': binding['fixture_only'],
              'model_requests': 0, 'training_prohibited': True,
              'scope': 'Frozen table component scores the original pre-native DB. Exact submitted source is independently rebuilt. Actual all-model execution is decided only from original paired Qwen events; no author SQL whitelist or gold substitution.'}
    result_ref = save(output / 'audit-result.json', result)
    dependencies = save(output / 'adapter-dependencies.json', {
        'code_refs': [io.evidence_ref(ROOT / 'scripts' / name, ROOT) for name in
            ('difficulty_spider_score.py', 'difficulty_spider_binding.py', 'difficulty_spider_registry.py',
             'difficulty_spider_probe.py', 'difficulty_spider_worker.py', 'difficulty_spider_workflow.py',
             'difficulty_submission.py', 'difficulty_submission_v2.py', 'prepare_difficulty_audit_v2.py', 'phase3_capture.py',
             'difficulty_deb_events.py', 'mechanical_observer.py', 'context_probe.py')],
        'checker_dependencies': context['qualified']['overlay']['code_refs'],
        'image': context['production'][binding['task_key']]['image']})
    receipt = {k: binding[k] for k in ('task_key', 'task_version', 'fixture_only', 'original_result', 'qualified_review')}
    receipt.update(training_prohibited=True, model_requests=0, audit_reward=reward, audit_status=result['status'],
                   audit_result=result_ref, source_binding=context['manifest']['source_binding'],
                   checker=io.evidence_ref(context['qualified']['checker'], ROOT),
                   adapter=io.evidence_ref(Path(__file__), ROOT), adapter_dependencies=dependencies,
                   checker_request=request_ref, rebuild_result=context['rebuild_result'])
    save(output / 'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = score(args.audit_dir, args.output)
    print({k: result[k] for k in ('task_key', 'audit_reward', 'audit_status', 'fixture_only')})

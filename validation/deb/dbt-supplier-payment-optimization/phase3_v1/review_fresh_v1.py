"""Replay payment audit against actual two clean blind builds and native snapshots."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / 'scripts'), str(ROOT / 'src')]
from dbt_bench import io
from phase3_capture import verify_trial
from source_inspection_v2 import inspect
from run_audit_v4 import run

TASK = 'deb__dbt-supplier-payment-optimization'


def save(path, value):
    if path.exists():
        raise ValueError('Never replace a review')
    # Decimal/date diagnostics retain their exact textual values; the trusted
    # submitted tables themselves retain the observer's typed representation.
    io.write_json(path, json.loads(json.dumps(value, default=str, allow_nan=False)))
    return io.evidence_ref(path, ROOT)


def tree(items):
    result = {}
    for item in items:
        if item.get('error'):
            raise ValueError('Incomplete model tree capture')
        if item['relative_path'] != 'models':
            continue
        for f in item.get('files', []):
            result['models/' + f['path']] = f['sha256']
    return result


def values(snapshot, relation):
    j = io.read_json(snapshot)
    entry = next(x for x in j['outputs'] if x['specification']['relation'] == relation)
    if entry.get('error'):
        return None
    path = snapshot.parent / entry['file']
    if io.file_hash(path) != entry['sha256']:
        raise ValueError('Output export changed')
    columns = [c['name'].lower() for c in entry['schema']]
    return {'schema': sorted((c['name'].lower(), c['type']) for c in entry['schema']),
            'rows': sorted(json.dumps(dict(zip(columns, json.loads(line))), sort_keys=True)
                           for line in path.read_text().splitlines())}


def review(batch, parsed, output):
    output.mkdir(parents=True, exist_ok=False)
    parse = io.read_json(parsed)
    assert parse['duckdb_version'] == '1.2.2' and parse['task_database_opened'] is False
    records = []
    for result in sorted(batch.glob('jobs/*/*/result.json')):
        raw_result = io.read_json(result)
        if Path(raw_result['config']['task']['path']).name != TASK + '--phase3-blind-v1':
            continue
        trial = result.parent
        v = verify_trial(batch, trial)
        cap, e = v['capture'], v['execution']
        assert raw_result['verifier_result']['rewards'] == {'reward': 1.0} and not raw_result.get('exception_info')
        d = output / trial.name
        d.mkdir()
        rows = {}
        for name, export in v['initial']['raw'].items():
            if name.startswith('main__'):
                continue
            columns = [c.lower() for c in export['columns']]
            rows[name.split('__')[-1].upper()] = [dict(zip(columns, json.loads(line)))
                                                for line in cap.runtime(export).read_text().splitlines()]
        original = save(d / 'original-input.json', {'task_key': TASK, 'rows': rows,
            'source_refs': [io.evidence_ref(cap.runtime(x), ROOT) for x in v['initial']['raw'].values()],
            'scope': 'Original initial raw tables; complete raw and staging projections are unchanged after actual build.'})
        command = next(c for c in v['commands'] if 'run' in c['run']['command'])
        m, rr = command['manifest'], command['run_results']
        files = {name: p.read_text() for name, p in v['final_files'].items()
                 if name.endswith(('.sql', '.yml')) and not name.startswith('phase3_target_v2/')}
        parsed_records = [r for r in parse['records'] if r['trial'] == str(trial)]
        assert len(parsed_records) == 1
        for p in parsed_records:
            assert p['manifest'] == io.evidence_ref(command['manifest_path'], ROOT)
        a, z = tree(e['initial_trees']), tree(e['final_trees'])
        changed = [name for name in a.keys() | z.keys() if a.get(name) != z.get(name)]
        inspection = inspect(parsed_records, m, files, changed, True)
        uid = parsed_records[0]['unique_id']
        facts = {'files': files, 'manifest': {**m, 'nodes': {uid: m['nodes'][uid]}},
            'run_results': {**rr, 'results': [r for r in rr['results'] if r['unique_id'] == uid]},
            'trusted_complete_inventory': True, 'trusted_fresh_process': True,
            'fresh_source_invocation_binding': True, 'environment': e['environment'],
            'commands': e['runs'],
            'backend_before_install': {'shell': v['backend_shell'],
                'install_after_utc': e['initial_capture_hooks'][0]['finished_at_utc'],
                'sequential_trusted_driver_verified': True},
            **inspection,
            'evidence': [*cap.evidence.values(), io.evidence_ref(parsed, ROOT),
                         io.evidence_ref(ROOT / 'scripts/phase3_capture.py', ROOT),
                         io.evidence_ref(Path(__file__), ROOT)],
            'scope': 'Actual immutable source/process/raw binding; manifest/result retain the required node, full source artifacts hash-bound above.'}
        wf = save(d / 'workflow.json', facts)
        stages, bags = [], []
        for part in ('agent/audit-submission', 'verifier/mechanical-before', 'verifier/mechanical-after'):
            path = trial / part / 'snapshot.json'
            snapshot = cap.read(path)
            for f in v['request']['submission']['files']:
                observed = next(x for x in snapshot['files'] if x['path'] == v['request']['project_root'] + '/' + f['path'])
                assert observed['exists'] and observed['sha256'] == f['sha256']
            spec = {'task_key': TASK, 'original_input': original,
                'candidate_snapshot': io.evidence_ref(path, ROOT), 'candidate_stage': snapshot['stage'],
                'trusted_workflow': wf}
            sr = save(d / (snapshot['stage'] + '-request.json'), spec)
            audit = run(spec, ROOT)
            ar = save(d / (snapshot['stage'] + '-audit.json'), audit)
            bag = values(path, 'main.supplier_payment_optimization')
            bags.append(bag)
            stages.append({'stage': snapshot['stage'], 'request': sr, 'audit': ar,
                'reward': audit['reward'], 'non_pass': {n: c for n, c in audit.get('checks', {}).items() if c['status'] != 'pass'}})
            print(trial.name, snapshot['stage'], audit['reward'], audit.get('reason'), flush=True)
        assert bags[0] == bags[1] == bags[2] and bags[0] is not None
        spec = {'task_key': TASK, 'original_input': original,
            'candidate_snapshot': io.evidence_ref(trial / 'agent/audit-initial/snapshot.json', ROOT), 'candidate_stage': 'initial'}
        nr = save(d / 'initial-request.json', spec)
        no = run(spec, ROOT)
        na = save(d / 'initial-audit.json', no)
        records.append({'trial': str(trial), 'original_input': original, 'workflow': wf, 'stages': stages,
            'initial': {'request': nr, 'audit': na, 'reward': no['reward']},
            'context_values_equal': True, 'raw_staging_unchanged': True,
            'canonical_output_digest': hashlib.sha256(json.dumps(bags[0], sort_keys=True).encode()).hexdigest(),
            'evidence': list(cap.evidence.values())})
    assert len(records) == 2
    assert len({r['canonical_output_digest'] for r in records}) == 1
    return save(output / 'review.json', {'task_key': TASK, 'records': records,
        'code_refs': [io.evidence_ref(p, ROOT) for p in Path(__file__).parent.glob('*.py')] +
                     [io.evidence_ref(Path(__file__).with_name('policy.json'), ROOT)],
        'model_requests': 0, 'native_rewards_modified': False})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--parsed', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(review(a.batch, a.parsed, a.output))

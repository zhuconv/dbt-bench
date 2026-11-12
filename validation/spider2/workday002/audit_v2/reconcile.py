"""Reconcile isolated collector trials and an exact retained-model replay."""
import argparse
from collections import Counter
from pathlib import Path
import sys
from checker import score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(HERE.parent / 'audit_v1'))
from capture import verify_capture
from dbt_bench.io import evidence_ref, evidence_ok, file_hash, read_json, tree_hash, write_json

KEY = 'spider2__workday002'
ref = lambda p: evidence_ref(p, ROOT)


def run(out, controls, reference, replay):
    out.mkdir(parents=True, exist_ok=False)
    records, seen = [], set()
    proven = read_json(reference / 'reconciliation.json')
    assert proven['complete'] and proven['outcomes'] == {'accepted_correct': 2}
    proofs = {r['trial_id']: r for r in proven['trials']}
    for batch, kind in [(controls, 'controls'), (reference, 'reference')]:
        manifest = read_json(batch / 'manifest.json')
        assert manifest['capture_kind'] == kind
        for name in ['config', 'collector', 'capture_installer', 'capture_preparation']:
            assert evidence_ok(manifest[name], ROOT), name
        for name, change in manifest['read_only_capture'].items():
            task = batch / 'tasks' / name
            assert tree_hash(task) == change['after_task_hash']
            for relative, digest in change['prior_verifier_files'].items():
                path = task / 'tests' / ('audit-original-test.sh' if relative == 'test.sh' else relative)
                assert file_hash(path) == digest
        counts = Counter()
        for path in sorted((batch / 'jobs').glob('*/*/result.json')):
            raw = read_json(path)
            task = Path(raw['config']['task']['path'])
            assert task.parent == batch / 'tasks' and task.name in manifest['read_only_capture']
            assert raw['id'] not in seen
            seen.add(raw['id'])
            agent_name = raw['config']['agent']['name']
            counts[agent_name] += 1
            assert raw['exception_info'] is None
            rewards = (raw.get('verifier_result') or {}).get('rewards')
            assert rewards == {'reward': 0.0 if agent_name == 'nop' else 1.0}
            capture = path.parent / 'verifier/workday-audit'
            request = dict(schema_version=2, task_key=KEY, data_scope='original_pinned_inputs',
                before=ref(capture / 'before-native/snapshot.json'),
                after=ref(capture / 'after-native/snapshot.json'), workflow=None)
            if kind == 'reference':
                proof = proofs[raw['id']]
                verify_capture(ROOT, proof)
                agent = path.parent / 'agent'
                state = read_json(agent / 'workflow-state.json')
                request['workflow'] = dict(capture_kind='reviewed_validation_case_command_capture',
                    executed_engine=ref((ROOT / proof['case']['path']).parent / 'engine.py'),
                    observation=ref(agent / 'workflow-state.json'),
                    command_logs={c['log']: ref(agent / c['log']) for c in state['commands']},
                    run_results=ref(agent / 'dbt-run_results.json'), dbt_manifest=ref(agent / 'dbt-manifest.json'))
            destination = out / 'inputs' / (raw['id'] + '.json')
            write_json(destination, request)
            audit = score(ROOT, destination)
            expected = 'pass' if kind == 'reference' else 'fail' if agent_name == 'nop' else 'unknown'
            assert audit['status'] == expected, audit
            audit_path = out / 'audits' / (raw['id'] + '.json')
            write_json(audit_path, audit)
            records.append(dict(trial_id=raw['id'], kind=kind, agent=agent_name,
                result=ref(path), native_rewards=rewards, audit=ref(audit_path),
                input_manifest=ref(destination), status=audit['status'], audit_reward=audit['audit_reward'],
                data_status=audit['data']['status'], workflow_status=audit['workflow']['status']))
        assert counts == ({'oracle': 2, 'nop': 2} if kind == 'controls' else {'oracle': 2})
        assert sum(counts.values()) == len(manifest['planned'])
    replay_result = read_json(replay / 'result.json')
    assert replay_result['passed'] and not replay_result.get('error') and not replay_result.get('cleanup_error')
    assert replay_result['original_native_result'] == replay_result['replay_native_result'] == {'rewards': {'reward': 1.0}}
    assert replay_result['benchmark_trials'] == replay_result['model_requests'] == 0
    for name in ['source_trial', 'submission_spec', 'script', 'collector']:
        assert evidence_ok(replay_result[name], ROOT)
    request = dict(schema_version=2, task_key=KEY, data_scope='original_pinned_inputs', workflow=None,
        before=ref(replay / 'snapshots/before-native/snapshot.json'),
        after=ref(replay / 'snapshots/after-native/snapshot.json'))
    model_manifest = out / 'inputs/model-replay.json'
    write_json(model_manifest, request)
    model = score(ROOT, model_manifest)
    assert model['status'] == 'unknown' and model['audit_reward'] is None
    write_json(out / 'audits/model-replay.json', model)
    summary = dict(schema_version=1, task_key=KEY, certified=False, planned=6, observed=len(records),
        complete=True, missing=[], duplicates=[], records=records,
        statuses=dict(Counter(r['status'] for r in records)),
        model_replay=dict(result=ref(replay / 'result.json'), input_manifest=ref(model_manifest),
            audit=ref(out / 'audits/model-replay.json'), status=model['status'], audit_reward=model['audit_reward'],
            unresolved_data_cells=len(model['data']['unknown'])),
        batches=[ref(b / 'manifest.json') for b in [controls, reference]],
        reference_proof=ref(reference / 'reconciliation.json'),
        scoring_code=ref(HERE / 'checker.py'), collector=ref(HERE / 'collect.py'),
        input_policy=ref(HERE / 'input-policy.json'), reconciler=ref(Path(__file__)),
        new_benchmark_trials=6, new_model_trials=0, new_submission_replays=1,
        scope='Read-only before/after-native snapshots agree in all seven executions. Copy-gold and missing workflow remain unknown; nop rejects. No original score changed or task certified.')
    write_json(out / 'summary.json', summary)
    print({k: summary[k] for k in ['planned', 'observed', 'complete', 'statuses']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--controls', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    args = parser.parse_args()
    run(*(p.resolve() for p in [args.output_dir, args.controls, args.reference, args.replay]))

"""Bind captured Session audits to native controls, workflow proof and model replay."""
import argparse
from collections import Counter
from pathlib import Path
import sys
from checker import score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(HERE.parent / 'audit_v1'))
from capture import verify
from dbt_bench.io import evidence_ref, evidence_ok, file_hash, read_json, tree_hash, write_json

KEY = 'deb__dbt-session-attribution'
ref = lambda p: evidence_ref(p, ROOT)


def run(out, controls, reference, replay, failed_startup, failed_replay):
    out.mkdir(parents=True, exist_ok=False)
    records, seen = [], set()
    proven = read_json(reference / 'reconciliation.json')
    assert proven['complete'] and proven['outcomes'] == {'accepted_correct': 2}
    proofs = {r['trial_id']: r for r in proven['trials']}
    for batch, kind in [(controls, 'controls'), (reference, 'reference')]:
        manifest = read_json(batch / 'manifest.json'); assert manifest['capture_kind'] == kind
        for name in ['config', 'collector', 'capture_installer', 'capture_preparation']:
            assert evidence_ok(manifest[name], ROOT), name
        for name, change in manifest['read_only_capture'].items():
            task = batch / 'tasks' / name
            assert tree_hash(task) == change['after_task_hash']
            for relative, digest in change['prior_verifier_files'].items():
                p = task / 'tests' / ('audit-original-test.sh' if relative == 'test.sh' else relative)
                assert file_hash(p) == digest, p
        counts = Counter()
        for path in sorted((batch / 'jobs').glob('*/*/result.json')):
            raw = read_json(path); task = Path(raw['config']['task']['path'])
            assert task.parent == batch / 'tasks' and task.name in manifest['read_only_capture']
            assert raw['id'] not in seen; seen.add(raw['id'])
            agent_name = raw['config']['agent']['name']; counts[agent_name] += 1
            assert raw['exception_info'] is None
            rewards = (raw.get('verifier_result') or {}).get('rewards')
            assert rewards == {'reward': 0.0 if agent_name == 'nop' else 1.0}
            capture = path.parent / 'verifier/session-audit'
            request = dict(schema_version=2, task_key=KEY, data_scope='original_pinned_inputs', workflow=None,
                before=ref(capture / 'before-native/snapshot.json'), after=ref(capture / 'after-native/snapshot.json'))
            if kind == 'reference':
                agent, proof = verify(ROOT, batch, proofs[raw['id']])
                final = agent / proof['phases'][-1]
                request['workflow'] = dict(kind='reviewed_validation_case_snapshots',
                    inputs=ref(agent / 'evaluated-inputs.json'), before=ref(agent / 'before-case/snapshot.json'),
                    phases=[ref(agent / p / 'snapshot.json') for p in proof['phases']],
                    **{name: ref(final / name) for name in ['target/manifest.json', 'target/run_results.json']})
            destination = out / 'inputs' / (raw['id'] + '.json'); write_json(destination, request)
            audit = score(ROOT, destination)
            expected = 'pass' if kind == 'reference' else 'fail' if agent_name == 'nop' else 'unknown'
            assert audit['status'] == expected, audit
            audit_path = out / 'audits' / (raw['id'] + '.json'); write_json(audit_path, audit)
            records.append(dict(trial_id=raw['id'], kind=kind, agent=agent_name, result=ref(path),
                native_rewards=rewards, audit=ref(audit_path), input_manifest=ref(destination),
                status=audit['status'], audit_reward=audit['audit_reward'], data_status=audit['data']['status'],
                workflow_status=audit['workflow']['status']))
        assert counts == ({'oracle': 2, 'nop': 2} if kind == 'controls' else {'oracle': 2})
        assert sum(counts.values()) == len(manifest['planned'])
    replay_result = read_json(replay / 'result.json')
    assert replay_result['passed'] and not replay_result.get('error') and not replay_result.get('cleanup_error')
    assert replay_result['original_native_result'] == replay_result['replay_native_result'] == {'rewards': {'reward': 1.0}}
    assert replay_result['benchmark_trials'] == replay_result['model_requests'] == 0
    for name in ['source_trial', 'submission_spec', 'script', 'collector', 'transfer_helper']:
        assert evidence_ok(replay_result[name], ROOT), name
    model_manifest = out / 'inputs/model-replay.json'
    write_json(model_manifest, dict(schema_version=2, task_key=KEY, data_scope='original_pinned_inputs', workflow=None,
        before=ref(replay / 'snapshots/before-native/snapshot.json'),
        after=ref(replay / 'snapshots/after-native/snapshot.json')))
    model = score(ROOT, model_manifest)
    assert model['status'] == 'unknown' and model['audit_reward'] is None and model['data']['status'] == 'pass'
    write_json(out / 'audits/model-replay.json', model)
    failures = []
    for path in sorted((failed_startup / 'jobs').glob('*/*/result.json')):
        raw = read_json(path); error = raw.get('exception_info') or {}
        assert raw['id'] not in seen; seen.add(raw['id'])
        assert raw['verifier_result'] is None and error['exception_type'] == 'FileNotFoundError'
        assert error['exception_message'] == "[Errno 2] No such file or directory: 'singularity'"
        failures.append(dict(trial_id=raw['id'], result=ref(path), agent=raw['config']['agent']['name'],
            native_rewards=None, cause_category=2, confidence='high', stage='environment_start',
            reason='Invocation shell omitted the documented Apptainer PATH. No container, agent or verifier started.'))
    assert len(failures) == len(read_json(failed_startup / 'manifest.json')['planned']) == 4
    transfer_failure = read_json(failed_replay / 'result.json')
    assert not transfer_failure['passed'] and not transfer_failure.get('cleanup_error')
    assert transfer_failure['error']['type'] == 'OSError' and '[Errno 122]' in transfer_failure['error']['message']
    assert 'replay_native_result' not in transfer_failure
    summary = dict(schema_version=1, task_key=KEY, certified=False, complete=True, missing=[], duplicates=[],
        planned_scored=6, observed_scored=len(records), records=records, startup_failures=failures,
        statuses=dict(Counter(r['status'] for r in records)),
        model_replay=dict(result=ref(replay / 'result.json'), input_manifest=ref(model_manifest),
            audit=ref(out / 'audits/model-replay.json'), status=model['status'], audit_reward=model['audit_reward'],
            data_status=model['data']['status'], workflow_status=model['workflow']['status']),
        batches=[ref(b / 'manifest.json') for b in [controls, reference, failed_startup]],
        startup_retry={'original_batch': ref(failed_startup / 'manifest.json'), 'replacement_batch': ref(controls / 'manifest.json'),
                       'association': 'Whole four-slot validation batch repeated with corrected shell PATH; no model attempts involved.'},
        reference_proof=ref(reference / 'reconciliation.json'), scoring_code=ref(HERE / 'checker.py'),
        core=ref(HERE.parent / 'audit_v1/checker.py'), collector=ref(HERE / 'collect.py'),
        input_policy=ref(HERE.parent / 'audit_v1/input-policy.json'), reconciler=ref(Path(__file__)),
        failed_submission_replay=dict(result=ref(failed_replay / 'result.json'), cause_category=2,
            reason='Shared-filesystem quota exceeded during Harbor transient sanitized copy before grading. The retry uses node-local storage with the same sanitizer and exact-file checks.'),
        new_native_validation_records=10, new_scored_benchmark_trials=6, new_model_trials=0, new_submission_replays=2,
        scope='Seven unchanged before/after-native captures: six controls and one exact model replay. Four pre-start null-reward failures and one pre-grading replay failure retained separately. Missing workflow remains unknown; no original score changed or task certified.')
    write_json(out / 'summary.json', summary)
    print({k: summary[k] for k in ['complete', 'observed_scored', 'statuses', 'new_native_validation_records']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['output-dir', 'controls', 'reference', 'replay', 'failed-startup', 'failed-replay']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    run(*(p.resolve() for p in [args.output_dir, args.controls, args.reference, args.replay, args.failed_startup, args.failed_replay]))

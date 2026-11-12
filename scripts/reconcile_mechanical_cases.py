#!/usr/bin/env python3
"""Reconcile proof-gated full native cases, preserving all rewards and failures."""
import argparse
from collections import Counter
from pathlib import Path
from review_tools import ROOT, cached_hashes, terminal_result_paths
from dbt_bench import io
from mechanical_compare import compare_snapshots


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--continuation', type=Path, action='append', default=[],
                   help='Separate completion directory; original interrupted job remains unchanged')
    a = p.parse_args()
    manifest = io.read_json(a.batch / 'manifest.json')
    assert io.evidence_ok(manifest['config'], ROOT)
    config = io.read_json(ROOT / manifest['config']['path'])
    job = Path(config['jobs_dir']) / config['job_name']
    job_result = io.read_json(job / 'result.json')
    if not a.continuation:
        assert job_result['finished_at'], 'Job still running'
    paths = terminal_result_paths(a.batch, a.continuation)
    continuation_refs = []
    for directory in a.continuation:
        completion = io.read_json(directory / 'completion.json')
        continuation_refs.append(io.evidence_ref(directory / 'completion.json', ROOT))
        for plan in completion['plans']:
            cfg = io.read_json(ROOT / plan['config']['path'])
            continuation_refs.append(io.evidence_ref(Path(cfg['jobs_dir']) / cfg['job_name'] / 'result.json', ROOT))
    labels = {str(Path(t['path']).resolve()): Path(t['path']).name for t in config['tasks']}
    seen, ids, records = set(), set(), []
    with cached_hashes():
        for path in paths:
            raw = io.read_json(path)
            task = Path(raw['config']['task']['path'])
            label = labels[str(task.resolve())]
            assert label not in seen and raw['id'] not in ids and raw['finished_at']
            seen.add(label)
            ids.add(raw['id'])
            entry = manifest['tasks'][label]
            assert io.tree_hash(task) == entry['validation_copy_hash']
            assert all(io.evidence_ok(entry[n], ROOT) for n in ('case', 'engine', 'native_verifier'))
            proof_path = path.parent / 'agent/case-proof.json'
            proof = io.read_json(proof_path) if proof_path.exists() else {}
            shell_path = path.parent / 'agent/case-shell.json'
            shell = io.read_json(shell_path) if shell_path.exists() else {}
            before = path.parent / 'verifier/mechanical-before/snapshot.json'
            after = path.parent / 'verifier/mechanical-after/snapshot.json'
            submission = path.parent / 'agent/submission/snapshot.json'
            expected = {'negative': 'business_error', 'equivalent': 'legal_equivalent',
                        'reference_relative': 'reference_relative_equivalent'}[entry['kind']]
            valid = (proof.get('validity') == expected and proof.get('tested_checker_invoked_to_define_validity') is False
                     and shell.get('exit_code') == 0)
            rewards = (raw.get('verifier_result') or {}).get('rewards')
            # The shared wrapper gates the unchanged native entrypoint. Check
            # observation ordering too, rather than assuming proof precedes it.
            order = (shell.get('finished_at_utc') <= io.read_json(before).get('started_at_utc')
                     if shell.get('finished_at_utc') and before.exists() and io.read_json(before).get('started_at_utc') else None)
            if rewards is not None:
                assert valid and order is True, 'Native reward without prior valid proof: ' + label
            records.append({'task_key': entry['task_key'], 'case_label': label, 'kind': entry['kind'],
                'trial_id': raw['id'], 'result': io.evidence_ref(path, ROOT), 'native_rewards': rewards,
                'exception_info': raw.get('exception_info'), 'proof_valid_for_declared_scope': valid,
                'validity': proof.get('validity'), 'validity_scope': proof.get('validity_scope'),
                'baseline_correctness': proof.get('baseline_correctness'),
                'proof_completed_before_verifier': order, 'audit_reward': None,
                'submission_to_verifier': compare_snapshots(submission, before) if submission.exists() and before.exists() else None,
                'verifier_effects': compare_snapshots(before, after) if before.exists() and after.exists() else None,
                'artifacts': [io.evidence_ref(f, ROOT) for d in ('agent', 'verifier')
                              for f in sorted((path.parent / d).rglob('*')) if f.is_file()],
                'scope': entry.get('scope')})
        assert seen == set(manifest['tasks']), {'missing': sorted(set(manifest['tasks']) - seen)}
        assert len(records) == manifest['planned_trials']
    io.write_json(a.output, {'schema_version': 1, 'execution_backed': True,
        'manifest': io.evidence_ref(a.batch / 'manifest.json', ROOT), 'job_result': io.evidence_ref(job / 'result.json', ROOT),
        'continuations': continuation_refs,
        'records': records, 'observed_trials': len(records), 'missing': [], 'duplicate_ids': [],
        'model_requests': 0, 'native_rewards_modified': False, 'checker': io.evidence_ref(Path(__file__), ROOT),
        'scope': 'Actual full native cases; independent proof scope and per-rule coverage require review.'})
    print({'trials': len(records), 'validity': dict(Counter(r['validity'] for r in records))})


if __name__ == '__main__':
    main()

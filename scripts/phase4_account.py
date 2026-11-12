#!/usr/bin/env python3
"""Reconcile completed phase 4 Harbor batches; does not schedule or alter trials."""
import argparse
from collections import Counter
from pathlib import Path
import subprocess
import sys
from dbt_bench import io
from reconcile_audit_rebuild import reconcile
from review_tools import ROOT


def account(review):
    review = Path(review)
    parent = review.parent.parent
    batches, pending_cleanup = [], []
    for batch in sorted(parent.glob('phase4-*')):
        if not (batch / 'launch.json').is_file():
            continue
        manifest = io.read_json(batch / 'manifest.json')
        complete = (batch / 'launch-result.json').is_file()
        row = {'batch': batch.name, 'manifest': io.evidence_ref(batch / 'manifest.json', ROOT),
               'planned_trials': manifest['planned_trials'], 'complete': complete}
        if complete:
            destination = batch / 'reconciliation-v1.json'
            if not destination.exists():
                value = reconcile(batch)
                io.write_json(destination, value)
            else:
                value = io.read_json(destination)
            row.update(reconciliation=io.evidence_ref(destination, ROOT), observed_trials=value['observed_trials'],
                reward_counts=dict(Counter(str(t['native_rewards']) for t in value['trials'])),
                build_failures=sum(t.get('build_success') is False for t in value['trials']),
                exceptions=sum(bool(t['exception_info']) for t in value['trials']))
            if any(t.get('case_setup') for t in manifest['tasks'].values()):
                case_path = batch / 'case-reconciliation-v1.json'
                if not case_path.exists():
                    cases = []
                    for trial in value['trials']:
                        setup = manifest['tasks'][trial['label']].get('case_setup')
                        if not setup:
                            continue
                        directory = (ROOT / trial['result']['path']).parent / 'agent'
                        proof_path = directory / 'case-proof.json'
                        proof = io.read_json(proof_path) if proof_path.exists() else {}
                        artifacts = [io.evidence_ref(p, ROOT) for d in directory.glob('phase4-*')
                                     if d.is_dir() for p in d.rglob('*') if p.is_file()]
                        cases.append({'trial_id': trial['trial_id'], 'label': trial['label'],
                            'case_setup': setup, 'proof': io.evidence_ref(proof_path, ROOT) if proof else None,
                            'validity': proof.get('validity'),
                            'proof_precedes_native_and_is_independent': proof.get('validity') == setup['expected_validity']
                                and proof.get('tested_checker_invoked_to_define_validity') is False,
                            'native_rewards': trial['native_rewards'],
                            'native_outputs_equal_before_after': trial['native_checker_output_changes']['equal'],
                            'case_artifacts': artifacts,
                            'scope': 'Changed native outputs require review for rebuild/repair; native1 alone never proves a verifier miss.'})
                    io.write_json(case_path, {'schema_version': 1, 'model_requests': 0, 'cases': cases,
                                              'reconciliation': row['reconciliation']})
                row['case_reconciliation'] = io.evidence_ref(case_path, ROOT)
            if not (batch / 'cleanup-v1.json').exists():
                pending_cleanup.append(batch)
        batches.append(row)
    for batch in pending_cleanup:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify_container_cleanup.py'),
            '--jobs', str(batch / 'jobs'), '--output', str(batch / 'cleanup-v1.json')], cwd=ROOT,
            capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Cleanup check failed: ' + batch.name + ': ' + result.stderr)
    state = io.read_json(review / 'state.json')
    number = len(list(review.glob('execution-summary-v*.json'))) + 1
    output = review / f'execution-summary-v{number:02d}.json'
    summary = {'schema_version': 1, 'model_requests': 0, 'batches': batches,
        'complete_batches': sum(b['complete'] for b in batches),
        'running_batches': sum(not b['complete'] for b in batches),
        'terminal_trials': sum(b.get('observed_trials', 0) for b in batches),
        'scope': 'Execution accounting includes retained failed installation attempts and semantic cases; not audit certification.'}
    io.write_json(output, summary)
    state.update(running_batches=[b['batch'] for b in batches if not b['complete']],
                 completed_batches=[b['batch'] for b in batches if b['complete']],
                 execution_summary=io.evidence_ref(output, ROOT))
    io.write_json(review / 'state.json', state)
    return {k: summary[k] for k in ('complete_batches', 'running_batches', 'terminal_trials')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review', type=Path, required=True)
    a = p.parse_args()
    print(account(a.review))

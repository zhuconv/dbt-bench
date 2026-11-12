#!/usr/bin/env python3
"""Consume real checker replay, reconcile retained runs and render a no-draw preflight.

No subprocess, Slurm mutation, model request, container launch or random draw.
This report does not replace any historical measurement or audit receipt.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
from dbt_bench import io
from reconcile_difficulty_v6 import reconcile, with_population
from propose_difficulty_split_v3 import draft
from review_tools import cached_hashes


def read(ref):
    if not io.evidence_ok(ref, ROOT):raise ValueError('Changed evidence: '+str(ref))
    return io.read_json(ROOT/ref['path'])


def run(prior_path, replay_path, pool_path, output):
    output = Path(output)
    if output.exists():raise ValueError('Preserve prior review; use a new output directory')
    prior = io.read_json(prior_path); replay = io.read_json(replay_path)
    if replay['reconciliation'] != io.evidence_ref(prior_path, ROOT):
        raise ValueError('Replay and prior trial selection differ')
    expected = {(a['original_result']['path'], a['original_result']['sha256']):a
                for task in prior['tasks'] for a in task['attempts']}
    seen, receipts = set(), []
    for row in replay['rows']:
        detail = read(row['evidence']); original = row['original_result']
        key = original['path'], original['sha256']
        if key in seen or key not in expected:raise ValueError('Foreign or duplicate replay trial')
        seen.add(key); attempt = expected[key]
        if (row['original_receipt'] != attempt['audit_receipt'] or detail['original_result'] != original
                or not detail['checker_reexecuted'] or detail['model_requests'] != 0):
            raise ValueError('Replay is not bound to the selected actual audit')
        score = read(detail['replayed_result']); previous = read(detail['prior_audit_result'])
        semantic = lambda x: {k:v for k,v in x.items() if k not in ('model_requests','training_prohibited')}
        if semantic(score) != semantic(previous):raise ValueError('Replayed frozen checker differs')
        if score['reward'] != attempt['audit_reward']:raise ValueError('Replayed score differs from measurement')
        read(original);read(row['original_receipt'])
        receipts.append(ROOT/row['original_receipt']['path'])
    if seen != set(expected):raise ValueError('Replay does not cover every observed selected trial')
    runs = [(ROOT/entry['manifest']['path']).parent for entry in prior['runs']]
    for entry in prior['runs']:read(entry['manifest'])
    output.mkdir(parents=True)
    with cached_hashes():
        report = with_population(reconcile(runs, receipts), pool_path)
    io.write_json(output/'reconciliation.json', report)
    # The real evidence passes through the same draft reader and serialization,
    # but executing even one assignment is forbidden in this readiness review.
    with patch('propose_difficulty_split_v3.random.Random', side_effect=AssertionError('No RNG authorized')), \
         patch('propose_difficulty_split_v3.propose', side_effect=AssertionError('No split draw authorized')):
        preview = draft(pool_path, output/'reconciliation.json', output/'split-preflight', dry_run=True)
    if (preview['random_draws_executed'] != 0 or 'assignments' in preview
            or (output/'split-preflight/proposal.json').exists()):
        raise ValueError('No-draw preflight unexpectedly assigned a split')
    summary = {'at':datetime.now(timezone.utc).isoformat(), 'scope':'Readiness review, not new model measurements or a split proposal.',
        'checker_replay':io.evidence_ref(replay_path,ROOT), 'actual_replayed_trials':len(seen),
        'reconciliation':io.evidence_ref(output/'reconciliation.json',ROOT),
        'split_preflight':io.evidence_ref(output/'split-preflight/preflight.json',ROOT),
        'candidate_count':preview['candidate_count'], 'ready_task_count':preview['ready_task_count'],
        'blocked_task_count':len(preview['blockers']), 'would_allow_draw':preview['would_allow_draw'],
        'counts':[{'task_key':r['task_key'],'native_success_count':r['native_success_count'],
                   'k':r['k'],'stratum':r['nominal_stratum']} for r in preview['task_measurement_counts'] if r['ready_for_draft']],
        'source_refs':[io.evidence_ref(ROOT/'scripts'/name,ROOT) for name in
            ('review_difficulty_readiness_v1.py','reconcile_difficulty_v6.py','difficulty_interruption.py','propose_difficulty_split_v3.py')],
        'model_requests':0,'slurm_mutations':0,'new_model_attempts':0,'random_draws':0,'split_frozen':False,
        'original_scores_modified':False,'training_prohibited':True}
    io.write_json(output/'summary.json',summary)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prior','replay','pool','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=run(a.prior,a.replay,a.pool,a.output)
    print({k:r[k]for k in ('actual_replayed_trials','candidate_count','ready_task_count','blocked_task_count','would_allow_draw','random_draws')})

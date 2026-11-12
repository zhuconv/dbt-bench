#!/usr/bin/env python3
"""Draft the preregistered family split; never freeze or replace a split."""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
import random
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io


def workflow_group(value):
    value=re.sub(r'\s+','_',value.strip().lower())
    if value in {'new_project','new_project_from_reference_models'}:
        return 'new_project'
    if (value in {'repair_grain','build_models_and_repair_grain','rebuild_attribution_model'}
            or value.startswith(('diagnose','modify_existing_model'))):
        return 'repair_diagnosis'
    return 'add_or_complete_models'


def difficulty_group(count):
    if type(count)!=int or not 0<=count<=4:
        raise ValueError('Difficulty requires an actual integer success count0..4')
    return '0' if count==0 else '4' if count==4 else '1-3'


def propose(rows, candidate_successes, *, seed, draws=10000):
    """Pure deterministic draft; counts are measured joint native+audit success."""
    if draws!=10000:raise ValueError('Protocol requires exactly10000 assignments')
    index=io.unique_index(rows,'task_key')
    if not candidate_successes or set(candidate_successes)-set(index):
        raise ValueError('Empty or unknown candidate set')
    families=defaultdict(list)
    totals=Counter();group_counts=defaultdict(Counter);candidate_counts=Counter()
    for row in rows:
        families[row['task_family']].append(row['task_key'])
        if row['task_key'] not in candidate_successes:continue
        if row['source'] not in ('ade','spider2','deb'):raise ValueError('Unknown benchmark source')
        tags=(('source',row['source']),('workflow',workflow_group(row['workflow'])),
              ('difficulty',difficulty_group(candidate_successes[row['task_key']])))
        totals.update(tags);group_counts[row['task_family']].update(tags)
        candidate_counts[row['task_family']]+=1
    rng=random.Random(seed)
    no_candidates={f:('test' if rng.random()<0.25 else 'train')
                   for f in sorted(families) if not candidate_counts[f]}
    forced={f for f,n in candidate_counts.items() if n>4}
    possible=sorted(candidate_counts)
    best_score=None;best=None;best_index=None;best_counts=None
    for draw in range(draws):
        assignment={f:('train' if f in forced else 'test' if rng.random()<0.25 else 'train')
                    for f in possible}
        test=Counter()
        for f,side in assignment.items():
            if side=='test':test.update(group_counts[f])
        score=sum((abs(Fraction(test[tag],total)-Fraction(1,4)) for tag,total in totals.items()),Fraction(0))
        if best_score is None or score<best_score:
            best_score,best,best_index,best_counts=score,assignment,draw,test
    sides={**no_candidates,**best}
    assignments={key:sides[row['task_family']] for key,row in sorted(index.items())}
    strata={}
    for (dimension,category),total in sorted(totals.items()):
        n=best_counts[(dimension,category)]
        strata.setdefault(dimension,{})[category]={'total':total,'train':total-n,'test':n,
            'test_fraction':n/total,'absolute_deviation':float(abs(Fraction(n,total)-Fraction(1,4)))}
    groups=[{'family':f,'split':sides[f],'task_keys':sorted(keys),
             'candidate_task_keys':sorted(k for k in keys if k in candidate_successes),
             'candidate_count':candidate_counts[f],'forced_train':f in forced,
             'assignment_basis':'candidate_count_gt4' if f in forced else
                                'same_seed_noncandidate_bernoulli' if f in no_candidates else 'best_of10000'}
            for f,keys in sorted(families.items())]
    differences=[{'task_key':key,'family':index[key]['task_family'],'old_split':index[key]['split'],
                  'proposed_split':side,'candidate':key in candidate_successes}
                 for key,side in assignments.items() if side!=index[key]['split']]
    return {'schema_version':1,'status':'draft_awaiting_explicit_user_confirmation',
            'random_seed':seed,'draws':draws,'selected_draw_index_zero_based':best_index,
            'objective':{'numerator':best_score.numerator,'denominator':best_score.denominator,'float':float(best_score)},
            'candidate_count':len(candidate_successes),
            'candidate_split_counts':dict(Counter(assignments[k] for k in candidate_successes)),
            'all_task_split_counts':dict(Counter(assignments.values())),
            'forced_train_families':sorted(forced),'strata':strata,'groups':groups,
            'assignments':assignments,'differences':differences,
            'model_requests':0,'training_prohibited':True,'frozen':False,
            'database_scope_note':'DEB train and test share the same retail database: new tasks on familiar data, not unseen-database generalization.'}


def summarize_task(attempts):
    """Keep execution validity separate from an executed audit's certainty."""
    ids = [a['attempt'] for a in attempts]
    if len(ids) != len(set(ids)) or any(type(n) is not int or n not in (1, 2, 3, 4) for n in ids):
        raise ValueError('Duplicate or invalid attempt slot; reconcile replacements before drawing')
    valid, unresolved, unknown, pending = [], [], [], []
    for attempt in attempts:
        # v5 execution_valid includes audit certainty. It must not discard an
        # otherwise valid model run just because its audit remains unknown.
        if ((attempt.get('execution_adjudication') or {}).get('status') in
                ('external_interrupted', 'pending_boundary_review')
                or not attempt.get('finished_at') or not attempt.get('capture')
                or not attempt.get('native_validity', {}).get('reward_valid')
                or type(attempt.get('native_success')) is not bool):
            unresolved.append(attempt['attempt'])
            continue
        valid.append(attempt)
        reward = attempt.get('audit_reward')
        if reward is not None and (type(reward) not in (int, float) or reward not in (0, 1)):
            raise ValueError('Unsupported audit reward')
        if not attempt.get('audit_receipt'):
            if reward is not None:
                raise ValueError('Audit score without an executed audit receipt')
            pending.append(attempt['attempt'])
        elif reward is None:
            unknown.append(attempt)
    k = sum(a['native_success'] and a['audit_reward'] == 1 for a in valid)
    potential = sum(a['native_success'] for a in unknown)
    ready = len(valid) == 4 and not pending
    possible = sorted({difficulty_group(n) for n in range(k, k + potential + 1)}) if ready else []
    return {'valid_attempt_count': len(valid), 'native_success_count': sum(a['native_success'] for a in valid),
        'joint_success_count': k, 'k': k, 'audit_unknown_count': len(unknown),
        'audit_pending_count': len(pending), 'audit_pending_slots': pending,
        'unresolved_execution_slots': unresolved, 'missing_slots': sorted({1, 2, 3, 4} - set(ids)),
        'native_pass_audit_unknown_count': potential, 'possible_k_range': [k, k + potential] if ready else None,
        'nominal_stratum': difficulty_group(k) if ready else None,
        'possible_strata': possible, 'stratum_sensitive_audit_unknown': len(possible) > 1,
        'ready_for_draft': ready,
        'unknown_audits': [{'attempt': a['attempt'], 'trial_id': a['trial_id'],
                           'native_success': a['native_success'], 'receipt': a['audit_receipt'],
                           'gaps': a.get('gaps', [])} for a in unknown]}


def draft(pool_path, measurements_path, output, *, rules_path=ROOT/'configs/difficulty-v1/split-rules-v2.json', dry_run=False):
    output=Path(output)
    if output.exists():raise ValueError('Keep proposals immutable; select a new output directory')
    rules=io.read_json(rules_path)
    for key in ('family_definition','family_review','catalog','supersedes'):
        if not io.evidence_ok(rules[key],ROOT):raise ValueError('Preregistered family/catalog changed')
    pool=io.read_json(pool_path);measurement=io.read_json(measurements_path)
    if not pool.get('phase4_review_complete'):
        raise ValueError('Finish phase4 review before a real draw')
    if measurement.get('candidate_pool')!=io.evidence_ref(pool_path,ROOT):
        raise ValueError('Measurement population differs from candidate pool')
    if measurement.get('split_rules') not in (io.evidence_ref(rules_path,ROOT),rules['supersedes']):
        raise ValueError('Measurement used different preregistered rules')
    eligible={r['task_key']:r for r in pool['tasks'] if r['eligible_for_measurement']}
    measured=io.unique_index(measurement['tasks'],'task_key')
    if set(measured)-set(eligible):raise ValueError('Foreign measured task')
    counts={}; accounting=[]; blockers=[]
    for key,row in eligible.items():
        m=measured.get(key)
        if row.get('exclusion_category') in rules['excluded_categories'] or row.get('native_conclusion')=='broken':
            raise ValueError('Excluded task entered candidate pool')
        if m is None:
            blockers.append({'task_key':key,'reason':'Task not yet measured'})
            continue
        if m['task_version']!=row['task_version']:
            raise ValueError('Task version differs')
        for attempt in m['attempts']:
            for ref in attempt['evidence']:
                if not io.evidence_ok(ref,ROOT):raise ValueError('Actual measurement evidence changed')
            if not attempt['evidence']:raise ValueError('Measured outcome has no evidence')
        summary=summarize_task(m['attempts'])
        if not summary['ready_for_draft']:
            blockers.append({'task_key':key,'reason':'Require four valid executions and an executed audit for each',
                'missing_slots':summary['missing_slots'],'unresolved_execution_slots':summary['unresolved_execution_slots'],
                'audit_pending_slots':summary['audit_pending_slots'], 'recovery_slots':m.get('recovery_slots',[])})
        # Read and expose the actual unknown reason without changing any score.
        for unknown in summary['unknown_audits']:
            receipt=io.read_json(ROOT/unknown['receipt']['path'])
            result_ref=receipt['audit_result']
            if not io.evidence_ok(result_ref,ROOT):raise ValueError('Unknown audit evidence changed')
            unknown.update(audit_status=receipt['audit_status'],audit_result=result_ref,
                           audit_details=io.read_json(ROOT/result_ref['path']))
        accounting.append({'task_key':key,'task_version':row['task_version'],**summary})
        if summary['ready_for_draft']:counts[key]=summary['k']
    rows=io.read_jsonl(ROOT/rules['catalog']['path'])
    if dry_run:
        # This branch never constructs an RNG or calls propose, even if every
        # task is ready. It tests real evidence and serialization without draws.
        result={'schema_version':1,'status':'dry_run_preflight_no_draw',
                'candidate_count':len(eligible),'ready_task_count':len(counts),
                'would_allow_draw':not blockers,'blockers':blockers,
                'random_draws_executed':0,'model_requests':0,'training_prohibited':True,
                'frozen':False,'database_scope_note':rules['database_scope_note']}
    else:
        if blockers:raise ValueError('Incomplete difficulty evidence; run --dry-run to list blockers')
        result=propose(rows,counts,seed=rules['random_seed'],draws=rules['random_assignments'])
    output.mkdir(parents=True)
    shutil.copyfile(ROOT/rules['family_definition']['path'],output/'old-split.json')
    result.update(rules=io.evidence_ref(rules_path,ROOT),candidate_pool=io.evidence_ref(pool_path,ROOT),
                  measurements=io.evidence_ref(measurements_path,ROOT),
                  old_split=io.evidence_ref(output/'old-split.json',ROOT),
                  proposer=io.evidence_ref(__file__,ROOT),task_measurement_counts=accounting,
                  audit_unknown_tasks=[r for r in accounting if r['audit_unknown_count']],
                  stratum_sensitive_audit_unknown_tasks=[r for r in accounting if r['stratum_sensitive_audit_unknown']],
                  audit_uncertainty_policy='Stratify by confirmed joint-success k only; disclose possible strata separately; unknown audit is not rewritten as a failed audit.')
    io.write_json(output/('preflight.json' if dry_run else 'proposal.json'),result)
    if not dry_run:
        io.write_jsonl(output/'family-assignments.jsonl',result['groups'])
        io.write_jsonl(output/'split-differences.jsonl',result['differences'])
    io.write_jsonl(output/'task-measurement-counts.jsonl',accounting)
    io.write_jsonl(output/'audit-uncertainty.jsonl',result['audit_unknown_tasks'])
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool',type=Path,required=True);p.add_argument('--measurements',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dry-run',action='store_true',help='Validate evidence and render counts/blockers only; no RNG, assignments, or freeze')
    a=p.parse_args();r=draft(a.pool,a.measurements,a.output,dry_run=a.dry_run)
    print({k:r[k] for k in ('status','candidate_count','ready_task_count','would_allow_draw','random_draws_executed','candidate_split_counts','objective') if k in r})

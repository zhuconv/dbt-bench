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


def draft(pool_path, measurements_path, output, *, rules_path=ROOT/'configs/difficulty-v1/split-rules.json'):
    output=Path(output)
    if output.exists():raise ValueError('Keep proposals immutable; select a new output directory')
    rules=io.read_json(rules_path)
    for key in ('family_definition','family_review','catalog'):
        if not io.evidence_ok(rules[key],ROOT):raise ValueError('Preregistered family/catalog changed')
    pool=io.read_json(pool_path);measurement=io.read_json(measurements_path)
    if not pool.get('phase4_review_complete') or not measurement.get('complete'):
        raise ValueError('Finish phase4 review and all difficulty measurements before a real draw')
    if measurement.get('candidate_pool')!=io.evidence_ref(pool_path,ROOT):
        raise ValueError('Measurement population differs from candidate pool')
    if measurement.get('split_rules')!=io.evidence_ref(rules_path,ROOT):
        raise ValueError('Measurement used different preregistered rules')
    eligible={r['task_key']:r for r in pool['tasks'] if r['eligible_for_measurement']}
    measured=io.unique_index(measurement['tasks'],'task_key')
    if set(eligible)!=set(measured):raise ValueError('Missing, duplicate or foreign measured task')
    counts={}
    for key,row in eligible.items():
        m=measured[key]
        if row.get('exclusion_category') in rules['excluded_categories'] or row.get('native_conclusion')=='broken':
            raise ValueError('Excluded task entered candidate pool')
        if m['task_version']!=row['task_version'] or m.get('valid_attempt_count')!=4:
            raise ValueError('Task version differs or four valid attempts are absent')
        if len(m['attempts'])!=4 or {a['attempt'] for a in m['attempts']}!={1,2,3,4}:
            raise ValueError('Attempt identity mismatch')
        if any(type(a.get('joint_success')) is not bool for a in m['attempts']):
            raise ValueError('Unknown audit/attempt outcome cannot become a difficulty category')
        for attempt in m['attempts']:
            for ref in attempt['evidence']:
                if not io.evidence_ok(ref,ROOT):raise ValueError('Actual measurement evidence changed')
            if not attempt['evidence']:raise ValueError('Measured outcome has no evidence')
        count=sum(a['joint_success'] for a in m['attempts'])
        if count!=m['joint_success_count']:raise ValueError('Measured count differs from four actual outcomes')
        counts[key]=count
    rows=io.read_jsonl(ROOT/rules['catalog']['path'])
    result=propose(rows,counts,seed=rules['random_seed'],draws=rules['random_assignments'])
    output.mkdir(parents=True)
    shutil.copyfile(ROOT/rules['family_definition']['path'],output/'old-split.json')
    result.update(rules=io.evidence_ref(rules_path,ROOT),candidate_pool=io.evidence_ref(pool_path,ROOT),
                  measurements=io.evidence_ref(measurements_path,ROOT),
                  old_split=io.evidence_ref(output/'old-split.json',ROOT),
                  proposer=io.evidence_ref(__file__,ROOT))
    io.write_json(output/'proposal.json',result)
    io.write_jsonl(output/'family-assignments.jsonl',result['groups'])
    io.write_jsonl(output/'split-differences.jsonl',result['differences'])
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool',type=Path,required=True);p.add_argument('--measurements',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=draft(a.pool,a.measurements,a.output)
    print({k:r[k] for k in ('status','candidate_count','candidate_split_counts','objective')})

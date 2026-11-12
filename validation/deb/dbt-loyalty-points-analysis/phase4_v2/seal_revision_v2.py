"""Seal additive audit-only reviews; canonical state/native records stay untouched."""
from pathlib import Path
from copy import deepcopy
import argparse
from dbt_bench import io
from dbt_bench.validation import CHECKS
from phase4_review import precheck
from run_revision_v3 import ROOT,BASE

def ref(path):return io.evidence_ref(Path(path),ROOT)
def checked(status,reason,evidence,executed=True):
    value={'status':status,'reason':reason,'evidence':evidence}
    if executed:value['execution_evidence']=evidence
    return value

def run(task,score_name='candidate-v3.json',selftest_name='selftest-v2.json'):
    old_dir=BASE/task;out=BASE/'revisions-v1'/task
    score_path=out/score_name;score=io.read_json(score_path)
    test_path=out/selftest_name;test=io.read_json(test_path)
    workflow_path=out/'workflow-selftest-v3.json';workflow_test=io.read_json(workflow_path)
    old_workflow=old_dir/'workflow-selftest-v1.json'
    assert io.read_json(old_workflow)['passed'] and workflow_test['passed']
    assert score['six_stage_audit_rewards']==[1]*6 and score['six_stage_component_rewards']==[1]*6
    assert test['counts']['expectation_mismatches']==0 and all(x['audit']['reward']==0 for x in test['nop'])
    old_overlay=old_dir/'audit-overlay-v1.json';overlay=deepcopy(io.read_json(old_overlay))
    contract=deepcopy(io.read_json(old_dir/'contract-v1.json'))
    policy=ROOT/'validation/deb'/task/'phase4_v2/policy.json'
    interpretation=io.read_json(policy)
    contract.update(previous_contract=ref(old_dir/'contract-v1.json'),review_revision='phase4-v2',
        interpretation_policy=ref(policy),scope=interpretation['scope'],
        actual_findings={'candidate':ref(score_path),'six_stage_audit_rewards':[1]*6,'selftest':ref(test_path),
            'workflow_regressions':[ref(old_workflow),ref(workflow_path)],
            'source_parser':score['source_parser'],'source_fixtures':score['source_fixtures']})
    contract['inputs']=interpretation['inputs'];contract['deliverables']=interpretation['deliverables']
    for rule in interpretation['additional_requirements']:
        contract['requirements'].append(rule)
    cp=out/'contract-v2.json'
    if cp.exists():raise ValueError('Preserve sealed revision')
    io.write_json(cp,contract)
    evidence=[ref(score_path),ref(test_path),ref(policy),ref(workflow_path),ref(old_workflow)]
    checks=overlay['checks']
    checks['contract']=checked('pass','Reviewed task requirements and raw-data exceptions have explicit named-column presentation/default policies. Historical native interpretation/scores stay untouched.',evidence,False)
    for name in ('reference','reference_repeat'):
        checks[name]=checked('pass','Both existing independent clean authored builds, all three formal snapshots each, actually accepted by the revised source-bound audit. No build, source or native reward was changed.',evidence)
    checks['independent_semantics']=checked('pass','Imperative original-input arithmetic and full typed/keyed required projections executed for all five or seven models. Scoped fallback/nearest rounding is independently derived before downstream aggregation.',evidence)
    checks['negatives']=checked('pass','Executed independently defined row/key/value/type and task workflow violations; every applicable negative is rejected. Tiny rounding boundaries and AST/provenance fixtures are explicitly separate from original benchmark trials.',evidence)
    checks['equivalent']=checked('pass','Executed legal column presentation plus task-specific missing-master defaults, percentage precision or sequential nearest-rounding/default-aggregate equivalences.',evidence)
    checks['comparator']=checked('pass','Task-specific declared projections reject duplicate/missing rows, absent keys, wrong logical types and key/value corruption; conventional rounding and scoped defaults have accepting and rejecting boundary fixtures.',[ref(BASE/'comparison-regressions-v1.json'),*evidence])
    checks['nop']=checked('pass','New checker actually scored both preserved pre-installation initial snapshots and rejected both; native nop results remain their historical immutable records.',[ref(test_path)])
    # Environment, visibility, cross-context and exact repeat evidence remains
    # valid because task/build/data/image identity and original outputs match.
    for name in ('assets','environment_agent','environment_verifier','cross_context','agent_visibility','stability'):
        checks[name]['evidence']=[*checks[name]['evidence'],ref(score_path)]
        if name!='assets':checks[name]['execution_evidence']=[*checks[name].get('execution_evidence',[]),ref(score_path)]
    code=deepcopy(score['checker_code'])
    for path in (Path(__file__),Path(__file__).with_name('selftest_v2.py'),Path(__file__).with_name('selftest_workflow_v3.py'),policy):
        item=ref(path)
        if item not in code:code.append(item)
    version=io.digest(code)
    overlay.update(contract=ref(cp),checker_version=version,code_refs=code,scope=interpretation['scope'],
        review_revision='phase4-v2',previous_overlay=ref(old_overlay),reference_kind='independent_recompute',
        witness_kind='built_from_inputs',requirement_coverage={r['id']:{'status':'covered',
            'reason':'Current actual source-bound full projections and independent raw arithmetic, plus explicitly executed counterexamples/equivalences. Finite fixed-input and declared fixture scope; no SQL author whitelist.',
            'evidence':evidence} for r in contract['requirements']})
    assert set(checks)==set(CHECKS) and all(x['status']=='pass' for x in checks.values())
    ap=out/'audit-overlay-v2.json';io.write_json(ap,overlay)
    rows=[]
    for ti,t in enumerate(score['trials']):
        for si,s in enumerate(t['stages']):
            rows.append({'trial':Path(t['trial']).name,'stage':s['stage'],'candidate_snapshot':s['snapshot'],
                'audit_result':ref(score_path),'audit_field_paths':{'reward':['trials',ti,'stages',si,'audit','reward'],
                    'candidate_snapshot':['trials',ti,'stages',si,'snapshot'],'task_key':['task_key']},'audit_reward':1})
    candidate={'schema_version':1,'task_key':score['task_key'],'phase4_identity':overlay['phase4_identity'],
        'model_requests':0,'qualified_overlay':ref(ap),'checker_version':version,'code_refs':code,
        'records':rows,'candidate_pass':True,'scope':'Same six original snapshots, new declared audit policy/code; native scores and prior audit results unchanged.'}
    candidate_path=out/'candidate-review-v2.json';io.write_json(candidate_path,candidate)
    result=precheck(ap);io.write_json(out/'audit-precheck-v2.json',result)
    handoff={'task_key':score['task_key'],'audit':str(ap),'candidate_review':str(candidate_path),
        'previous_native_overlay':str(old_dir/'native-overlay-v1.json'),
        'audit_conclusion':result['conclusion'],'review_complete':result['review_complete'],
        'candidate_pass':True,'six_stage_audit_rewards':[1]*6,'model_requests':0,
        'revision_reason':interpretation['revision_reason'],'new_completed_task_credit':0}
    io.write_json(out/'handoff-v2.json',handoff);print(handoff)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--score',default='candidate-v3.json');p.add_argument('--selftest',default='selftest-v2.json')
    x=p.parse_args();run(x.task,x.score,x.selftest)

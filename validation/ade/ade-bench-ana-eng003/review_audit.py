"""Reconcile 17 original-data cases and four controls without changing rewards."""
from collections import Counter
from pathlib import Path
import json,shutil,subprocess,sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref,file_hash,read_json,tree_hash,write_json
from dbt_bench.validation import CHECKS,identity,review_inputs
from case_engine import compare,derive


def main():
    out=ROOT/'validation/evidence/customer-audit-review-v1'
    out.mkdir(exist_ok=False)
    code_refs=[]
    for path in [HERE/'audit.py',HERE/'collect_audit.py',HERE/'case_engine.py',ROOT/'src/dbt_bench/comparison.py']:
        frozen=out/'checker-source'/path.name;frozen.parent.mkdir(exist_ok=True)
        shutil.copyfile(path,frozen);code_refs.append(evidence_ref(path,ROOT))
    trials=[]
    for name in ['customer-audit-cases-v1','customer-audit-controls-v1']:
        batch=ROOT/'artifacts/validation'/name;manifest=read_json(batch/'manifest.json')
        planned=Counter((t['task_label'],t['agent']) for t in manifest['planned'])
        observed=Counter();seen=set();batch_trials=[]
        assert file_hash(batch/'config.yaml')==manifest['config']['sha256']
        for label,info in manifest['tasks'].items():
            task=batch/'tasks'/label
            assert tree_hash(task)==info['validation_copy_hash']
            native=ROOT/'tasks/ade__ade-bench-ana-eng003/tests'
            assert file_hash(task/'tests/native-test.sh')==file_hash(native/'test.sh')
            assert file_hash(task/'tests/native-test-setup.sh')==file_hash(native/'test-setup.sh')
            assert file_hash(task/'tests/customer-collector.py')==file_hash(HERE/'collect_audit.py')
        for path in sorted((batch/'jobs').glob('*/*/result.json')):
            result=read_json(path);label=Path(result['config']['task']['path']).name
            agent=result['config']['agent']['name'];info=manifest['tasks'][label]
            assert Path(result['config']['task']['path']).resolve()==(batch/'tasks'/label).resolve()
            assert (label,agent) in planned and result['id'] not in seen
            observed[label,agent]+=1;seen.add(result['id'])
            assert result['exception_info'] is None
            rewards=(result.get('verifier_result') or {}).get('rewards');assert rewards in [{'reward':0.0},{'reward':1.0}]
            proof_ref=None;validity=None;requirements=[]
            if info['case']:
                case=read_json(batch/'tasks'/label/'solution/validation-case/case.json')
                proof_path=path.parent/'agent/case-proof.json';proof=read_json(proof_path)
                assert proof['case']==info['case_mode'] and not proof['tested_checker_invoked_to_define_validity']
                source_path=path.parent/'agent/customer-input.json'
                assert file_hash(source_path)==proof['input_artifacts'][0]['sha256']==case['pristine_input_sha256']
                expected=derive(read_json(source_path)['rows'])
                for phase in ['before','after']:
                    snapshot_path=path.parent/'agent'/('customer-'+phase)/'snapshot.json'
                    ref=next(r for r in proof['submission_artifacts'] if r['path'].endswith('customer-'+phase+'/snapshot.json'))
                    assert file_hash(snapshot_path)==ref['sha256']
                    snap=read_json(snapshot_path)
                    for file in snap['source_files']:assert file_hash(snapshot_path.parent/Path(file['path']).name)==file['sha256']
                    comparison=compare(expected,snap,proof['expected_columns'])
                    if phase=='before':assert comparison['equal']
                    else:assert comparison==proof['comparison']
                positive=case['kind']!='negative';kind=case['kind'];validity=proof['validity']
                requirements=proof['requirement_ids'];proof_ref=evidence_ref(proof_path,ROOT)
            else:
                positive=agent=='oracle';kind='native_control'
            audit_path=out/'audits'/(result['id']+'.json')
            command=[sys.executable,str(HERE/'audit.py'),'--snapshot',str(path.parent/'verifier/customer-audit'),'--output',str(audit_path)]
            execution=subprocess.run(command,capture_output=True,text=True)
            (out/(result['id']+'-audit.log')).write_text(execution.stdout+execution.stderr)
            assert execution.returncode==0,execution.stdout+execution.stderr
            audit=read_json(audit_path);assert audit['status']=='scored'
            assert audit['success']==positive,('Unexpected audit outcome',info['case_mode'],audit)
            native_outcome=('accepted_correct' if positive else 'missed_wrong') if rewards['reward']==1.0 else ('rejected_legal' if positive else 'detected_wrong')
            record={'trial_id':result['id'],'task_key':'ade__ade-bench-ana-eng003','case_mode':info['case_mode'],
                'agent':agent,'kind':kind,'native_rewards':rewards,'audit_reward':audit['reward'],
                'native_outcome':native_outcome,'audit_outcome':'accepted_correct' if positive else 'detected_wrong',
                'exception_info':result['exception_info'],'result':evidence_ref(path,ROOT),'audit':evidence_ref(audit_path,ROOT),
                'proof':proof_ref,'proof_validity':validity,'requirement_ids':requirements,
                'snapshots':audit['snapshots'],'attempt':1 if info['case'] else None,'retry':None,
                'attempt_note':'Case tasks each have one planned attempt; native controls are reconciled as a multiset without guessing ordinals.'}
            batch_trials.append(record);trials.append(record)
        assert observed==planned
        native_job=read_json(batch/'jobs'/name/'result.json')
        assert native_job['finished_at'] and native_job['stats']['n_completed_trials']==len(batch_trials)
        write_json(batch/'reconciliation.json',{'coverage_complete':True,'planned_trials':sum(planned.values()),
            'observed_trials':len(batch_trials),'errors':[],'trials':batch_trials,
            'manifest':evidence_ref(batch/'manifest.json',ROOT),'native_job_result':evidence_ref(batch/'jobs'/name/'result.json',ROOT)})
    assert len(trials)==21 and len({t['trial_id'] for t in trials})==21
    cases=[t for t in trials if t['kind']!='native_control'];controls=[t for t in trials if t['kind']=='native_control']
    versions={read_json(ROOT/t['audit']['path'])['checker_version'] for t in trials};assert len(versions)==1
    summary={'schema_version':1,'task_key':'ade__ade-bench-ana-eng003','native_conclusion':'weak','audit_conclusion':'pending',
        'checker_version':versions.pop(),'case_trials':17,'control_trials':4,
        'native_case_outcomes':dict(Counter(t['native_outcome'] for t in cases)),
        'audit_case_outcomes':dict(Counter(t['audit_outcome'] for t in cases)),
        'audit_control_outcomes':dict(Counter(t['audit_outcome'] for t in controls)),
        'code':code_refs,'trials':trials,
        'scope':'Pinned full input, reviewed cases and unchanged native dbt setup. Actual Qwen contexts and arbitrary project dependency transfer remain unvalidated.'}
    summary_path=out/'summary.json';write_json(summary_path,summary);ref=evidence_ref(summary_path,ROOT)
    contract_path=HERE/'contract.yaml';old=read_json(contract_path)
    archive=out/'contract-before.json';write_json(archive,old)
    contract={**old,'review_history':old.get('review_history',[])+[evidence_ref(archive,ROOT)],
        'review_evidence':old.get('review_evidence',[])+[ref]}
    grouped={}
    for t in cases:
        for req in t['requirement_ids']:grouped.setdefault(req,[]).append(t)
    requirements=[]
    # Completeness/value cases use existing stable IDs from the original contract.
    for requirement in old['requirements']:
        req={**requirement};related=grouped[req['id']]
        failure=next((t for t in related if t['native_outcome'] in ['missed_wrong','rejected_legal']),None)
        coverage={'status':'covered','evidence':[ref]+[t['result'] for t in related],
                  'scope':'Declared original-data cases; no claim of exhaustive proof'}
        if failure:coverage.update(status=failure['native_outcome'],case_validity=failure['proof_validity'],
                                  independent_proof=failure['proof'],checker_evidence=failure['result'])
        req.update(cases=sorted({t['case_mode'] for t in related}),coverage={'native':coverage,
            'audit':{'status':'covered','evidence':[ref]+[t['audit'] for t in related],
                     'scope':'Declared independently proved cases only; full context protocol pending'}})
        requirements.append(req)
    contract['requirements']=requirements
    contract['deliverables'][0]['allow_extra_columns']=True
    contract['deliverables'][0]['build_responsibility']='Native test-setup invokes dbt run; a correct model SQL submission need not pre-materialize the relation'
    checks={k:dict(v) for k,v in old['checks'].items()}
    def check(status,reason,audit=False):return {'status':status,'reason':reason,'evidence':[ref]+(code_refs if audit else [])}
    checks.update(reference=check('pass','Two clean actual builds and two native source-only oracle controls match all 29 customer rows'),
        reference_repeat=check('pass','Independent initializations reproduce complete expected output'),
        nop=check('pass','Both native untouched controls fail; audit also rejects missing requested model'),
        equivalent=check('fail','Native accepts source-only, view, alternate path, direct source, reordered and DISTINCT cases, but rejects a legal extra informational column'),
        negatives=check('gap','Eight negative cases executed: six rejected; two duplicate cases still missed'),
        comparator=check('fail','Native set equality misses duplicate multiplicity and its column-set check rejects an unprohibited extra column'),
        stability=check('pass','Both reference outputs agree with all fixed input-derived values; no column omitted'))
    contract['checks']=checks
    audit_checks={k:check('pending','Actual Qwen context, visibility and complete environment/transfer review still required',True) for k in CHECKS}
    for k,reason in {'assets':'Both collector phases verify the original fixed input and identical submitted source inventories',
        'contract':'Declared 18 required columns, exact key, optional extra columns and native build workflow reviewed',
        'reference':'Two clean case builds and two native oracle builds match full independent expected output',
        'reference_repeat':'Two independent reference initializations accepted with stable outputs',
        'nop':'Both actual native nop controls rejected for missing dim_customer model',
        'equivalent':'All seven declared equivalence cases accepted, including source-only and extra-column',
        'independent_semantics':'Audit derives exact source-key rows separately from the pre-verifier case proof; no gold or native reward imports',
        'negatives':'All eight independently proved negative cases rejected; malformed captures remain errors with null score',
        'comparator':'Declared keyed comparison preserves multiplicity, value association, integer precision and NULL; independent synthetic behavior tests also required',
        'stability':'Full outputs match the same fixed public input across independent initializations'}.items():
        audit_checks[k]=check('pass',reason,True)
    # A test record is an explicit prerequisite for the comparator review.
    test_path=ROOT/'validation/evidence/customer-audit-tests-v1.json';test=read_json(test_path)
    assert test['successful'] and not test['skipped']
    for name in ['audit.py','collect_audit.py']:
        assert test['inputs'][str((HERE/name).relative_to(ROOT))]==file_hash(HERE/name)
    audit_checks['comparator']['evidence'].append(evidence_ref(test_path,ROOT))
    contract['audit']={'checker_version':summary['checker_version'],'reference_kind':'independent_recompute',
        'witness_kind':'built_from_inputs','review_status':'reviewed','requirements':requirements,
        'checks':audit_checks,'review_evidence':[ref]+code_refs,
        'scope':'Pinned original input and declared source inventory with native setup output reuse; actual Qwen and complete environment protocol pending'}
    contract['missing_contract_work']=['Verify actual Qwen tool environment, hidden-asset visibility and cross-context compatibility.',
        'Finish audit environment/dependency transfer review; keep audit pending until every protocol check has evidence.']
    row=next(r for r in load_catalog(ROOT) if r['task_key']==contract['task_key'])
    inputs=review_inputs(identity(ROOT,row,contract_path,'apptainer'))
    contract['reviewed_inputs']=inputs;contract['audit']['reviewed_inputs']=inputs
    write_json(contract_path,contract)
    print(json.dumps({k:v for k,v in summary.items() if k.endswith('outcomes') or k in ['case_trials','control_trials','native_conclusion','audit_conclusion']}))


if __name__=='__main__':main()

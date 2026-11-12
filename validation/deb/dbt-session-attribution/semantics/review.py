"""Review this explicit batch; do not promote the task or its checker to validated."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import shutil,sys

ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import read_json,read_jsonl,write_json,file_hash,evidence_ref,digest
from dbt_bench.catalog import load_catalog
from dbt_bench.validation import identity,review_inputs,build_record,summarize


def main():
    base=Path(__file__).resolve().parent.parent;key='deb__dbt-session-attribution'
    stage=ROOT/'artifacts/validation/session-semantics-v2';out=ROOT/'validation/evidence/session-semantics-review-v2'
    assert not out.exists()
    pilot_path=ROOT/'artifacts/validation/session-semantics-pilot-v2/reconciliation.json'
    cases_path=ROOT/'artifacts/validation/session-semantics-cases-v2/reconciliation.json'
    pilot=read_json(pilot_path);cases=read_json(cases_path)
    assert pilot['complete'] and cases['complete'] and pilot['observed']==4 and cases['observed']==17
    assert pilot['outcomes']=={'accepted_correct':4}
    assert all(v['equal'] and v['runs']==2 for v in pilot['positive_stability'].values())
    trials=pilot['trials']+cases['trials']
    assert all(t['proof_recomputed'] and t['exception_info'] is None and t['outcome']!='unresolved_execution' for t in trials)
    by_mode={t['mode']:t for t in cases['trials']}
    legal=by_mode['fixture-extra-null-column'];assert legal['outcome']=='rejected_legal'
    precision=by_mode['fixture-excess-precision'];assert precision['outcome']=='missed_wrong'
    bonus=by_mode['fixture-credit-bonus'];assert bonus['outcome']=='missed_wrong'
    negatives=[t for t in trials if t['kind']=='negative'];assert len(negatives)==16
    initial_path=stage/'initial.json';initial=read_json(initial_path)
    assert initial['paths']['/app/dbt_transforms']['exists'] is False
    assert initial['paths']['/app/dbt_models_duckdb']['exists'] is True
    assert len(initial['inputs']['stg_digital__web_sessions']['rows'])==5029
    assert len(initial['inputs']['stg_analytics__fact_sales']['rows'])==5057
    source_paths=[ROOT/'tasks'/key/p for p in ['instruction.md','task.toml','environment/Dockerfile','solution/solve.sh','tests/test.sh','tests/test_outputs.py']]
    original_trials=[t for t in trials if t['data_scope']=='original_pinned_inputs'];assert len(original_trials)==2
    original_proof=read_json(ROOT/original_trials[0]['independent_proof']['path'])
    fixture_trial=next(t for t in pilot['trials'] if t['data_scope']=='additional_input_fixture')
    fixture_proof=read_json(ROOT/fixture_trial['independent_proof']['path'])
    out.mkdir(parents=True)
    summary={'schema_version':1,'timestamp_utc':datetime.now(timezone.utc).isoformat(),'task_key':key,
             'native':'weak','audit':'pending','full_validation_complete':False,
             'original_reference_trials':2,'additional_input_fixture_trials':19,'qwen_smoke_executed':False,
             'fixture_negative_detection':{'should_detect':16,'detected':sum(t['outcome']=='detected_wrong' for t in negatives),
                                            'missed':sum(t['outcome']=='missed_wrong' for t in negatives)},
             'fixture_outcomes':dict(Counter(t['outcome'] for t in trials if t['data_scope']=='additional_input_fixture')),
             'original_input_coverage':original_proof['facts']['coverage'],'fixture_input_coverage':fixture_proof['facts']['coverage'],
             'stability':pilot['positive_stability'],'sources':[evidence_ref(p,ROOT) for p in source_paths],
             'initial_public_project':evidence_ref(initial_path,ROOT),'batches':[evidence_ref(p,ROOT) for p in [pilot_path,cases_path]],
             'scope':'Original data has only single-touch attributed paths. Additional-input fixture validates declared multi-touch rules separately; its native rewards are not original benchmark scores. Nullable extra column is falsely rejected; bonus applied to conversion count and excess precision are accepted.',
             'remaining':['Actual Qwen/verifier context and artifact compatibility review','Dbt workflow and ordering negative coverage',
                          'Null-device, timestamp-tie, repeated converting-order, subsecond and half-cent semantics',
                          'Independent supplemental checker validation','Conflicting project paths in instruction need a separate task-variant decision']}
    summary_path=out/'summary.json';write_json(summary_path,summary);summary_ref=evidence_ref(summary_path,ROOT)
    contract_path=base/'contract.yaml';contract=read_json(contract_path);old_record=read_json(base/'record.json')
    rows=load_catalog(ROOT);row=next(r for r in rows if r['task_key']==key)
    current=identity(ROOT,row,contract_path,backend='apptainer')
    assert all(v==old_record['identity'][k] for k,v in current.items() if k not in {'contract','cases'}), 'Existing execution inputs changed; inherited checks need a separate review'
    shutil.copyfile(contract_path,out/'contract-before.json');shutil.copyfile(base/'record.json',out/'record-before.json')
    contract.setdefault('review_history',[]).append(evidence_ref(out/'contract-before.json',ROOT))
    contract.setdefault('review_evidence',[]).extend([summary_ref,evidence_ref(initial_path,ROOT)])
    for requirement in contract['requirements']:
        relevant=[t for t in cases['trials'] if requirement['id'] in t['requirements']]
        if not relevant:continue
        failure=next((t for t in relevant if t['outcome'] in ['missed_wrong','rejected_legal']),None)
        references=[ref for t in relevant for ref in [t['independent_proof'],t['result']]]
        coverage={'status':'covered','scope':'Additional input fixture only; not an original-data detection claim.',
                  'evidence':references,'cases':[{'mode':t['mode'],'outcome':t['outcome']} for t in relevant]}
        if failure:
            coverage.update(status=failure['outcome'],independent_proof=failure['independent_proof'],checker_evidence=failure['result'],
                            case_validity='legal_equivalent' if failure['kind']=='equivalent' else 'business_error')
        requirement.setdefault('coverage',{})['native']=coverage
    original_refs=[ref for t in original_trials for ref in [t['independent_proof'],t['result']]]
    checks=contract['checks']
    updates={
      'assets':('pass','Pinned instruction, source, native verifier and initialized project/input definitions read; paths and gaps explicitly recorded.',[summary_ref,evidence_ref(initial_path,ROOT)]),
      'contract':('pass','All critical requirements have explicit mappings or gaps; extra fixture scope and project-path discrepancy are declared.',[summary_ref]),
      'reference':('pass','Two actual original-input dbt builds accepted by native and independently recomputed; no gold access.',original_refs),
      'reference_repeat':('pass','Two clean original-data and two extra-fixture references accepted; full required outputs retained.',[evidence_ref(pilot_path,ROOT)]),
      'stability':('pass','All output rows/columns identical as multisets across two independent initializations for each dataset; no columns dropped.',[evidence_ref(pilot_path,ROOT)]),
      'independent_semantics':('pass','Independent Decimal reconstruction agrees with all required original-data outputs and the declared branch fixture. Ambiguous cases absent from these datasets remain explicitly outside this result.',[summary_ref,evidence_ref(pilot_path,ROOT)]),
      'equivalent':('fail','A legal additional nullable column is rejected because native null validation includes the extra column.',[legal['independent_proof'],legal['result']]),
      'negatives':('gap','Sixteen independently established fixture negatives executed; native omissions and unexecuted workflow/edge cases remain.',[summary_ref,evidence_ref(cases_path,ROOT)]),
      'comparator':('fail','Original-data duplicate omission remains established; extra fixture also demonstrates nullable-extra-column false rejection, incorrect conversion bonus and excess-precision acceptance.',[summary_ref,legal['independent_proof'],legal['result'],precision['independent_proof'],precision['result'],bonus['independent_proof'],bonus['result']]),
    }
    for name,(status,reason,refs) in updates.items():checks[name]={'status':status,'reason':reason,'evidence':refs}
    # No actual agent/verifier-context claim is added by these oracle fixtures.
    contract['missing_contract_work']=summary['remaining']
    contract['semantic_fixture_review']=summary_ref
    contract['runtime_project_path_review'].update(initial_image_evidence=evidence_ref(initial_path,ROOT),
        semantic_conclusion='Native initialized project exists only at /app/dbt_models_duckdb. The instruction also names nonexistent /app/dbt_transforms; discrepancy preserved, no silent prompt patch.')
    contract['notes'].append('Session semantics v2 adds two current original references and 19 additional-input fixture runs. These do not certify an audit checker, actual Qwen context or omitted edge cases.')
    write_json(contract_path,contract)
    contract['reviewed_inputs']=review_inputs(identity(ROOT,row,contract_path,backend='apptainer'));write_json(contract_path,contract)
    record=build_record(ROOT,row,contract_path,backend='apptainer')
    assert record['native']['conclusion']=='weak' and record['audit']['conclusion']=='pending'
    history=base/'history'/(digest(old_record)+'.json')
    if not history.exists():write_json(history,old_record)
    write_json(base/'record.json',record)
    records=read_jsonl(ROOT/'validation/ledger.jsonl');write_json(stage/'ledger-before.json',records)
    updated=[record if r['task_key']==key else r for r in records]
    result=summarize(ROOT,rows,updated);print(result)


if __name__=='__main__':main()

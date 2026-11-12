"""Review current typed cases; retain the initial case-tooling fault separately."""
from collections import Counter
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.catalog import load_catalog
from dbt_bench.io import read_json,write_json,evidence_ref,file_hash
from dbt_bench.validation import identity,review_inputs,CHECKS
from independent import MONEY


def main():
    here=Path(__file__).parent;out=ROOT/'validation/evidence/orders-filter-review'
    batches=[ROOT/'artifacts/validation'/p for p in ['orders-filter-semantic-pilot-v2','orders-filter-semantic-remaining-v2']]
    reports=[read_json(b/'reconciliation.json') for b in batches]
    assert all(r['coverage_complete'] for r in reports)
    trials=[t for r in reports for t in r['trials']]
    assert len(trials)==14 and len({t['trial_id'] for t in trials})==14
    assert all(t['schema_complete'] and t['exception_info'] is None for t in trials)
    groups={}
    for t in trials:groups.setdefault(t['case_mode'],[]).append(t)
    references=groups['full-reference'];assert len(references)==2
    assert all(t['case_outcome']=='accepted_correct' for t in references)
    proofs=[read_json(ROOT/t['proof']['path']) for t in references]
    assert proofs[0]['expected_columns']==proofs[1]['expected_columns']
    assert proofs[0]['input_types']==proofs[1]['input_types'] and len(proofs[0]['input_types'])==109
    assert all(p['comparison']['equal'] and p['comparison']['actual_rows']==1950 for p in proofs)
    snapshots=[read_json(ROOT/t['snapshots'][0]['path']) for t in references]
    assert snapshots[0]['types']==snapshots[1]['types']
    for s in snapshots:
        assert s['table_type']=='BASE TABLE' and s['types']['order_date']=='DATE'
    controls_path=ROOT/'artifacts/validation/orders-filter-reference-nop-v1/reconciliation.json'
    controls=read_json(controls_path);assert controls['coverage_complete']
    assert len(controls['trials'])==4 and all(t['exception_info'] is None for t in controls['trials'])
    nops=[t for t in controls['trials'] if t['agent']=='nop'];assert len(nops)==2
    assert all(t['native_rewards']=={'reward':0.0} for t in nops)
    task=ROOT/'tasks/deb__dbt-test-orders-filter'
    sources=[evidence_ref(task/p,ROOT) for p in ['instruction.md','environment/Dockerfile','solution/solve.sh','tests/test_outputs.py','tests/test.sh']]
    summary={'schema_version':1,'task_key':'deb__dbt-test-orders-filter','native_conclusion':'weak','audit_conclusion':'pending',
        'current_case_revision':'v2-case-insensitive-metadata-and-required-types','case_trials':14,
        'reference_nop_trials':4,'outcomes':dict(Counter(t['case_outcome'] for t in trials)),
        'negative_detection_by_kind':{kind:{'should_detect':sum(t['violation_kind']==kind for t in trials),
            'detected':sum(t['violation_kind']==kind and t['case_outcome']=='detected_wrong' for t in trials),
            'missed':sum(t['violation_kind']==kind and t['case_outcome']=='missed_wrong' for t in trials)} for kind in ['data_semantics','schema_preservation']},
        'source_rows':2037,'source_columns':109,'output_rows':1950,'output_columns':110,
        'native_reference_missing_source_columns':105,'corrected_reference_stable':True,
        'pristine_input':evidence_ref(ROOT/'artifacts/validation/orders-filter-input-v1/result.json',ROOT),
        'reviewed_sources':sources,'trials':trials,'reference_nop':evidence_ref(controls_path,ROOT),
        'batches':[evidence_ref(b/'reconciliation.json',ROOT) for b in batches],
        'case_tooling_correction':evidence_ref(out/'case-tooling-correction.json',ROOT),
        'scope':'Original fixed orders and stated three-purpose-flag policy; the purpose of exclude_from_reporting is unspecified and unresolved',
        'original_native_scores_preserved':True,'qwen_smoke':False}
    summary_path=out/'case-summary.json';write_json(summary_path,summary);summary_ref=evidence_ref(summary_path,ROOT)
    path=here/'contract.yaml';old=read_json(path);history=out/('contract-before-'+file_hash(path)+'.json')
    if not history.exists():write_json(history,old)
    specs=[
        ('R-filter','Keep all orders without affirmative test/sample/internal flags.','Native independently calculates three-flag count, aggregate revenue and explicit flagged-ID joins.',['include-test','include-sample','include-internal']),
        ('R-filter-test','Exclude test_order_flag=TRUE.','TestOrderFiltering.test_no_test_orders',['include-test']),
        ('R-filter-sample','Exclude sample_order_flag=TRUE.','TestOrderFiltering.test_no_sample_orders',['include-sample']),
        ('R-filter-internal','Exclude internal_order_flag=TRUE.','TestOrderFiltering.test_no_internal_orders',['include-internal']),
        ('R-null-flags','Retain NULL-purpose-flag rows under the reviewed no-affirmative-flag interpretation.','expected_values uses coalesce(flag,false)',['drop-null-flags']),
        ('R-completeness','Retain every eligible order.','Expected count; no complete ID-set equality',['missing-order']),
        ('R-unique-order','Order identifiers must be non-NULL and unique.','test_no_null_order_ids and test_unique_order_ids',['duplicate-order']),
        ('R-preserve','Preserve all 109 source columns, adding order_date and rounding monetary values as instructed.','Only five minimum columns checked; original reference omits 105 source columns',['native-minimum']),
        ('R-association','Retain each order\'s customer, amounts, status and other attributes with the correct ID.','Only totals and broad validity checks; no per-ID value comparison',['swap-totals','wrong-status']),
        ('R-date','order_date is the DATE of that order\'s ordered_at timestamp.','Only non-NULL dates checked',['shift-date']),
        ('R-money','Round monetary values to two decimal places while preserving source-derived amounts.','Grand-total sum compared with absolute tolerance 1.0; no per-row precision check',['fractional-money']),
        ('R-order','Output row order is not constrained.','No native ordering requirement',['reordered']),
        ('R-workflow','Submit a dbt model in the given project, configured with the declared profile and database.','Native tests only query the final database; no source rebuild or model file check',[]),
        ('R-other-flags','Clarify any intended policy for other source flags such as exclude_from_reporting.','Native reference/grader ignore this flag; the prose names only test/sample/internal purposes',[]),
    ]
    requirements=[]
    for req_id,text,mapping,modes in specs:
        observations=[t for m in modes for t in groups[m]]
        cov={'status':'pending','reason':'No executed case or unresolved task interpretation','evidence':[]}
        if observations:
            t=next((t for t in observations if t['case_outcome']=='missed_wrong'),observations[0])
            cov={'status':'missed_wrong' if t['case_outcome']=='missed_wrong' else 'covered',
                'case_validity':t['proof_validity'],'independent_proof':t['proof'],'checker_evidence':t['result'],
                'evidence':[summary_ref]+[r for t in observations for r in [t['proof'],t['result']]],
                'scope':'Named current original-data cases; other data branches and submission strategies untested'}
        requirements.append({'id':req_id,'critical':True,'description':text,'native_mapping':mapping,'cases':modes,
                             'coverage':{'native':cov,'audit':{'status':'pending'}}})
    checks={k:{'status':'pending','reason':'Actual Qwen context or decisive interpretation/coverage not yet reviewed','evidence':[]} for k in CHECKS}
    def check(state,reason,refs=None):return {'status':state,'reason':reason,'evidence':refs or [summary_ref]}
    checks.update(assets=check('pass','Pristine public input hash is identical before and after all typed cases; fixed task and runtime captured in manifests'),
        contract=check('gap','All concrete instructions mapped, but the scope of other reporting flags remains unspecified'),
        reference=check('pass','The additional full-column dbt witness builds all 1950 expected rows; the original reference has an independently proved column-preservation defect'),
        reference_repeat=check('pass','Two clean full-column builds accepted; every row and all 110 columns match the independent derivation'),
        nop=check('pass','Two current-runtime nop trials produce native reward 0 without Harbor exception',[t['result'] for t in nops]),
        equivalent=check('pass','Reversed row order accepted; no order requirement imposed'),
        independent_semantics=check('gap','Full input-derived comparison covers the explicit three-flag policy, values and rounding; other-flag interpretation remains unresolved'),
        negatives=check('gap','Actual cases prove multiple semantic and column-preservation misses; additional workflow and policy coverage remains unfinished'),
        comparator=check('fail','Native aggregates accept wrong per-order amounts/date/status and fractional monetary precision'),
        stability=check('pass','Both full-column witnesses match the same fixed input-derived 1950 by 110 output and exact output types; no unstable column skipped'))
    contract={**old,'review_status':'reviewed_with_interpretation_gap','workflow':'create_filtered_order_model_in_existing_project',
        'reference_kind':'combined','witness_kind':'built_from_inputs','native_reference_kind':'aggregate_assertions',
        'inputs':[{'database':'/app/database/retail.duckdb','table':'main.orders','row_count':2037,'columns':proofs[0]['input_types']}],
        'deliverables':[{'project':'/app/dbt_models_duckdb','model':'production_sales','table':'main.production_sales',
            'columns':proofs[0]['expected_columns'],'key':['order_id'],'row_order':'irrelevant',
            'monetary_columns':list(MONEY),'rounding':'Decimal half-up to two places; other attributes unchanged'}],
        'requirements':requirements,'checks':checks,'native_success':{'metric':'reward','equals':1.0,'reviewed':True},
        'missing_contract_work':['Clarify the intended treatment of other reporting flags; no policy added beyond the three named purposes.',
            'Check actual Qwen context, visibility and verifier compatibility.',
            'Execute workflow/source-file, NULL ID and further schema cases; certify a separate audit before sampling.'],
        'audit':None,'review_evidence':[summary_ref],
        'review_history':old.get('review_history',[])+[evidence_ref(history,ROOT)],
        'notes':['Original oracle scores remain 1 even though it omits 105 required source columns.',
                 'Full-column witnesses are separate validation cases; no upstream solution or native verifier is overwritten.',
                 'V1 case-tooling failure and all original rewards remain in separate immutable jobs; only v2 typed cases support this review.']}
    row=next(r for r in load_catalog(ROOT) if r['task_key']==contract['task_key'])
    contract['reviewed_inputs']=review_inputs(identity(ROOT,row,path,'apptainer'))
    write_json(path,contract)
    print({'native':'weak','audit':'pending','outcomes':summary['outcomes'],'detection':summary['negative_detection_by_kind']})


if __name__=='__main__':main()

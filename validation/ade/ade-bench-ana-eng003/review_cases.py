"""Reconcile actual dbt cases and bind the customer-key coverage review."""
from collections import Counter
from pathlib import Path
import csv,json,sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.catalog import load_catalog
from dbt_bench.io import read_json,write_json,evidence_ref,file_hash,tree_hash
from dbt_bench.validation import identity,review_inputs,CHECKS
from case_engine import derive,compare


def main():
    batch=ROOT/'artifacts/validation/customer-dimension-cases-v1'
    manifest=read_json(batch/'manifest.json');planned={t['task_label']:t for t in manifest['planned']}
    seen=set();trials=[]
    for path in sorted((batch/'jobs').glob('*/*/result.json')):
        result=read_json(path);label=Path(result['config']['task']['path']).name
        assert label in planned and label not in seen;seen.add(label)
        frozen=batch/'tasks'/label
        assert tree_hash(frozen)==manifest['tasks'][label]['validation_copy_hash']
        case=read_json(frozen/'solution/validation-case/case.json')
        proof_path=path.parent/'agent/case-proof.json';proof=read_json(proof_path)
        assert proof['case']==planned[label]['case_mode'] and not proof['tested_checker_invoked_to_define_validity']
        source_path=path.parent/'agent/customer-input.json'
        assert file_hash(source_path)==case['pristine_input_sha256']==proof['input_artifacts'][0]['sha256']
        expected=derive(read_json(source_path)['rows']);snapshots={}
        for when in ['before','after']:
            p=path.parent/'agent'/('customer-'+when)/'snapshot.json'
            asserted=next(r for r in proof['submission_artifacts'] if r['path'].endswith('customer-'+when+'/snapshot.json'))
            assert file_hash(p)==asserted['sha256']
            snapshot=read_json(p)
            for ref in snapshot['source_files']:assert file_hash(p.parent/Path(ref['path']).name)==ref['sha256']
            observation=compare(expected,snapshot,proof['expected_columns'])
            if when=='before':assert observation['equal']
            else:assert observation==proof['comparison']
            snapshots[when]=evidence_ref(p,ROOT)
        assert result['exception_info'] is None
        rewards=(result.get('verifier_result') or {}).get('rewards');assert rewards in ({'reward':0.0},{'reward':1.0})
        positive=case['kind']!='negative'
        outcome=('accepted_correct' if positive else 'missed_wrong') if rewards['reward']==1.0 else ('rejected_legal' if positive else 'detected_wrong')
        trials.append({**planned[label],'trial_id':result['id'],'kind':case['kind'],'case_outcome':outcome,
            'native_rewards':rewards,'exception_info':result['exception_info'], 'result':evidence_ref(path,ROOT),
            'proof':evidence_ref(proof_path,ROOT),'proof_validity':proof['validity'],'requirement_ids':proof['requirement_ids'],
            'snapshots':snapshots,'raw_directory':str(path.parent.relative_to(ROOT))})
    assert len(trials)==10 and seen==set(planned)
    groups={}
    for t in trials:groups.setdefault(t['case_mode'],[]).append(t)
    references=groups['reference'];assert len(references)==2 and all(t['case_outcome']=='accepted_correct' for t in references)
    assert all(groups[m][0]['case_outcome']=='missed_wrong' for m in ['duplicate-source','duplicate-output'])
    controls_path=ROOT/'artifacts/validation/customer-dimension-reference-nop-v1/reconciliation.json'
    controls=read_json(controls_path);assert controls['coverage_complete']
    assert len(controls['trials'])==4 and all(t['exception_info'] is None for t in controls['trials'])
    nops=[t for t in controls['trials'] if t['agent']=='nop'];assert len(nops)==2
    assert all(t['native_rewards']=={'reward':0.0} for t in nops)
    task=ROOT/'tasks/ade__ade-bench-ana-eng003'
    pristine=ROOT/'artifacts/validation/customer-control-input-v1/input'
    source=read_json(pristine/'inputs.json')['inputs']['customer']
    expected=derive(source['rows'])
    assert len(expected)==29 and not any(v=='' for r in source['rows'] for v in r.values())
    gold=list(csv.DictReader((task/'tests/seeds/solution__dim_customer.csv').open()))
    derived={str(r['customer_id']):{k:'' if v is None else str(v) for k,v in r.items()} for r in expected}
    assert len(gold)==29 and all(derived[g['customer_id']]==g for g in gold)
    out=ROOT/'validation/evidence/customer-dimension-review';out.mkdir(exist_ok=True)
    review_sources=[evidence_ref(task/p,ROOT) for p in ['instruction.md','environment/setup.sh','environment/project/models/staging/stg_customer.sql',
        'environment/project/models/staging/source.yml','solution/solve.sh','solution/solution.sh','tests/test.sh','tests/test-setup.sh',
        'tests/AUTO_dim_customer_equality.sql','tests/AUTO_dim_customer_existence.sql','tests/seeds/solution__dim_customer.csv']]
    macro=pristine/'public-project/dbt_packages/dbt_utils/macros/generic_tests/equality.sql'
    assert '{{ dbt.except() }}' in macro.read_text()
    summary={'schema_version':1,'task_key':'ade__ade-bench-ana-eng003','native_conclusion':'weak','audit_conclusion':'pending',
        'case_trials':10,'reference_nop_trials':4,'outcomes':dict(Counter(t['case_outcome'] for t in trials)),
        'negative_detection':{'should_detect':6,'detected':sum(t['kind']=='negative' and t['case_outcome']=='detected_wrong' for t in trials)},
        'raw_rows':30,'unique_customer_ids':29,'columns':18,'gold_matches_independent_source_derivation':True,
        'true_build_witnesses':2,'stable_outputs':True,'native_comparison':'Two set differences; row multiplicity is ignored',
        'reviewed_sources':review_sources,'actual_comparator':evidence_ref(macro,ROOT),
        'pristine_input':evidence_ref(ROOT/'artifacts/validation/customer-control-input-v1/result.json',ROOT),
        'manifest':evidence_ref(batch/'manifest.json',ROOT),'reference_nop':evidence_ref(controls_path,ROOT),
        'trials':trials,'scope':'Fixed input and declared cases; actual Qwen contexts and complete supplemental audit remain pending'}
    summary_path=out/'case-summary.json';write_json(summary_path,summary);summary_ref=evidence_ref(summary_path,ROOT)
    write_json(batch/'reconciliation.json',{'planned_trials':10,'observed_trials':10,'coverage_complete':True,'missing':[],
        'outcomes':summary['outcomes'],'trials':trials,'review':summary_ref})
    path=Path(__file__).with_name('contract.yaml');old=read_json(path)
    history=out/('contract-before-'+file_hash(path)+'.json')
    if not history.exists():write_json(history,old)
    specs=[('R-model','Create a dbt model called dim_customer from the public customer data; direct source or existing staging use is allowed.','Existence and ref resolution; native setup rebuilds the model',[]),
        ('R-rename','Rename source id to customer_id.','Equality macro checks full column-name set',['no-rename']),
        ('R-unique','Make customer_id a unique, non-NULL primary key; collapse the identical duplicate source row.','No explicit key uniqueness or row-multiplicity assertion; set differences ignore duplicates',['duplicate-source','duplicate-output']),
        ('R-complete','Retain every distinct source customer.','Full set equality detects missing customer rows',['missing-customer']),
        ('R-values','Retain the source customer attributes with the correct ID.','Full row set comparison preserves column associations',['swapped-keys','wrong-name']),
        ('R-order','No output row order is required.','Set comparison is order-independent',['reordered']),
        ('R-equivalent-sql','Accept equivalent deduplication SQL; the prompt does not prescribe a particular operator.','Output comparison independent of SQL spelling',['distinct'])]
    requirements=[]
    for req_id,text,mapping,modes in specs:
        obs=[t for mode in modes for t in groups[mode]]
        coverage={'status':'pending','reason':'No task-specific negative for model/source submission yet','evidence':[]}
        if obs:
            t=next((t for t in obs if t['case_outcome']=='missed_wrong'),obs[0])
            coverage={'status':'missed_wrong' if t['case_outcome']=='missed_wrong' else 'covered',
                'case_validity':t['proof_validity'],'independent_proof':t['proof'],'checker_evidence':t['result'],
                'evidence':[summary_ref]+[r for t in obs for r in [t['proof'],t['result']]],
                'scope':'Named original-data cases; NULL key and other equivalence branches not yet executed'}
        requirements.append({'id':req_id,'description':text,'critical':True,'native_mapping':mapping,'cases':modes,
            'coverage':{'native':coverage,'audit':{'status':'pending'}}})
    checks={k:{'status':'pending','reason':'Actual Qwen execution context/visibility not yet reviewed','evidence':[]} for k in CHECKS}
    def check(status,reason,refs=None):return {'status':status,'reason':reason,'evidence':refs or [summary_ref]}
    checks.update(assets=check('pass','Fixed input matches every case and independent gold cross-check; actual comparator copied from native container'),
        contract=check('pass','Prompt, native setup, source and reference models, comparator and gold reviewed; requirements and gaps explicit',review_sources+[summary_ref]),
        reference=check('pass','Native reference SQL actually built from public customer staging; all 29 by 18 output cells agree with independent raw-source derivation'),
        reference_repeat=check('pass','Two independent clean builds accepted and independently match the same complete expected rows'),
        nop=check('pass','Both current native untouched trials fail without Harbor exceptions',[t['result'] for t in nops]),
        equivalent=check('pass','Both reversed row order and DISTINCT deduplication are accepted'),
        independent_semantics=check('pass','Deduplication of the only identical repeated key and full source attributes independently computed; fixed gold matches without grader use'),
        negatives=check('gap','Six independently proved negatives: both duplicate variants missed, four other defects detected; NULL-key/workflow negatives remain'),
        comparator=check('fail','Native set difference ignores duplicate row multiplicity and therefore misses the explicit uniqueness requirement'),
        stability=check('pass','Both full output multisets match the same fixed public input; no unstable column removed'))
    contract={**old,'review_status':'reviewed','workflow':'create_customer_dimension_from_existing_staging',
        'reference_kind':'combined','witness_kind':'built_from_inputs',
        'inputs':[{'database':'/app/analytics_engineering.duckdb','table':'main.customer','staging':'main.stg_customer','row_count':30,'unique_ids':29}],
        'deliverables':[{'model_name':'dim_customer','native_example_path':'/app/models/warehouse/dim_customer.sql','relation':'main.dim_customer',
            'key':['customer_id'],'row_count':29,'columns':list(expected[0]),'row_order':'irrelevant',
            'duplicates':'not allowed','nulls':'Preserve nullable source attributes; customer_id must not be NULL'}],
        'requirements':requirements,'checks':checks,'native_success':{'metric':'reward','equals':1.0,'reviewed':True},
        'missing_contract_work':['Execute NULL-key and source/model-workflow negative cases.',
            'Verify actual Qwen context, hidden-asset visibility and cross-context database compatibility.',
            'Independently certify a supplemental multiset/key checker before trusted sampling.'],
        'audit':None,'review_evidence':[summary_ref],'review_history':old.get('review_history',[])+[evidence_ref(history,ROOT)],
        'notes':['The original source has one exact duplicate ID 1 and no conflicting attribute alternatives, so deduplication is unambiguous on this input.',
            'The native equality macro is dbt-utils 1.3.2 captured from the actual environment; its set semantics do not enforce the explicit key constraint.',
            'This task did not qualify as a fully adequate native normal control; successful rejection of four cases does not erase the duplicate misses.']}
    row=next(r for r in load_catalog(ROOT) if r['task_key']==contract['task_key'])
    contract['reviewed_inputs']=review_inputs(identity(ROOT,row,path,'apptainer'))
    write_json(path,contract)
    print({'native':'weak','audit':'pending','outcomes':summary['outcomes']})


if __name__=='__main__':main()

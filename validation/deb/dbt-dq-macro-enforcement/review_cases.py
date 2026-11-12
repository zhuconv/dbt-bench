"""Bind actual DQ cases to a scoped native verdict; never certify untested flags."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import shutil,sys

ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.backends import binding_identity
from dbt_bench.catalog import load_catalog
from dbt_bench.io import digest,evidence_ref,file_hash,read_json,read_jsonl,write_json
from dbt_bench.validation import CHECKS,identity,review_inputs,build_record,summarize
from independent import PRESERVED,encoded


def main():
    key='deb__dbt-dq-macro-enforcement';base=Path(__file__).parent
    out=ROOT/'validation/evidence/dq-review-v1';out.mkdir(parents=True,exist_ok=False)
    stage=ROOT/'artifacts/validation/dq-review-v1'
    rows=load_catalog(ROOT);row=next(r for r in rows if r['task_key']==key)
    batch_path=ROOT/'artifacts/validation/dq-cases-v2/reconciliation.json';batch=read_json(batch_path)
    assert batch['coverage_complete'] and batch['observed_trials']==11 and batch['reference_output_stable']
    assert batch['outcomes']=={'accepted_scoped_positive':3,'detected_wrong':2,'missed_wrong':6}
    groups={}
    for trial in batch['trials']:groups.setdefault(trial['case_mode'],[]).append(trial)
    assert len(groups['reference'])==2
    for trial in batch['trials']:
        assert trial['exception_info'] is None
        if trial['native_rewards']=={'reward':1.0}:
            assert 'SUCCESS: All 26 tests passed' in (ROOT/trial['native_log']['path']).read_text()
    native=read_json(base/'native-reference-server-v1.json')
    assert native['task_hash']==row['adapted_task_hash']
    assert native['backend_binding']==binding_identity(ROOT,row,'apptainer')
    nops=[t for t in native['trials'] if t['agent']=='nop']
    assert len(nops)==2 and all(t['native_rewards']=={'reward':0.0} and t['exception_info'] is None for t in nops)
    sources=[evidence_ref(ROOT/row['run_path']/p,ROOT) for p in ['instruction.md','task.toml','environment/Dockerfile','solution/solve.sh','tests/test.sh','tests/test_outputs.py']]
    initial_path=ROOT/'artifacts/validation/dq-initial-review-v1/initial.json';initial=read_json(initial_path)
    assert [c[0].lower() for c in initial['initial_output']['columns']]==list(PRESERVED)
    assert initial['initial_output']['table_type']==['VIEW']
    old_runs=[]
    for path in (ROOT/'artifacts/validation/dq-reference-v1/jobs').glob('*/*/result.json'):
        result=read_json(path);comparison_path=path.parent/'agent/independent-baseline.json';comparison=read_json(comparison_path)
        assert not (result.get('verifier_result') or {}).get('rewards')
        assert Counter(m['column'] for m in comparison['mismatches'])=={'net_unit_price':22,'unit_price_net_corrected':1,'dq_format_corrected':1}
        old_runs.append({'result':evidence_ref(path,ROOT),'comparison':evidence_ref(comparison_path,ROOT),
                         'native_reward':None,'classification':'validation_proof_gate_rejected_an_unreviewed_exact_decimal_assumption'})
    assert len(old_runs)==2
    summary={'schema_version':1,'task_key':key,'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'native_conclusion':'weak','audit_conclusion':'pending','case_trials':11,'pre_grading_failures':old_runs,
        'outcomes':batch['outcomes'],'negative_detection':{'should_detect':8,'detected':2,'missed':6},
        'source_rows':3576,'reference_builds':2,'stable_output_columns':33,'independently_recomputed_fields':15,
        'reference_semantic_certification':False,'batches':[evidence_ref(batch_path,ROOT)],'trials':batch['trials'],
        'untouched_results':[t['result'] for t in nops],'reviewed_sources':sources,
        'initialized_public_project':evidence_ref(initial_path,ROOT),
        'numeric_runtime':evidence_ref(ROOT/'artifacts/validation/dq-initial-review-v1/numeric-runtime.json',ROOT),
        'numeric_source':evidence_ref(ROOT/'artifacts/validation/dq-initial-review-v1/duckdb-source/download.json',ROOT),
        'cleanup':evidence_ref(stage/'cleanup.json',ROOT),
        'numeric_policy':'The literal pinned DuckDB expression uses DOUBLE division/rounding and a binary 0.01 correction comparison. Decimal sums/products and keys remain exact. Pure Decimal differs on 22 prices and two flags; that diagnostic is retained, not treated as proof that the native reference is wrong.',
        'scope':'Six demonstrated native omissions. Construction, scoped pricing/normalization, stable outputs and reorder acceptance are established; full DQ flag semantics, negative coverage and actual Qwen context remain pending.'}
    write_json(out/'summary.json',summary);proof_ref=evidence_ref(out/'summary.json',ROOT)
    path=base/'contract.yaml';old=read_json(path);old_record=read_json(base/'record.json')
    shutil.copyfile(path,out/'contract-before.json');shutil.copyfile(base/'record.json',out/'record-before.json')
    specs=[
      ('R-grain','Preserve one output row for each source order_line_id; category joins are left joins and may not drop order lines.','Only total count is asserted. All value comparisons inner-join source and output by order_line_id; no key coverage or duplicate-output check.',['duplicate-replaces-missing','missing-row']),
      ('R-preserve-columns','Preserve all ten existing columns and names; add the requested fields.','test_columns_present only requires the new/selected columns; variant_name is never required or read.',['missing-variant-column']),
      ('R-existing-nulls','Introduce no NULL into an existing non-NULL field.','No general null-preservation check. line_total comparison uses abs(expected-actual)>0.0001, which skips NULL.',['null-line-total']),
      ('R-normalization','SKU and product_name use trimmed nonempty primary value, then trimmed nonempty order-line fallback; track fallback use.','Required/format flags recompute these values from inputs; actual output SKU/name are not compared.',['wrong-sku']),
      ('R-category','Left join product primary_category_id to category_id; leaf is trimmed text after the final >, else nonempty category_name, else normalized product_name.','test_product_category_leaf compares matched-key trimmed values; key coverage remains incomplete.',[]),
      ('R-pricing','For every row compute quantity*unit_price-discount+tax; line_profit subtracts quantity*COALESCE(cost_price,0).','line_total uses nullable absolute difference; line_profit uses IS DISTINCT FROM and detects the +1 case.',['null-line-total','wrong-profit']),
      ('R-net-price','Compute round(corrected_total/nullif(quantity,0),2) under the pinned DuckDB expression types.','test_net_unit_price_recomputed uses the same typed SQL expression and nullable abs comparison. No separate semantic boundary negative yet.',[]),
      ('R-corrections','line_total_corrected uses abs(new-old)>0.01; unit_price_net_corrected compares net/unit price with NULL treated as FALSE.','Only the unit-price flag is directly compared, with != that misses NULL; actual line_total_corrected is only required to exist.',['null-net-corrected']),
      ('R-required-fields','Use add_dq_flags on the ten listed required expressions, treating NULL and trimmed empty strings as missing.','test_required_field_flags derives expected missingness from input expressions; test_macro_updates checks for trim(cast text.',[]),
      ('R-regex-macro','Use target-dependent REGEXP_MATCHES/REGEXP_LIKE in dq_flag_potential_pii.','Macro text check accepts target.type OR either function name; actual alternative backend behavior not exercised.',[]),
      ('R-percentile-macro','Use target-dependent quantile_cont/PERCENTILE_CONT in dq_flag_iqr_outlier.','Macro text check accepts any of target.type or the function names; no cross-backend execution.',[]),
      ('R-revenue-flag','Call revenue anomaly macro on abs(total) for CREDIT/RETURNED, else total; preserve its threshold/date rules.','test_revenue_anomaly_flags independently queries source expressions per matching key; no task-specific negative yet.',[]),
      ('R-quantity-flag','Call quantity anomaly macro on abs(quantity) for CREDIT/RETURNED, else quantity.','test_quantity_anomaly_flags recomputes flags on matching keys; no task-specific negative yet.',[]),
      ('R-duplicate-flag','Flag source partitions with more than one order_id/product_id and derive duplicate_suspected.','test_duplicate_flags checks expected partition counts per matching key; does not check multiplicity of final output rows.',[]),
      ('R-shipped-flag','Use shipped-vs-ordered macro with 5 percent tolerance and its declared NULL policy.','test_shipped_vs_ordered_valid uses source quantities and nullable boolean comparison.',[]),
      ('R-return-flag','Use returned-vs-shipped macro with zero tolerance only for RETURNED, otherwise TRUE.','test_return_vs_shipped_valid recomputes source-derived flags on matching keys.',[]),
      ('R-status-flag','PENDING has zero shipped/returned, SHIPPED positive shipped, RETURNED positive returned, CREDIT negative ordered/total, otherwise TRUE.','test_status_qty_valid recomputes rules; reference/grader coalesce PENDING NULL quantities to zero. NULL interpretation still needs review.',[]),
      ('R-iqr-flag','Exclude CREDIT/RETURNED net prices, partition by product_id, apply the existing 1.5 IQR macro.','test_unit_price_iqr_flag derives source quartiles and matches flags; no independent Python quartile/boundary fixtures yet.',[]),
      ('R-order-total-flag','Compare order grand_total with line sum using CREDIT contribution zero and tolerance 0.01.','test_order_total_match independently recomputes source sums, but flags use nullable !=.',[]),
      ('R-pii-flag','Call potential PII macro on COALESCE(notes,analyst_notes).','test_pii_flags repeats source regex patterns. Escape behavior and semantic examples remain independently unreviewed.',[]),
      ('R-nonnegative-flag','Use minimum-zero range check allowing NULL, override CREDIT to TRUE.','test_line_total_nonnegative recomputes the source rule on matching keys.',[]),
      ('R-discount-flag','Exclude CREDIT/RETURNED; NULL ratio gives NULL; negative, >1, >0.70 produce the stated labels in that order.','test_discount_rate_flags recomputes rules; comparison coalesces NULL and empty strings.',[]),
      ('R-margin-flag','Exclude CREDIT/RETURNED; zero revenue, negative profit, >0.80 margin give stated labels in that order.','test_margin_flags recomputes rules; comparison coalesces NULL and empty strings.',[]),
      ('R-date-flag','Use macro to check ordered_at <= line_created_at <= line_updated_at for non-NULL adjacent dates.','test_line_dates_valid uses source timestamps; no boundary negative yet.',[]),
      ('R-category-missing','TRUE when primary category missing, join has no match, or category_name is empty.','test_category_missing_flags recomputes source rule; nullable boolean comparison.',[]),
      ('R-placeholder-overrides','Replace format_corrected with correction/fallback OR, duplicate_suspected with non-NULL duplicate flag, late_arriving with >2 days except CREDIT/RETURNED.','Format, duplicate and late-arriving tests compare recomputed source flags; only format is currently independently recomputed by the helper.',[]),
      ('R-composite','dq_is_valid combines all stated boolean, NULL-category and duplicate requirements.','test_is_valid_composite reconstructs source flags and compares with !=, allowing NULL actual booleans; independent composite proof not completed.',[]),
      ('R-workflow','Modify the existing dbt model/macros, preserve view materialization, and do not modify upstream staging.','Verifier runs dbt for the model and reads macro text; no relation-type or pristine staging check.',['table-materialization']),
      ('R-backend-profile','Read live DB_TYPE before editing; use matching project, retail_dw_master profiles, declared database and dbt deps.','Native scripts use live environment and profile; actual Qwen command order/tool context not checked here.',[]),
      ('R-order','No output row order is required; retain row associations and multiplicity.','Comparisons do not require output order; explicit reorder is accepted.',['reordered'])]
    requirements=[]
    for rid,description,mapping,modes in specs:
        coverage={'status':'pending','reason':'Static mapping or scoped recomputation does not replace this rule negative/equivalence coverage.','evidence':[]}
        if modes:
            trial=groups[modes[0]][0]
            coverage={'status':'missed_wrong' if trial['case_outcome']=='missed_wrong' else 'covered',
                'case_validity':trial['proof_validity'],'independent_proof':trial['independent_proof'],
                'checker_evidence':trial['result'],'evidence':[trial['independent_proof'],trial['result'],proof_ref],
                'scope':'Only the declared executed case; untested subrules remain pending.'}
        requirements.append({'id':rid,'critical':rid!='R-order','description':description,'native_mapping':mapping,
                             'cases':modes,'coverage':{'native':coverage,'audit':{'status':'pending'}}})
    checks={name:{'status':'pending','reason':'Actual Qwen context or full DQ semantic/negative coverage is incomplete.','evidence':[]} for name in CHECKS}
    def check(status,reason,refs=None):return {'status':status,'reason':reason,'evidence':refs or [proof_ref]}
    checks.update(assets=check('pass','Pinned task/image and exact public-input identities checked.'),
        contract=check('pass','Full instruction/reference/verifier and initialized model/macros read; all requirements mapped to checks or explicit gaps.',sources+[proof_ref]),
        reference=check('pass','Two independent native dbt builds accepted; 15 scoped fields match independent Python recomputation. Full DQ semantics remain separately pending.'),
        reference_repeat=check('pass','Two independent initializations with identical source identity and output multiset.'),
        nop=check('pass','Two current-image native untouched runs return zero.',[t['result'] for t in nops]),
        equivalent=check('pass','Reordered full output is accepted and preserves all row multiplicities/values.'),
        stability=check('pass','All 3576 rows and 33 columns match across both clean references, with no column omitted.'),
        negatives=check('gap','Eight independently established errors executed; two detected and six missed. Other required-rule negatives remain pending.'),
        comparator=check('fail','Same-count duplicate/missing key and NULL outputs are missed by inner joins and nullable comparisons.'))
    contract={**old,'review_status':'reviewed','workflow':'modify_existing_order_line_model_and_dq_macros',
        'reference_kind':'combined','witness_kind':'built_from_inputs',
        'construction_observation':{'runs':2,'built_without_gold':True,'full_semantics_certified':False},
        'inputs':[{'path':'/app/database/retail.duckdb','tables':list(initial['inputs']),'role':'fixed public staging relations'}],
        'deliverables':[{'path':'/app/dbt_models_duckdb/models/marts/sales/fct_order_line_detail.sql','kind':'existing dbt model'},
            {'path':'/app/dbt_models_duckdb/macros/data_quality/','kind':'updated DQ macros'},
            {'path':'/app/database/retail.duckdb','table':'main.fct_order_line_detail','table_type':'VIEW','preserved_columns':list(PRESERVED),'key':['order_line_id']}],
        'comparison_contract':{'key':'order_line_id exact; one row per fixed source key','row_order':'irrelevant','duplicates':'reject',
            'preserved_columns':list(PRESERVED),'extra_columns':'No prohibition stated; retain required existing fields and append DQ fields',
            'pricing':'Decimal multiplication/addition remains exact; net-price division/round and its correction threshold follow the explicit pinned DOUBLE SQL expression, independently modeled.',
            'scope':'Helper verifies 15 declared fields and structural properties, not all DQ flags or a certified supplemental audit.'},
        'requirements':requirements,'checks':checks,'native_success':{'metric':'reward','equals':1.0,'reviewed':True},
        'missing_contract_work':['Independently verify remaining DQ flags, PII regex semantics, IQR/NULL/boundary rules and composite validity.',
            'Execute requirement-specific negatives for remaining flags, conditional macros and upstream immutability.',
            'Check actual Qwen tool/verifier execution contexts, hidden-asset visibility and full submission transfer.',
            'Validate an independent supplemental checker before any training eligibility; current helper is only scoped proof.'],
        'review_evidence':old.get('review_evidence',[])+[proof_ref],
        'review_history':old.get('review_history',[])+[evidence_ref(out/'contract-before.json',ROOT)]}
    write_json(path,contract);contract['reviewed_inputs']=review_inputs(identity(ROOT,row,path,'apptainer'));write_json(path,contract)
    record=build_record(ROOT,row,path,backend='apptainer')
    assert record['review_current'] and record['native']['conclusion']=='weak' and record['audit']['conclusion']=='pending'
    write_json(base/'history'/f'{digest(old_record)}.json',old_record);write_json(base/'record.json',record)
    records=read_jsonl(ROOT/'validation/ledger.jsonl')
    assert sum(r['task_key']==key for r in records)==1
    write_json(stage/'ledger-before.json',records)
    records=[record if r['task_key']==key else r for r in records]
    print(summarize(ROOT,rows,records))


if __name__=='__main__':main()

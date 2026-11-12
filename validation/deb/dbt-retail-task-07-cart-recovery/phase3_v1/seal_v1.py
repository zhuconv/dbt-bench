"""Seal two additive reviews, including their failed actual candidate scores."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
from collections import Counter
import json
from dbt_bench import io
from phase3_review import binding, precheck
from phase3_publish import reproduction, candidate_evaluation
from dbt_bench.validation import CHECKS
R = Path.cwd()
B = R / 'artifacts/validation/phase3-deb-batch06-rebuild-v2'
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/shared_projects/cart-trust-phase3-v1'))

def ref(p):
    return io.evidence_ref(p, R)

def save(p, value):
    assert not p.exists(), p
    io.write_json(p, value)
    return ref(p)

def main():
    actual = io.read_json(D / 'actual-review-v3/summary.json')
    fixtures = io.read_json(D / 'fixtures-v1/summary.json')
    workflow = io.read_json(D / 'workflow-fixtures-v2/summary.json')
    typed = io.read_json(D / 'typed-fixtures-v1/summary.json')
    cases = io.read_json(D / 'native-case-review-v2/summary.json')
    inherited = io.read_json(D / 'unchanged-case-bindings-v1.json')['records'] + io.read_json(D / 'unchanged-workflow-bindings-v1.json')['records']
    assert len(fixtures['cases']) == 389 and len(workflow['records']) == 41 and (len(typed['records']) == 24) and (len(cases['records']) == 21)
    assert fixtures['all_expected'] and workflow['all_passed'] and typed['all_expected']
    handoffs = []
    for task in ('deb__dbt-retail-task-07-cart-recovery', 'deb__dbt-retail-task-10-trust-risk'):
        folder = R / 'validation/deb' / task.removeprefix('deb__') / 'phase3_v1'
        out = D / 'sealed-v1' / task
        cart = '-07-' in task
        old = io.read_json(folder.parent / 'contract.yaml')
        ident = binding(B, task + '--phase3-blind-v1')
        trials = [t for t in actual['trials'] if t['task_key'] == task]
        assert len(trials) == 2
        actual_rows = [v for t in trials for v in t['records']]
        assert len(actual_rows) == 6 and all((v['audit_reward'] == 0 for v in actual_rows))
        evidence = [ref(D / 'actual-review-v3/summary.json'), ref(D / 'mechanical-review-v1/summary.json'), ref(D / 'fixtures-v1/summary.json'), ref(D / 'workflow-fixtures-v2/summary.json'), ref(D / 'typed-fixtures-v1/summary.json'), ref(D / 'execution-gates-v1/summary.json'), ref(D / 'native-case-review-v2/summary.json'), ref(D / 'unchanged-case-bindings-v1.json'), ref(D / 'unchanged-workflow-bindings-v1.json')]
        problem = {'task_key': task, 'actual_trials': [{'trial': t['trial'], 'native_result': t['native_result'], 'execution': t['execution'], 'stages': t['records']} for t in trials], 'model_requests': 0, 'gold_used_to_derive_expected': False, 'native_reward_each': 1, 'audit_rewards': [v['audit_reward'] for v in actual_rows], 'business_value_scope': 'Cart all129 delivered required fields match independent input recomputation; Trust all196 required fields match for value diagnosis after DATE-to-midnight normalization. Neither diagnostic hides physical interface failures.', 'observed_defect': 'The added instruction explicitly requires items_total in both cart_base and final. The delivered final table has no items_total column.' if cart else 'review_month is physically DATE instead of required TIMESTAMP; unverified_purchase_flag CASE uses ELSE TRUE despite the explicit all-flags ELSE FALSE requirement. The latter is a form violation, not a wrong Boolean value.', 'counterexample_scope': 'Actual successful blind input builds are accepted by unchanged native verifier despite these explicit current interface/workflow defects.', 'independent_review': ref(D.parents[1] / 'spider2/publisher-review-v1/cart-trust-independent-schema-review-v1.json'), 'full_legal_build_status': 'gap: Both supplied blind source candidates were actually built twice and inspected; neither is a complete legal candidate under the patched instruction. Table fixtures are not a replacement legal dbt build. Author SQL and instructions remain untouched.'}
        diagnosis = save(out / 'candidate-defects.json', problem)
        requirements = [{k: v for k, v in q.items() if k in ('id', 'critical', 'description', 'kind', 'source')} for q in old['audit']['requirements']]
        new_id = 'P3-null-item-rollup' if cart else 'P3-null-optional-aggregates'
        requirements.append({'id': new_id, 'critical': True, 'description': 'Preserve absent item-rollup NULL values for item_line_count/distinct_variants/total_quantity/items_total in both cart_base and final, including physical delivery of final.items_total.' if cart else 'Preserve NULL optional grouped counts/amounts including product_event_7d_count when no event is in the review window; do not coalesce missing groups to zero.', 'source': ident['instruction_sha256']})
        contract = save(out / 'contract.json', {'schema_version': 1, 'task_key': task, 'review_status': 'reviewed', 'requirements': requirements, 'workflow': old['workflow'], 'original_contract': ref(folder.parent / 'contract.yaml'), 'instruction': ref(B / 'tasks' / ident['label'] / 'instruction.md'), 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'literal_interface': 'Five named relations; all130 required fields, exact keys; Cart permits additional columns and row/physical column reordering.' if cart else 'Three named views; all196 required columns in exact declared order and declared SQL type families. Row order is not mandated.', 'reference_and_witness_scope': 'Two actual clean blind dbt builds from original inputs. They witness execution, but the actual completed submissions fail literal task requirements. No repaired author solution is introduced.', 'table_schema_source': ref(folder / ('policy-inherited-v1.json' if cart else 'schemas-v1.json')), 'candidate_defects': diagnosis, 'protocol_evidence': evidence})
        native_coverage = {}
        for oldcase in inherited:
            if oldcase['task_key'] != task:
                continue
            c = deepcopy(oldcase['coverage'])
            c['reason'] = 'Current input/image/native-verifier identity rechecked; reused only the unchanged named rule and exact archived submission/counterexample. ' + c['reason']
            c['evidence'] = c['evidence'] + [ref(D / 'unchanged-case-bindings-v1.json'), ref(D / 'unchanged-workflow-bindings-v1.json')]
            native_coverage[oldcase['requirement_id']] = c

        def missed(rid, reason):
            native_coverage[rid] = {'status': 'missed_wrong', 'case_validity': 'business_error', 'reason': reason, 'independent_proof': diagnosis, 'checker_evidence': trials[0]['native_result'], 'evidence': [diagnosis, trials[0]['native_result'], trials[1]['native_result']]}
        if cart:
            missed(new_id, 'Actual final output omits explicitly required items_total, yet both actual unchanged native verifiers return1. No aggregate value was fabricated to demonstrate this.')
        else:
            missed('R-instruction-L348', 'Actual DATE review_month violates explicit TIMESTAMP interface; unchanged native accepts both actual candidate builds. DATE-to-midnight comparison is only a value diagnosis.')
            current_null = [c for c in cases['records'] if c['task_key'] == task and c['case'] in ('returns', 'coupon', 'events')]
            assert len(current_null) == 3
            native_coverage[new_id] = {'status': 'covered' if all((c['native_reward'] == 0 for c in current_null)) else 'missed_wrong', 'case_validity': 'business_error', 'reason': 'Three actual source builds change independently NULL optional counts to nonzero values; native results preserve each outcome. Baseline actual build native1; each target was proved wrong before verifier.', 'evidence': [c['review'] for c in current_null], 'independent_proof': current_null[0]['independent_proof'], 'checker_evidence': current_null[0]['native_result']}
        assert set(native_coverage) == {q['id'] for q in requirements}, (task, set((q['id'] for q in requirements)) - set(native_coverage))
        ff = {c['case']: c['evidence'] for c in fixtures['cases'] if c['task_key'] == task}
        ww = {c['case']: c['proof'] for c in workflow['records'] if c['task_key'] == task}
        tt = {(c['model'], c['case']): c['evidence'] for c in typed['records'] if c['task_key'] == task}
        if cart:
            f, base, sess, inv, cust = ('fct_cart_recovery_priority', 'int_cart_recovery__cart_base', 'int_cart_recovery__session_signals', 'int_cart_recovery__inventory_risk', 'int_cart_recovery__customer_context')
            selections = {'R-workflow': [ww['deleted-current-model'], ww['wrong-folder']], 'R-base': [ff[base + '--wrong-cart_item_count_reported']], 'R-session': [ff[sess + '--wrong-event_count']], 'R-inventory': [ff[inv + '--wrong-min_quantity_available']], 'R-customer': [ff[cust + '--wrong-email_consent']], 'R-recovery': [ff[f + '--wrong-is_recovered']], 'R-priority': [ff[f + '--wrong-priority_score']], 'R-final': [ff[f + '--missing-column'], diagnosis], 'R-backend': [ww['late-backend-echo']], 'R-clock': [ff[f + '--wrong-scored_at'], ww['dynamic-clock-expression']], 'R-idempotency': [ff[f + '--wrong-scored_at'], ref(D / 'mechanical-review-v1/summary.json')], 'R-materialization': [ww['wrong-materialization'], ff[f + '--wrong-relation-kind']], 'R-tags': [ww['missing-tags']], 'R-refs': [ww['direct-relation-bypasses-ref'], ww['nonstaging-ref'], ww['forbidden-source-dependency']], new_id: [ff[base + '--wrong-items_total'], ff[f + '--wrong-items_total'], diagnosis]}
        else:
            f, base, kpi = ('fct_review_moderation_risk', 'int_reviews__enriched', 'rpt_review_moderation_kpis')
            selections = {'R-flag-case-form': [ww['actual-else-true'], ww['case-form-else-false']], 'R-instruction-L348': [tt[kpi, 'wrong-sql-type'], diagnosis], 'R-project-paths': [ww['wrong-folder']], 'R-output-order': [tt[f, 'reversed-physical-columns']], 'R-output-columns': [ff[f + '--missing-column']], 'R-matching': [ff[base + '--wrong-order_line_id']], 'R-return-header': [ff[base + '--wrong-return_count']], 'R-staging-refs': [ww['missing-staging-ref'], ww['direct-relation-bypasses-ref']], 'R-kpi-share': [ff[kpi + '--wrong-verified_purchase_share']], 'R-schema-tests': [ww['missing-schema-yaml'], ww['missing-key-schema-tests']], 'R-events': [ff[base + '--wrong-product_event_7d_count']], 'R-penalties': [ff[f + '--wrong-score_deductions']], 'R-review-timestamp': [ff[base + '--wrong-review_ts']], 'R-materialization': [ww['wrong-materialization']], 'R-kpi-form': [ww['missing-kpi-percentage-form']], 'R-queue': [ff[f + '--wrong-queue_priority']], 'R-fraud': [ff[base + '--wrong-fraud_provider'], ww['wrong-fraud-alias']], 'R-coupons': [ff[base + '--wrong-coupon_redemption_count']], 'R-context-joins': [ff[base + '--wrong-email_verified']], 'R-keep-reviews': [ff[base + '--missing-row']], 'R-cte-alias': [ww['missing-cte-review-status']], 'R-review-velocity': [ff[base + '--wrong-customer_review_count_day']], 'R-kpi-grain': [ff[kpi + '--duplicate']], 'R-kpi-aggregates': [ff[kpi + '--wrong-review_count']], 'R-tags': [ww['missing-tags']], 'R-return-lines': [ff[base + '--wrong-product_return_qty']], 'R-datediff': [ww['wrong-datediff-name']], 'R-trust-score': [ff[f + '--wrong-trust_score']], 'R-risk-bucket': [ff[f + '--wrong-risk_bucket'], ww['risk-from-wrong-column']], 'R-profile': [ww['wrong-profiles-env']], 'R-extra-metrics': [ff[f + '--wrong-rating_delta']], 'R-matching-implementation': [ww['wrong-matching-rank'], ww['wrong-matching-date']], 'R-backend-order': [ww['late-backend-echo']], 'R-completion-build': [ref(D / 'execution-gates-v1/summary.json')], 'BR-executed-low-content-flag': [ff[f + '--wrong-low_content_flag']], 'R-score-sql-form': [ww['missing-same-cte-score'], ww['reference-deductions-in-score']], new_id: [ff[base + '--wrong-return_count'], ff[base + '--wrong-product_event_7d_count']]}
            for flag in ['fraud_high_flag', 'unverified_purchase_flag', 'return_flag', 'customer_velocity_flag', 'product_velocity_flag', 'rating_outlier_flag', 'coupon_used_flag']:
                selections['R-' + flag] = [ff[f + '--wrong-' + flag]]
        assert set(selections) == {q['id'] for q in requirements}
        audit_coverage = {rid: {'status': 'covered', 'reason': 'The named output/workflow predicate is tested by independently established finite wrong/right fixtures or the actual candidate. Fixture coverage is component-scoped and does not certify a complete legal build or unseen inputs.', 'evidence': refs} for rid, refs in selections.items()}
        case_map = {'abandoned': 'R-base', 'item-rollup': new_id, 'elapsed-hours': 'R-clock', 'session-events': 'R-session', 'inventory': 'R-inventory', 'consent': 'R-customer', 'priority-score': 'R-priority', 'channel': 'R-priority', 'recovery-window': 'R-priority', 'recovered': 'R-recovery'} if cart else {'order-match': 'R-matching', 'returns': 'R-return-header', 'coupon': 'R-coupons', 'events': 'R-events', 'velocity': 'R-review-velocity', 'flags': 'R-rating_outlier_flag', 'latency': 'R-extra-metrics', 'score': 'R-trust-score', 'risk': 'R-risk-bucket', 'kpi-count': 'R-kpi-aggregates', 'kpi-mean': 'R-kpi-aggregates'}
        for c in cases['records']:
            if c['task_key'] != task:
                continue
            assert c['audit_target_detected_all_stages']
            rid = case_map[c['case']]
            audit_coverage[rid]['evidence'].append(c['review'])
        checks = {}

        def check(name, status, reason, refs):
            checks[name] = {'status': status, 'reason': reason, 'evidence': refs, 'execution_evidence': refs}
        check('assets', 'pass', 'Current original task, copied variant instruction, blind source, original native checker, locked image and two actual clean build captures are hash-bound.', [ref(B / 'manifest.json'), contract, ref(D / 'actual-review-v3/summary.json')])
        check('contract', 'pass', 'Every declared critical rule has explicit native/audit coverage or scoped component evidence. Current literal missing-column/type/form defects are recorded, not explained away by value agreement.', [contract, diagnosis])
        check('reference', 'fail', 'Both supplied blind implementations were actually built from clean original inputs; native1 but current literal audit0. They are not complete correct positive audit witnesses.', [diagnosis, ref(B / 'reconciliation-v1.json')])
        check('reference_repeat', 'fail', 'Both independent clean builds repeat the same actual candidate defects and audit0; repeat execution is complete, acceptance as a legal reference fails.', [diagnosis, ref(D / 'mechanical-review-v1/summary.json')])
        check('nop', 'pass', 'Actual untouched new-version submissions have no required output relations and fail both native and the missing-output workflow gate in all three captured stages.', [ref(R / 'artifacts/validation/phase3-deb-cart-trust-nop-v1/reconciliation-v1.json'), ref(D / 'execution-gates-v1/summary.json')])
        check('stability', 'pass', 'All complete physical schemas, SQL types and typed row multisets agree across two clean builds and all three agent/verifier stages. Stable illegal output is not counted as correct.', [ref(D / 'mechanical-review-v1/summary.json')])
        check('environment_agent', 'pass', 'Actual locked Qwen shell captures Python3.12, DuckDB1.2.2, dbt executable/version/PATH and database access; blind dbt commands actually succeed.', [ref(D / 'mechanical-review-v1/summary.json'), ref(D / 'actual-review-v3/summary.json')])
        check('environment_verifier', 'pass', 'Observer on the original test.sh path reads the same actual Qwen-built output. Native scores and logs remain unchanged.', [ref(D / 'mechanical-review-v1/summary.json'), ref(B / 'reconciliation-v1.json')])
        check('cross_context', 'pass', 'All required outputs retain schemas and typed row multiplicities across actual agent submission/native-before/native-after, with unchanged declared raw-input projections.', [ref(D / 'mechanical-review-v1/summary.json')])
        check('agent_visibility', 'pass', 'Actual initial Qwen-shell hidden-path/content/mount scans found no gold/hidden reference exposure within recorded exclusions; source install follows the scan.', [ref(D / 'mechanical-review-v1/summary.json')])
        check('independent_semantics', 'fail', 'Independent Python/raw-input derivation matches all delivered business values, but current actual candidate violates an explicit output/interface requirement; Trust CASE is a literal form failure, not a Boolean value mismatch.', [diagnosis, ref(D / 'actual-review-v3/summary.json')])
        check('negatives', 'pass', 'Each critical grouped rule has an independently explained executed table/workflow counterexample; actual21 source mutations are assessed by the named wrong value across3stages, separately from background candidate defects. Scope is fixed current inputs plus declared fixtures.', [ref(D / 'native-case-review-v2/summary.json'), ref(D / 'fixtures-v1/summary.json'), ref(D / 'workflow-fixtures-v2/summary.json'), ref(D / 'typed-fixtures-v1/summary.json'), ref(D / 'execution-gates-v1/summary.json')])
        check('equivalent', 'gap', 'Executed independent complete-table positives, row reversal and permitted column alternatives are accepted, but two actual full blind dbt builds are not legal complete submissions. No full legal alternative build can therefore be certified from the supplied candidates; fixture success is not promoted to a solution.', [diagnosis, ref(D / 'fixtures-v1/summary.json'), ref(D / 'typed-fixtures-v1/summary.json')])
        check('comparator', 'pass', '389table+24physical-interface+41workflow fixtures and4general regressions executed: exact/large IDs, keyed tolerance, duplicates/missing rows, key/value association, physical columns/types, source delivery and SQL forms. Table function ASTs are identical across engine v2/v3; workflow changed and was rerun.', [ref(D / 'fixtures-v1/summary.json'), ref(D / 'typed-fixtures-v1/summary.json'), ref(D / 'workflow-fixtures-v2/summary.json')])
        assert set(checks) == set(CHECKS)
        native_checks = deepcopy(checks)
        for name in ('reference', 'reference_repeat'):
            native_checks[name].update(status='pass', reason='The actual blind models were built from clean inputs twice and accepted by unchanged native verifier both times. Independent semantic/interface failures remain separately recorded; native acceptance alone is not correctness certification.')
        native_checks['negatives'].update(status='fail', reason='Identity-bound executed unchanged-rule cases and current actual missing-column/type/form counterexamples establish native missed errors. Current21 source-error results remain separately reported.')
        native_checks['comparator'].update(status='fail', reason='Unchanged native accepts actual submissions with an explicit missing required column or wrong physical timestamp type. New audit fixtures do not retroactively repair native comparison.')
        code = [folder / 'derive_phase3_v1.py', folder / 'collect_v1.py', folder / 'project_workflow_v1.py', folder / 'project_workflow_v2.py', folder / 'policy-inherited-v1.json', folder.parent / 'audit_phase2_v1' / ('check_v2.py' if cart else 'check_v4.py'), D / 'cart_trust_engine_v2.py', D / 'cart_trust_engine_v3.py', D / 'cart_trust_fixtures_v1.py', D / 'cart_trust_mechanical_v1.py', D / 'cart_trust_native_engine_v2.py', R / 'scripts/audit_tables.py', R / 'scripts/phase3_capture.py', R / 'scripts/mechanical_observer.py', R / 'src/dbt_bench/comparison.py', R / 'src/dbt_bench/io.py']
        if not cart:
            code.append(folder / 'schemas-v1.json')
        common = R / 'validation/deb/dbt-retail-task-07-cart-recovery/phase3_v1'
        code += [common / n for n in ('workflow_fixture_v2.py', 'typed_fixture_v1.py', 'review_native_cases_v2.py', 'bind_unchanged_cases_v1.py')]
        code += [R / 'validation/deb/dbt-retail-task-10-trust-risk/phase3_v1' / n for n in ('bind_workflow_cases_v1.py', 'execution_gate_fixture_v1.py')]
        code_refs = [ref(p) for p in code]
        overlays = {}
        for target, cs, coverage in [('native', native_checks, native_coverage), ('audit', checks, audit_coverage)]:
            op = folder / (target + '-overlay-v1.json')
            value = {'schema_version': 1, 'task_key': task, 'target': target, 'checker_version': 'phase3-cart-trust-literal-input-v1' if target == 'audit' else 'unchanged-native-' + ident['native_verifier']['sha256'][:16], 'scope': 'Current instruction variant, fixed original inputs, exact actual source/build/output evidence. Native scores preserved; component fixtures do not certify a complete legal build.', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'review_status': 'reviewed', 'base_identity': ident['base_identity'], 'phase3_identity': ident, 'contract': contract, 'code_refs': code_refs, 'checks': cs, 'requirement_coverage': coverage, 'diagnoses': [], 'candidate_defects': diagnosis, 'model_requests': 0, 'native_rewards_modified': False}
            overlays[target] = save(op, value)
            pre = precheck(op)
            assert pre['review_complete'] and pre['conclusion'] == ('weak' if target == 'native' else 'pending'), pre
            save(out / (target + '-precheck.json'), pre)
        first_model = 'int_cart_recovery__cart_base' if cart else 'int_reviews__enriched'
        candidate_rows = []
        for row in actual_rows:
            candidate_rows.append({**{k: row[k] for k in ('trial', 'stage', 'candidate_snapshot', 'audit_result', 'audit_reward', 'spec')}, 'audit_field_paths': {'reward': ['reward'], 'candidate_snapshot': ['tables', first_model, 'capture'], 'task_key': ['task_key']}})
        audit = io.read_json(R / overlays['audit']['path'])
        candidate_path = out / 'candidate-evaluation.json'
        candidate = save(candidate_path, {'schema_version': 1, 'task_key': task, 'phase3_identity': ident, 'model_requests': 0, 'qualified_overlay': overlays['audit'], 'checker_version': audit['checker_version'], 'code_refs': audit['code_refs'], 'candidate_pass': False, 'records': candidate_rows, 'scope': 'All six actual current candidate scores are0; independent from checker protocol completeness.'})
        rebuilt = reproduction(B / 'reconciliation-v1.json', ident)
        verified = candidate_evaluation(candidate_path, ident, rebuilt, qualified_overlay=audit)
        assert verified['status'] == 'fail' and verified['scores'] == [0] * 6
        validation = save(out / 'candidate-readonly-verification.json', verified)
        handoffs.append({'task_key': task, 'native_overlay': overlays['native'], 'audit_overlay': overlays['audit'], 'candidate_review': candidate, 'candidate_validation': validation, 'reconciliation': ref(B / 'reconciliation-v1.json'), 'native_conclusion': 'weak', 'audit_conclusion': 'pending', 'pending_checks': 0, 'candidate_pass': False, 'reason': problem['observed_defect'], 'model_requests': 0, 'native_rewards_modified': False})
        print(task, 'sealed', {'native': 'weak', 'audit': 'pending', 'candidate': 'fail'}, flush=True)
    save(D / 'handoff-v1.json', {'records': handoffs, 'model_requests': 0, 'native_rewards_modified': False})
if __name__ == '__main__':
    main()

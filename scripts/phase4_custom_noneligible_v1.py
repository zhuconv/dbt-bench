"""Seal actual ABC/GL counterexamples without changing native scores or history."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import argparse, json, sys, math
from decimal import Decimal
from dbt_bench import io
from phase4_review import binding, precheck
from phase4_publish import reproduction, candidate_evaluation
from phase4_customer4_review_v3 import ROOT, ref, write
B = Path(_release_expand('${ARTIFACT_ROOT}/validation'))
P = B / 'phase4-review-v1/pipeline/custom-v1'
OLD = {'dbt-abc-classification': ROOT / 'validation/deb/dbt-abc-classification/audit_phase2_v1/overlay-v1.json', 'dbt-gl-reconciliation': B / 'phase2-review-v1/deb_operations/ltv-gl-v1/gl-audit-review-v4.json'}

def unique(xs):
    return list({str((ROOT / r['path']).resolve()): r for r in xs}.values())

def main(task):
    key = 'deb__' + task
    out = P / task / 'sealed-v1'
    freshpath = P / task / 'fresh-v2/fresh-review.json'
    fresh = io.read_json(freshpath)
    fr = ref(freshpath)
    if out.exists():
        raise ValueError('Already sealed')
    assert fresh['six_stage_rewards'] == [0] * 6 and fresh['repeat_equal'] and fresh['all_context_values_equal']
    batch = Path(fresh['batch']['path']).parent
    ident = binding(batch, fresh['label'])
    base = io.read_json(ROOT / ident['base_record']['path'])
    old = io.read_json(OLD[task])
    contract = io.read_json(ROOT / 'validation/deb' / task / 'contract.yaml')
    rebuilt = reproduction(batch / 'reconciliation-v1.json', ident)
    assert rebuilt['native1_both_clean_builds']
    assert old['base_identity']['backend_binding'] == ident['base_identity']['backend_binding']
    assert all((io.evidence_ok(r, ROOT) for r in old['code_refs']))
    rule = 'R-date-format' if task.endswith('classification') else 'BR-core-account-transaction-aggregation'
    findings = []
    for r in fresh['records']:
        wf = io.read_json(ROOT / r['workflow']['path'])
        a = io.read_json(ROOT / r['stages'][0]['audit_result']['path'])
        if task.endswith('classification'):
            assert wf['checks']['explicit_date_format']['status'] == 'fail'
            assert a['checks']['concentration_placement']['status'] == 'gap'
            assert all((c['status'] == 'pass' for n, c in a['checks'].items() if n not in ['workflow', 'concentration_placement']))
            detail = {'literal_workflow_violation': wf['checks']['explicit_date_format'], 'actual_unresolved_requirement': a['checks']['concentration_placement'], 'arithmetic': 'All currently declared output columns pass source-derived comparison. Four-ULP allowance is restricted to the unrounded intermediate weighted-average DOUBLE; monetary decimal/ID columns stay exact.'}
        else:
            pop = a['checks']['account_all_transactions_population']
            assert pop['status'] == 'fail' and len(pop['missing_account_ids']) == 15
            detail = {'literal_population_violation': pop, 'scope': 'Raw transaction IDs include35 accounts; blind output includes20 chart-known accounts. The 15 omitted IDs independently establish violated all-transactions membership. Orphan metadata representation and trial-side convention remain unresolved and do not invalidate this membership counterexample.', 'other_findings': {n: c for n, c in a['checks'].items() if c['status'] != 'pass'}}
        findings.append({'trial': r['trial'], 'raw_input': r['raw_refs'], 'native_result': r['result'], 'actual_submission': r['stages'][0]['snapshot'], 'workflow': r['workflow'], 'audit': r['stages'][0]['audit_result'], 'details': detail})
    finding = write(out / 'current-counterexamples.json', {'task_key': key, 'records': findings, 'native_rewards_preserved': True, 'model_requests': 0, 'validity': 'business_error', 'tested_checker_invoked_to_define_validity': False, 'reason': 'Explicit requirement plus actual compiled call/raw input membership establishes the violation before native-result comparison. Correct limited numeric components do not rescue a violated task requirement.'})
    testrefs = []
    if task.endswith('classification'):
        sys.path.insert(0, str(ROOT / 'validation/deb' / task / 'phase4_v1'))
        import audit_v2 as a
        assertions = {'binary_ratio_representation': a.eq(81.12666666666667, Decimal(24338) / 300, True), 'semantic_cent_rejected': not a.eq(81.13666666666667, Decimal(24338) / 300, True), 'large_id_exact': not a.eq(9007199254740993, 9007199254740992), 'integer_identity_exact': not a.eq(10001, 10000), 'null_exact': not a.eq(None, Decimal(0))}
        assert all(assertions.values())
        testrefs.append(write(out / 'numeric-representation-selftest.json', {'checks': assertions, 'passed': True, 'scope': 'Actual unit test of new intermediate-only4ULP comparison, with exact integral IDs and meaningful numeric errors.'}))
    requirements = [{k: deepcopy(r[k]) for k in ('id', 'description', 'critical') if k in r} for r in contract['audit']['requirements'] if r['id'] in old['requirement_coverage']]
    assert {r['id'] for r in requirements} == set(old['requirement_coverage'])
    cr = write(out / 'contract.json', {'schema_version': 1, 'task_key': key, 'review_status': 'reviewed', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'workflow': contract['workflow'], 'inputs': contract['inputs'], 'deliverables': contract['deliverables'], 'requirements': requirements, 'instruction': ref(batch / 'tasks' / fresh['label'] / 'instruction.md'), 'source_contract': ref(ROOT / 'validation/deb' / task / 'contract.yaml'), 'interpretation': 'All prior ambiguity and failed-case evidence retained. This fresh blind witness is executable and native1 twice, but violates a separately identifiable literal requirement; no correct full witness is certified.', 'finding': finding, 'model_requests': 0})
    bindingref = write(out / 'retained-binding.json', {'task_key': key, 'phase4_identity': ident, 'old_audit': ref(OLD[task]), 'input_image_native_identity_matches': True, 'old_evidence_rehashed': True, 'source_current_review': fr, 'scope': 'Instruction/native verifier/data/image are unchanged for these agree tasks. Old native and partial audit case evidence remains scoped to unchanged rules; no claim that new partial checker is qualified. New blind reference is evaluated separately on all six actual captures.'})
    codes = unique(old['code_refs'] + fresh['code_refs'] + [ref(Path(__file__))])
    overlays = {}
    for target in ('native', 'audit'):
        checks = deepcopy(base['native']['checks'] if target == 'native' else old['checks'])
        coverage = deepcopy({r['id']: r['coverage']['native'] for r in contract['audit']['requirements'] if r['id'] in old['requirement_coverage']} if target == 'native' else old['requirement_coverage'])
        for name, c in checks.items():
            assert c['status'] != 'pending', name
            if not c.get('execution_evidence') and name not in ('assets', 'contract'):
                c['execution_evidence'] = deepcopy(c['evidence'])
        for name in ('assets', 'environment_agent', 'environment_verifier', 'cross_context', 'agent_visibility', 'stability'):
            checks[name] = {'status': 'pass', 'reason': 'Actual two clean locked-shell builds with successful compatible DB access, original test.sh observers, initial visibility scan, and complete typed output equality across trials/native boundaries. Scope is captured paths and unchanged fixed input.', 'evidence': [fr, bindingref], 'execution_evidence': [fr]}
        checks['contract'] = {'status': 'gap', 'reason': 'Actual per-rule review retains unresolved output placement/semantic conventions documented by independent execution; literal counterexample is conclusive separately.', 'evidence': [cr, finding, ref(OLD[task])], 'execution_evidence': [fr, finding]}
        for name in ('reference', 'reference_repeat'):
            checks[name] = {'status': 'fail', 'reason': 'Two clean blind builds each get native1 but violate an explicit requirement; they are real input-built submissions, not certified correct reference witnesses.', 'evidence': [fr, finding, rebuilt['reconciliation']], 'execution_evidence': [fr, finding]}
        checks['independent_semantics'] = {'status': 'gap' if task.endswith('classification') else 'fail', 'reason': 'Current input arithmetic and keyed output review actually ran at all six boundaries; concentration placement remains unresolved for ABC, while GL violates all-account membership.', 'evidence': [fr, finding, cr], 'execution_evidence': [fr, finding]}
        checks['equivalent']['evidence'] += [fr, finding]
        checks['equivalent']['execution_evidence'] += [fr, finding]
        checks['equivalent']['reason'] += ' Current blind reference actually violates a task requirement; no complete lawful equivalent certification is inferred from native1.'
        if target == 'audit':
            checks['negatives']['evidence'] += [finding, bindingref]
            checks['negatives']['execution_evidence'] += [finding]
            checks['comparator']['evidence'] += testrefs
            checks['comparator']['execution_evidence'] += testrefs
        for c in coverage.values():
            c['evidence'] += [bindingref]
        if target == 'native':
            coverage[rule] = {'status': 'missed_wrong', 'case_validity': 'business_error', 'reason': 'The fresh blind witness itself violates an explicit requirement despite native1. Violation derives from actual source/raw input rather than tested-checker rejection.', 'evidence': [finding, fr], 'independent_proof': finding, 'checker_evidence': fresh['records'][0]['result']}
        overlay = {'schema_version': 1, 'task_key': key, 'target': target, 'phase4_identity': ident, 'base_identity': ident['base_identity'], 'checker_version': ident['native_verifier']['sha256'] if target == 'native' else 'phase4-custom-finite-rejection-v1', 'contract': cr, 'review_status': 'reviewed', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'checks': checks, 'requirement_coverage': coverage, 'code_refs': codes, 'model_requests': 0, 'scope': 'Actual complete protocol investigation with conclusive current-candidate rejection and explicit unresolved requirements. This partial audit remains unqualified; old native rewards and historical records unchanged.'}
        path = out / (target + '-overlay.json')
        write(path, overlay)
        write(out / (target + '-precheck.json'), precheck(path))
        overlays[target] = path
    cp = out / 'formal-candidate-review.json'
    write(cp, {'schema_version': 1, 'task_key': key, 'phase4_identity': ident, 'qualified_overlay': ref(overlays['audit']), 'checker_version': 'phase4-custom-finite-rejection-v1', 'code_refs': codes, 'records': fresh['candidate_scores'], 'candidate_pass': False, 'model_requests': 0})
    candidate_evaluation(cp, ident, rebuilt, qualified_overlay=io.read_json(overlays['audit']))
    h = {'task_key': key, 'native': ref(overlays['native']), 'audit': ref(overlays['audit']), 'candidate_review': ref(cp), 'reconciliation': rebuilt['reconciliation'], 'eligible_for_sampling': False, 'model_requests': 0, 'stage_by_root_only': True}
    write(out / 'handoff.json', h)
    print(h)
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--task', required=True)
    a = p.parse_args()
    main(a.task)

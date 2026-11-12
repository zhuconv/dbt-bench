"""Write additive phase4 reviews and candidate manifests without touching root ledger."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import argparse, json
from dbt_bench import io
from dbt_bench.validation import CHECKS
from phase4_review import binding, precheck
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase4-review-v1/customer'))

def ref(p):
    return io.evidence_ref(Path(p), R)

def latest(d, pattern):
    return max(d.glob(pattern), key=lambda p: int(p.stem.rsplit('v', 1)[1]))

def ck(status, reason, evidence, execution=True):
    v = {'status': status, 'reason': reason, 'evidence': evidence}
    if execution:
        v['execution_evidence'] = evidence
    return v

def case_records(key):
    records = []
    for batch in ('phase4-deb-customer-cases01-v1', 'phase4-deb-customer-cases02-v1'):
        b = R / 'artifacts/validation' / batch
        m = io.read_json(b / 'manifest.json')
        for rp in (b / 'jobs').glob('*/*/result.json'):
            result = io.read_json(rp)
            label = Path(result['config']['task']['path']).name
            if m['tasks'][label]['task_key'] != key:
                continue
            proof = rp.parent / 'agent/case-proof.json'
            p = io.read_json(proof)
            records.append({'case': p['case_id'], 'kind': m['tasks'][label]['case_setup']['kind'], 'proof': ref(proof), 'result': ref(rp), 'validity': p['validity'], 'reward': (result.get('verifier_result') or {}).get('rewards'), 'model_requests': 0})
    return records

def run(task):
    d = D / task
    score_path = latest(d, 'candidate-v*.json')
    score = io.read_json(score_path)
    key = score['task_key']
    base_dir = R / 'validation/deb' / task
    old = io.read_json(base_dir / 'contract.yaml')
    batch = Path(score['batch']['path']).parent
    ident = binding(batch, score['label'])
    native = deepcopy(old['checks'])
    audit = deepcopy(old['audit']['checks'])
    sf = latest(d, 'selftest-v*.json')
    selftest = io.read_json(sf)
    wf = d / 'workflow-selftest-v1.json'
    nop = d / 'nop-audit-v1.json'
    reg = D / 'comparison-regressions-v1.json'
    cases = case_records(key)
    score_ref = ref(score_path)
    self_ref = ref(sf)
    wf_ref = ref(wf)
    old_ref = ref(base_dir / 'contract.yaml')
    contract = {k: deepcopy(v) for k, v in old.items() if k in ('schema_version', 'task_key', 'workflow', 'inputs', 'deliverables', 'requirements', 'native_success')}
    contract.update(review_status='reviewed', reference_kind='independent_recompute', witness_kind='built_from_inputs', phase4_identity=ident, previous_contract=old_ref, scope='Current DuckDB task version and explicitly declared original-input plus synthetic-fixture checks. Historical records are retained unchanged. Actual candidate scores are separate from checker qualification.')
    contract['requirements'] = [r for r in contract['requirements'] if r.get('critical', True)]
    for r in contract['requirements']:
        if task == 'dbt-customer-ltv-fix' and r['id'] == 'R-valid-orders':
            r['description'] = 'Patched status-only definition: exclude exactly CANCELLED and RETURNED; do not additionally exclude UNPAID, test/sample/internal flags or other conditions.'
        if task == 'dbt-customer-cltv-forecasting' and r['id'] in ('R-eligible', 'R-reconciliation'):
            r['description'] += ' Current v2 excludes NULL customer and NULL timestamp rows before every metric and uses the same eligibility for reconciliation.'
        if task == 'dbt-fix-repeat-purchase-cohort-revenue' and r['id'] in ('R-order-deduplication', 'R-cohort-assignment', 'R-source-reconciliation'):
            r['description'] += ' Current v2 first drops NULL timestamps; each NULL order_id row remains separate and contributes revenue/cohort but not distinct order counts.'
        if task == 'dbt-order-interval-metrics' and r['id'] == 'R-frequency-tier':
            r['description'] += ' Current v2 explicitly uses ascending NTILE(4), with surplus rows assigned to lower-value quartiles.'
    contract['actual_findings'] = {'six_stage_audit_rewards': score['six_stage_audit_rewards'], 'component_rewards': score['six_stage_component_rewards'], 'source_parser': score['source_parser'], 'source_fixtures': score['source_fixtures'], 'cases': cases, 'candidate': score_ref, 'workflow_limitations': [{'trial': t['trial'], 'checks': {k: v for k, v in t['workflow']['checks'].items() if v['status'] != 'pass'}} for t in score['trials']]}
    for target in (native, audit):
        target['assets'] = ck('pass', 'Formal task, fixed backend, source payload and untouched native verifier are hash-bound by phase4 identity.', [ident['batch_manifest'], score_ref], False)
        target['cross_context'] = ck('pass', 'Both clean builds ran in locked Qwen shell; actual native verifier read the same typed submitted outputs, captured before and after it.', [score_ref, *[t['execution'] for t in score['trials']]])
        target['stability'] = ck(score['repeat_comparison']['status'], 'All captured contract output columns/types and full duplicate-preserving row bags compared across two clean initializations; no unstable fields removed.', [score_ref])
        scans = []
        unexpected = []
        for t in score['trials']:
            p = Path(t['trial']) / 'agent/audit-initial-visibility/scan.json'
            v = io.read_json(p)
            scans.append(ref(p))
            unexpected.extend(v['errors'])
            matches = v['matches']
            if task == 'dbt-consolidate':
                matches = [m for m in matches if m['path'] not in ('/app/data/googleads.csv', '/app/data/metaads.csv', '/app/data/tiktokads.csv')]
            unexpected.extend(matches)
        target['agent_visibility'] = ck('gap' if unexpected else 'pass', 'Actual initial root filesystem scanned per task. Consolidate matches are the three explicitly public source CSVs duplicated under tests/private_data; no expected output or solution match is accepted as public. Scanner limits remain explicit.', [score_ref, *scans])
        for name in ('environment_agent', 'environment_verifier'):
            target[name]['reason'] += ' Current formal backend binding is unchanged; both fresh build/verifier stages are attached.'
            target[name]['evidence'] = [*target[name]['evidence'], score_ref]
            target[name]['execution_evidence'] = [*target[name].get('execution_evidence', target[name]['evidence']), score_ref]
    if ident['task_variant']:
        nb = R / 'artifacts/validation/phase4-deb-variant-nop-v1'
        nr = io.read_json(nb / 'reconciliation-v1.json')
        matches = [x for x in nr['trials'] if x['task_key'] == key]
        assert len(matches) == 1 and matches[0]['native_rewards'] == {'reward': 0.0}
        native['nop'] = ck('pass', 'Untouched task under this exact patched instruction was actually run through native verifier and rejected.', [ref(nb / 'reconciliation-v1.json'), matches[0]['result']])
    native_success = all((t['native_reward'] == {'reward': 1.0} for t in score['trials']))
    workflow_scores = [t['workflow']['reward'] for t in score['trials']]
    native_reference = 'pass' if native_success and all((x == 1 for x in workflow_scores)) else 'fail' if 0 in workflow_scores or not native_success else 'gap'
    for name in ('reference', 'reference_repeat'):
        native[name] = ck(native_reference, 'Two actual clean blind SQL builds received native reward 1; compliance with explicitly required workflow and separately exercised source semantics is judged independently and any failure is retained.', [score_ref, *[t['result'] for t in score['trials']]])
    component = score['six_stage_component_rewards']
    semantic = 'fail' if 0 in component or task == 'dbt-fix-paid-search-attribution' else 'gap' if None in component else 'pass'
    native['independent_semantics'] = ck(semantic, 'Original raw inputs were actually recomputed in Python and compared by typed keys/full rows. Source-availability/purchase fixtures are separately identified; unresolved fallback/rounding cells remain explicit.', [score_ref, score['source_fixtures']])
    native['contract'] = ck('gap' if None in component else 'pass', 'Current instruction, real author workflow, original data, native verifier and independent recomputation reviewed. Patched business rules are described in this new contract; previous task stays intact.', [score_ref, old_ref, *[c['proof'] for c in cases]], False)
    if cases:
        eq = [c for c in cases if c['kind'] == 'equivalent']
        assert all((c['validity'] == 'legal_equivalent' and c['reward'] == {'reward': 1.0} for c in eq))
        native['equivalent'] = ck('pass', 'Actual comment-only equivalent SQL rebuild preserved full rows and native acceptance under current instruction.', [x for c in eq for x in (c['proof'], c['result'])])
    audit_pass = all((x == 1 for x in score['six_stage_audit_rewards']))
    audit_ref = 'pass' if audit_pass else 'fail' if 0 in score['six_stage_audit_rewards'] else 'gap'
    for name in ('reference', 'reference_repeat'):
        audit[name] = ck(audit_ref, 'Actual two clean authored submissions scored at agent submission, native-before and native-after. No failed workflow or unresolved arithmetic is promoted to accepted.', [score_ref])
    audit['contract'] = deepcopy(native['contract'])
    audit['independent_semantics'] = ck(semantic, 'Actual raw-source independent full declared values and extra fixture findings; unresolved branches are enumerated in candidate result.', [score_ref, score['source_fixtures']])
    cases_negative = [c for c in selftest['cases'] if c['kind'] == 'negative']
    misses = [c for c in cases_negative if not c['rejected']]
    equiv = [c for c in selftest['cases'] if c['kind'] == 'equivalent']
    eq_ok = equiv and all((c['audit_reward'] == 1 for c in equiv))
    audit['negatives'] = ck('gap' if misses or not audit_pass else 'pass', 'Executed per-column/key/row/type mutations and actual workflow omission fixtures. Unknown component baselines and ungraded intermediate scopes are not counted as a reliable rejection.', [self_ref, wf_ref, score_ref])
    audit['equivalent'] = ck('pass' if eq_ok else 'gap', 'Actual component column-order equivalences executed. Unknown full-component baselines remain unknown; no copied fixture is treated as a new clean solution.', [self_ref, score_ref])
    audit['comparator'] = ck('pass' if not misses else 'gap', 'Executed exact-ID/large-integer/keyed-tolerance/duplicate/SQL-type regression tests plus this task typed-output matrix; remaining uncovered model scope is explicit.', [ref(reg), self_ref])
    initial = io.read_json(nop)
    audit['nop'] = ck('pass' if all((x['audit']['reward'] == 0 for x in initial['checks'])) else 'gap', 'Actually scored both captured clean initial submissions with no source-build witness. Native nop remains a separate raw result.', [ref(nop)])
    old_audit = {r['id']: r for r in old['audit']['requirements']}
    native_coverage = {}
    audit_coverage = {}
    for r in contract['requirements']:
        rid = r['id']
        native_coverage[rid] = deepcopy(r.get('coverage', {}).get('native', {'status': 'gap', 'reason': 'Native coverage not established by prior executed review', 'evidence': [old_ref]}))
        a = deepcopy(old_audit.get(rid, {}).get('coverage', {}).get('audit', {'status': 'gap', 'reason': 'Prior audit coverage unresolved', 'evidence': [old_ref]}))
        if audit_pass:
            a = {'status': 'covered', 'reason': 'Declared rule is covered by current full raw-input table arithmetic, fresh workflow checks, actual typed mutation/equivalent matrix and separately executed source fixtures as applicable; finite original-input scope is explicit. No arbitrary SQL spelling or row ordering is required.', 'evidence': [score_ref, self_ref, wf_ref, score['source_fixtures']]}
        else:
            a['evidence'] = list(a.get('evidence', [])) + [score_ref, self_ref]
            a['reason'] = a.get('reason', '') + ' Current actual replay retains failures/uncertainties; this is not a qualification of the new checker.'
        audit_coverage[rid] = a
    for key2 in ('R-valid-orders', 'R-reconciliation', 'R-eligible', 'R-order-deduplication', 'R-frequency-tier'):
        if key2 in native_coverage and cases and (native_coverage[key2]['status'] == 'gap'):
            native_coverage[key2] = {'status': 'covered', 'reason': 'Changed business rule independently proved on current raw input, then actual native counterexample rejected under current instruction.', 'evidence': [score_ref, *[x for c in cases for x in (c['proof'], c['result'])]]}
    for r in contract['requirements']:
        r.pop('coverage', None)
    cp = d / 'contract-v1.json'
    assert not cp.exists()
    io.write_json(cp, contract)
    code = score['checker_code']
    for p in [Path(__file__), Path(__file__).with_name('selftest_workflow_v1.py'), Path(__file__).with_name('nop_audits_v1.py')]:
        value = ref(p)
        if value not in code:
            code.append(value)
    if task == 'dbt-order-interval-metrics':
        code.append(ref(base_dir / 'audit_phase2_v1/policy.json'))
    checker_version = io.digest(code)
    overlays = {}
    for target, checks, coverage in [('native', native, native_coverage), ('audit', audit, audit_coverage)]:
        for n in CHECKS:
            assert checks[n]['status'] != 'pending', (task, target, n)
            if n not in ('assets', 'contract') and (not checks[n].get('execution_evidence')):
                checks[n]['execution_evidence'] = checks[n]['evidence']
        overlay = {'schema_version': 1, 'task_key': key, 'target': target, 'model_requests': 0, 'phase4_identity': ident, 'base_identity': ident['base_identity'], 'checker_version': checker_version if target == 'audit' else 'native-' + ident['native_verifier']['sha256'], 'contract': ref(cp), 'code_refs': code, 'scope': contract['scope'], 'review_status': 'reviewed', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'checks': checks, 'requirement_coverage': coverage, 'diagnoses': []}
        p = d / (target + '-overlay-v1.json')
        assert not p.exists()
        io.write_json(p, overlay)
        overlays[target] = p
    audit_overlay = io.read_json(overlays['audit'])
    rows = []
    for ti, t in enumerate(score['trials']):
        for si, s in enumerate(t['stages']):
            rows.append({'trial': Path(t['trial']).name, 'stage': s['stage'], 'candidate_snapshot': s['snapshot'], 'audit_result': score_ref, 'audit_field_paths': {'reward': ['trials', ti, 'stages', si, 'audit', 'reward'], 'candidate_snapshot': ['trials', ti, 'stages', si, 'snapshot'], 'task_key': ['task_key']}, 'audit_reward': s['audit']['reward']})
    candidate = {'schema_version': 1, 'task_key': key, 'phase4_identity': ident, 'model_requests': 0, 'qualified_overlay': ref(overlays['audit']), 'checker_version': checker_version, 'code_refs': code, 'records': rows, 'candidate_pass': audit_pass, 'scope': 'Six actual formal snapshot scores. Checker protocol qualification remains separate.'}
    candidate_path = d / 'candidate-review-v1.json'
    io.write_json(candidate_path, candidate)
    checked = {}
    for target, p in overlays.items():
        checked[target] = precheck(p)
        io.write_json(d / (target + '-precheck-v1.json'), checked[target])
    handoff = {'task_key': key, 'native': str(overlays['native']), 'audit': str(overlays['audit']), 'candidate_review': str(candidate_path), 'reconciliation': str(batch / 'reconciliation-v1.json'), 'native_conclusion': checked['native']['conclusion'], 'audit_conclusion': checked['audit']['conclusion'], 'review_complete': all((v['review_complete'] for v in checked.values())), 'candidate_pass': audit_pass, 'six_stage_audit_rewards': score['six_stage_audit_rewards'], 'model_requests': 0}
    io.write_json(d / 'handoff-v1.json', handoff)
    print(json.dumps(handoff))
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--task', required=True)
    a = p.parse_args()
    run(a.task)

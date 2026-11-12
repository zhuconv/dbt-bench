"""Seal executed phase4 reviews without modifying historical records or pools."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import argparse, json
from dbt_bench import io
from dbt_bench.validation import CHECKS
from phase4_review import binding, precheck
from phase4_publish import reproduction, candidate_evaluation
from phase4_customer4_review_v3 import ROOT, ref, write, verify_trial, evaluate, outputs, policy, workflow
from phase3_capture import Capture
B = Path(_release_expand('${ARTIFACT_ROOT}/validation'))
P = B / 'phase4-review-v1/pipeline'

def unique(refs):
    return list({str((ROOT / r['path']).resolve()): r for r in refs}.values())

def checked(reference):
    if not io.evidence_ok(reference, ROOT):
        raise ValueError('Evidence changed: ' + str(reference))
    return io.read_json(ROOT / reference['path'])

def case_records(task, batch):
    records = []
    for rp in (batch / 'jobs').rglob('result.json'):
        cfg = rp.with_name('config.json')
        if not cfg.exists():
            continue
        label = Path(io.read_json(cfg).get('task', {}).get('path', '')).name
        entry = io.read_json(batch / 'manifest.json')['tasks'].get(label, {})
        if entry.get('task_key') != 'deb__' + task:
            continue
        proof = rp.parent / 'agent/case-proof.json'
        if not proof.exists():
            raise ValueError('Actual case proof missing')
        value = io.read_json(proof)
        result = io.read_json(rp)
        if value['validity'] not in ('legal_equivalent', 'business_error') or value['tested_checker_invoked_to_define_validity'] is not False:
            raise ValueError('Invalid/circular case')
        records.append({'trial': rp.parent, 'label': label, 'kind': entry['case_setup']['kind'], 'proof': ref(proof), 'result': ref(rp), 'native_rewards': result['verifier_result']['rewards'], 'exception': result['exception_info'], 'case_manifest': ref(batch / 'manifest.json')})
    return records

def seal(task):
    if task.endswith('tiering'):
        raise ValueError('Tiering requires the separately scoped noncandidate review')
    key = 'deb__' + task
    folder = P / 'reviews' / task
    out = folder / 'sealed-v1'
    if out.exists():
        raise ValueError('Already sealed')
    freshref = ref(folder / 'fresh-v3/fresh-review.json')
    fresh = checked(freshref)
    fixturesref = ref(folder / 'fixtures-v3/fixture-review.json')
    fixtures = checked(fixturesref)
    if fresh['six_stage_rewards'] != [1] * 6 or not fresh['repeat_equal'] or (not fresh['all_context_values_equal']) or (not fixtures['passed']):
        raise ValueError('Protocol executions failed')
    batch = (ROOT / fresh['batch']['path']).parent
    ident = binding(batch, fresh['label'])
    base = checked(ident['base_record'])
    original = io.read_json(ROOT / 'validation/deb' / task / 'contract.yaml')
    rebuilt = reproduction(batch / 'reconciliation-v1.json', ident)
    if not rebuilt['native1_both_clean_builds']:
        raise ValueError('Formal native reproduction failed')
    trial = batch / 'jobs' / batch.name / fresh['records'][0]['trial']
    v = verify_trial(batch, trial)
    casebatch = B / ('phase4-deb-warning-cases-v1' if task.endswith('warning') else 'phase4-deb-cohorts-rfm2-equivalent-v1')
    cases = case_records(task, casebatch)
    if not any((r['kind'] == 'equivalent' and r['native_rewards'] == {'reward': 1.0} and (not r['exception']) for r in cases)):
        raise ValueError('Actual legal equivalent not accepted')
    actual = []
    for record in cases:
        z = dict(v)
        z['capture'] = Capture(record['trial'], ROOT)
        wf = workflow(v, policy(task))
        scores = []
        for stage in ('agent-submission', 'verifier-before', 'verifier-after'):
            sr, o = outputs(z, stage)
            answer, facts = evaluate(task, z, o, wf)
            ar = write(out / ('case-' + record['trial'].name + '-' + stage + '.json'), dict(answer, task_key=key, candidate_snapshot=sr))
            scores.append({'stage': stage, 'snapshot': sr, 'audit': ar, 'reward': answer['reward']})
        want = 1 if record['kind'] == 'equivalent' else 0
        if any((s['reward'] != want for s in scores)):
            raise ValueError('Actual case audit disagrees with independently established validity')
        actual.append({**record, 'trial': str(record['trial']), 'audit_stages': scores})
    caseref = write(out / 'actual-cases.json', {'task_key': key, 'records': actual, 'model_requests': 0, 'scope': 'Each actual native source case also audited on all three captured submissions; raw hashes are checked against the independently recomputed formal input.'})
    requirements = []
    nativecoverage = {}
    auditcoverage = {}
    fby = {c['requirement']: c for c in fixtures['cases']}
    for r in original['audit']['requirements']:
        req = {k: deepcopy(r[k]) for k in ('id', 'description', 'critical') if k in r}
        requirements.append(req)
        nc = deepcopy(r['coverage']['native'])
        for e in nc['evidence']:
            if not io.evidence_ok(e, ROOT):
                raise ValueError('Historical native case evidence changed')
        nc['reason'] += ' Retained unchanged-rule case only: current task data/image/native-verifier identity equals the old record; phase4 reference legitimacy is supplied separately by fresh blind builds.'
        nativecoverage[r['id']] = nc
        case = fby[r['id']]
        auditcoverage[r['id']] = {'status': 'covered', 'reason': 'One independent raw-input or explicitly required workflow violation rejected by the actual new checker. This is finite grouped-rule coverage, not exhaustive SQL equivalence proof.', 'evidence': [case['independent_proof'], case['audit'], fixturesref], 'independent_proof': case['independent_proof'], 'checker_evidence': case['audit'], 'case_validity': 'business_error'}
    if task.endswith('warning'):
        requirements.append({'id': 'R-null-input-filter', 'critical': True, 'description': 'Patched instruction excludes NULL customer IDs and NULL order timestamps before as-of and all customer metrics; NULL-date-only customers produce no row.'})
        neg = next((r for r in actual if r['kind'] == 'negative'))
        proof = checked(neg['proof'])
        if not proof['after']['errors'] or neg['native_rewards'] != {'reward': 0.0}:
            raise ValueError('New NULL case incomplete')
        nativecoverage['R-null-input-filter'] = {'status': 'covered', 'reason': 'Actual source filter removal builds successfully, independently violates raw-input customer/revenue sets and is rejected by unchanged native verifier.', 'evidence': [caseref, neg['proof'], neg['result']], 'independent_proof': neg['proof'], 'checker_evidence': neg['result'], 'case_validity': 'business_error'}
        case = fby['R-null-input-filter']
        auditcoverage['R-null-input-filter'] = {'status': 'covered', 'reason': 'Actual NULL-filter source case and output identity counterexample are both rejected by the new independent audit.', 'evidence': [caseref, case['independent_proof'], case['audit']], 'independent_proof': neg['proof'], 'checker_evidence': neg['audit_stages'][0]['audit'], 'case_validity': 'business_error'}
    if task.endswith('-2'):
        for r in requirements:
            if r['id'] == 'R-quarter-velocity':
                r['description'] = '2024 quarter counts and velocity; reviewed current blind interpretation applies the listed trend predicates in their stated order (decelerating precedes churning where both hold). Historical ambiguity record is retained.'
    policytext = {'population': fresh['records'][0]['facts'], 'precision': 'Exact identities/integers/dates/categories and declared SQL types. Decimal input arithmetic; explicitly rounded columns accept nearest values, either adjacent value only on exact halfway ties. Float representation allowance at most 8 ULP with 1e-12 floor, never applied to identifiers. Downstream displayed-rounded values accepted only after separate raw-input validation.', 'workflow': 'Actual locked Qwen backend read before installation, project-local resolved profile, fresh successful named model executions and source/manifest correspondence. Existing workflow obligations have actual negative provenance fixtures. No source-style whitelist.', 'scope': 'Current fixed input and disclosed blind interpretation; all declared output values checked. Not a claim of unique prose meaning for all future data, complete model request capture, or mathematical verifier completeness.'}
    contractref = write(out / 'contract.json', {'schema_version': 1, 'task_key': key, 'review_status': 'reviewed', 'workflow': original['workflow'], 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'requirements': requirements, 'inputs': original['inputs'], 'deliverables': original['deliverables'], 'source_contract': ref(ROOT / 'validation/deb' / task / 'contract.yaml'), 'phase4_interpretation': policytext, 'instruction': ref(batch / 'tasks' / fresh['label'] / 'instruction.md'), 'formal_reproduction': rebuilt['reconciliation'], 'model_requests': 0})
    bindingref = write(out / 'retained-native-binding.json', {'task_key': key, 'phase4_identity': ident, 'base_record': ident['base_record'], 'old_native_case_coverage': nativecoverage, 'reason': 'Current mechanical batch backend_binding equals the original review identity; native test code is byte-identical. Unchanged-rule proof cases are retained, not rerun or replaced. Newly added instruction rule has its own actual source case. Prior oracle/reference failures remain historical and are not recertified.', 'model_requests': 0})
    selftest = ref(P / 'comparator-selftest-v3/result.json')
    s = checked(selftest)
    if s['returncode'] != 0:
        raise ValueError('Comparator SIF regression failed')
    evidence = {'assets': [ident['batch_manifest'], ident['native_verifier'], contractref, *ident['submission_sources']], 'contract': [contractref, freshref, caseref], 'reference': [freshref, rebuilt['reconciliation']], 'reference_repeat': [freshref, rebuilt['reconciliation']], 'stability': [freshref], 'environment_agent': [freshref], 'environment_verifier': [freshref], 'cross_context': [freshref], 'agent_visibility': [freshref], 'independent_semantics': [freshref, fixturesref, contractref], 'negatives': [fixturesref, caseref, bindingref], 'equivalent': [caseref, fixturesref], 'comparator': [selftest, fixturesref], 'nop': [fixturesref, fixtures['initial_audit']]}
    reasons = {'assets': 'Locked current task, unchanged native verifier and explicit source payload/data/image bindings verified.', 'contract': 'Individually reviewed all critical rule groups, exact output interface and disclosed fixed-input interpretation; original ambiguity records retained.', 'reference': 'Actual blind dbt source built through locked Qwen shell from clean inputs, native1 and independent audit1 at all three stages. No gold copied or consulted by expected arithmetic.', 'reference_repeat': 'Two distinct clean Harbor environments each built the blind witness and obtained native1 plus all three actual audit1.', 'stability': 'Complete captured columns, SQL types and typed row multisets identical across both clean builds and all native boundaries; no columns dropped.', 'environment_agent': 'Actual locked Qwen shell context records Python/dbt/DuckDB paths and successful database access; declared project builds succeed.', 'environment_verifier': 'Observers executed around real unchanged test.sh, native verifier reads submitted output and returns1; typed values remain unchanged.', 'cross_context': 'Actual shell-service build consumed by original verifier, with complete typed snapshots equal at submission/before/after and audit1 at each boundary.', 'agent_visibility': 'Actual clean Qwen shell scans find no declared hidden material or host paths; scope, exclusions and size limit remain in raw scan records, not a general noninterference claim.', 'independent_semantics': 'Executed Python/Decimal recomputation from captured initial source data validates every declared mart column by key, with exact multiplicity and explicit finite rounding policies.', 'negatives': 'Every critical rule group has one independently established rejected output/provenance fixture; newly added NULL input rule also has an actual successful SQL source mutation before native checking.', 'equivalent': 'Actual comment-only SQL control preserves all declared typed output bags, native accepts and audit accepts all stages; in-memory row/column permutation and extra-column controls also accepted.', 'comparator': 'Executed exact task-SIF 32 independent regressions plus task-specific structural fixtures: ID precision, row association, duplicates/missing/NULL/type/column handling and bounded numeric rounding.', 'nop': 'New audit actually rejects untouched initial captured outputs; no author payload existed at that point.'}
    checks = {c: {'status': 'pass', 'reason': reasons[c], 'evidence': unique(evidence[c]), 'execution_evidence': unique(evidence[c])} for c in CHECKS}
    codes = unique(fresh['code_refs'] + [ref(ROOT / 'scripts/phase4_customer4_fixtures_v3.py'), ref(ROOT / 'scripts/phase4_customer4_seal.py')])
    overlays = {}
    for target in ('native', 'audit'):
        cs = deepcopy(checks)
        if target == 'native':
            for name in ('negatives', 'comparator'):
                cs[name] = {'status': 'fail', 'reason': 'Current native verifier is unchanged and retains independently proved critical misses; original scores and old records remain exact.', 'evidence': [bindingref, ident['base_record']], 'execution_evidence': [bindingref, ident['base_record']]}
            cs['nop'] = deepcopy(base['native']['checks']['nop'])
            if task.endswith('warning'):
                nb = B / 'phase4-deb-variant-nop-v1'
                nops = []
                for rp in (nb / 'jobs').rglob('result.json'):
                    cfg = rp.with_name('config.json')
                    if cfg.exists() and task in io.read_json(cfg).get('task', {}).get('path', ''):
                        x = io.read_json(rp)
                        if x['verifier_result']['rewards'] != {'reward': 0.0}:
                            raise ValueError('Current variant nop not zero')
                        nops.append(ref(rp))
                if len(nops) != 1:
                    raise ValueError('Current variant nop missing/duplicate')
                cs['nop'] = {'status': 'pass', 'reason': 'Current instruction variant untouched initialization actually gives native0; no blind payload installed.', 'evidence': nops, 'execution_evidence': nops}
        overlay = {'schema_version': 1, 'task_key': key, 'target': target, 'phase4_identity': ident, 'base_identity': ident['base_identity'], 'checker_version': ident['native_verifier']['sha256'] if target == 'native' else 'phase4-customer4-fixed-input-v2', 'contract': contractref, 'review_status': 'reviewed', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'checks': cs, 'requirement_coverage': nativecoverage if target == 'native' else auditcoverage, 'code_refs': codes, 'model_requests': 0, 'scope': 'New phase4 blind witness and explicit fixed-input semantic audit; original task/native checker/reward/history preserved.'}
        path = out / (target + '-overlay.json')
        write(path, overlay)
        overlays[target] = path
        result = precheck(path)
        write(out / (target + '-precheck.json'), result)
    candidate = {'schema_version': 1, 'task_key': key, 'phase4_identity': ident, 'qualified_overlay': ref(overlays['audit']), 'checker_version': 'phase4-customer4-fixed-input-v2', 'code_refs': codes, 'records': fresh['candidate_scores'], 'candidate_pass': True, 'model_requests': 0}
    cp = out / 'formal-candidate-review.json'
    write(cp, candidate)
    candidate_evaluation(cp, ident, rebuilt, qualified_overlay=io.read_json(overlays['audit']))
    handoff = {'task_key': key, 'native': ref(overlays['native']), 'audit': ref(overlays['audit']), 'candidate_review': ref(cp), 'reconciliation': rebuilt['reconciliation'], 'model_requests': 0, 'stage_by_root_only': True}
    write(out / 'handoff.json', handoff)
    return handoff
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--task', required=True)
    a = p.parse_args()
    print(seal(a.task))

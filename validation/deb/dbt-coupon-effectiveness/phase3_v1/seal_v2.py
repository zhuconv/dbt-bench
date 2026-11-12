"""Seal already executed coupon evidence; never run a model or alter a prior artifact."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import json
import sys
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from phase3_review import binding, precheck
from phase3_publish import reproduction, candidate_evaluation
from review_tools import cached_hashes
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/deb'))
B = D.parent.parent / 'phase3-deb-coupon-capture-v3'
KEY = 'deb__dbt-coupon-effectiveness'
VERSION = 'phase3-coupon-fixed-input-v1'

def ref(path):
    return io.evidence_ref(Path(path), ROOT)

def load(reference):
    assert io.evidence_ok(reference, ROOT), reference
    return io.read_json(ROOT / reference['path'])

def write(path, value):
    assert not path.exists(), path
    io.write_json(path, value)
    return ref(path)

def unique(refs):
    return list({str((ROOT / r['path']).resolve()): r for r in refs}.values())

def verify_nested(value):
    if isinstance(value, dict):
        if set(value) == {'path', 'sha256'}:
            assert io.evidence_ok(value, ROOT), value
        else:
            for v in value.values():
                verify_nested(v)
    elif isinstance(value, list):
        for v in value:
            verify_nested(v)

def main():
    freshref = ref(D / 'coupon-fresh-v2/review.json')
    casesref = ref(D / 'coupon-cases-v1/review.json')
    fresh, cases = (load(freshref), load(casesref))
    assert freshref['sha256'] == 'd4a196b6754e0bbd2d8416b88cf914ba6c6917ad1d4f9ec8bfe65017c2bf0be6'
    verify_nested(fresh)
    verify_nested(cases)
    assert len(fresh['records']) == 2
    assert len({r['canonical_value_digest'] for r in fresh['records']}) == 1
    assert cases['critical_rules'] == cases['target_detected'] == len(cases['records']) == 48
    assert all((r['target_detected'] and r['target_check']['status'] == 'fail' for r in cases['records']))
    hostref, sifref = [ref(D / ('coupon-selftest-' + side + '-v2.json')) for side in ('host', 'sif')]
    for r in (hostref, sifref):
        s = load(r)
        assert s['passed'] == 28 and s['failed'] == 0
        verify_nested(s)
    sifexec = ref(D / 'coupon-selftest-sif-execution-v2.json')
    assert load(sifexec)['returncode'] == 0
    label = fresh['records'][0]['label']
    ident = binding(B, label)
    rebuilt = reproduction(B / 'reconciliation-v1.json', ident)
    assert rebuilt['native1_both_clean_builds']
    original = io.read_json(HERE.parent / 'contract.yaml')
    base = load(ident['base_record'])
    assert base['native']['conclusion'] == 'weak'
    pilot = Path(_release_expand('${SCRATCH_ROOT}/claude-two-version-pilot/dbt-coupon-effectiveness'))
    decisions = ref(pilot / 'decisions.md')
    instruction = ref(B / 'tasks' / label / 'instruction.md')
    envrecords, envrefs, scores = ([], [], [])
    for r in fresh['records']:
        t = B / 'jobs' / B.name / r['trial']
        modelref = ref(t / 'agent/no-model.json')
        mode = load(modelref)
        assert mode['model_requests'] == 0 and mode['solution_uploaded'] is False
        assert mode['execution'] == 'Locked Qwen shell service, trusted clean reconstruction'
        visrefs = {n: ref(t / ('agent/audit-initial-visibility/' + n + '.json')) for n in ('facts', 'scan', 'structured')}
        facts, scan, structured = [load(visrefs[n]) for n in ('facts', 'scan', 'structured')]
        assert all(facts['declared_absent_paths'].values()) and (not any(facts['host_paths_visible'].values()))
        assert scan['errors'] == [] and scan['matches'] == [] and (structured == [])
        snapshots = []
        for part in ('agent/audit-initial', 'agent/audit-submission', 'verifier/mechanical-before', 'verifier/mechanical-after'):
            sr = ref(t / part / 'snapshot.json')
            snap = load(sr)
            c = snap['context']
            assert snap['task_key'] == KEY and c['duckdb_version'] == '1.2.2'
            assert c['python']['executable'] == '/usr/local/bin/python3'
            assert c['dbt']['version']['returncode'] == 0
            assert c['databases'] and all((x['rows'] == [[1]] and x['readable'] for x in c['databases']))
            snapshots.append({'snapshot': sr, 'stage': snap['stage'], 'context': c})
            envrefs.append(sr)
        initialspec = load(r['initial_nop']['spec'])
        assert load(r['initial_nop']['audit'])['reward'] == 0
        assert r['context_values_identical'] and all((x['complete_types_values_multiplicities_equal'] for x in r['input_contrasts']))
        for stage in r['stages']:
            spec = load(stage['spec'])
            audit = load(stage['audit'])
            assert audit['reward'] == stage['audit_reward'] == 1
            assert audit['input_refs']['candidate_snapshot'] == spec['candidate_snapshot']
            scores.append({'trial': r['trial'], 'stage': stage['stage'], 'spec': stage['spec'], 'audit_result': stage['audit'], 'candidate_snapshot': spec['candidate_snapshot'], 'audit_reward': audit['reward'], 'audit_field_paths': {'reward': ['reward'], 'candidate_snapshot': ['input_refs', 'candidate_snapshot'], 'task_key': ['task_key']}})
        envrefs.extend([modelref, *visrefs.values(), ref(t / 'agent/audit-initial-context-shell.json'), ref(t / 'agent/audit-initial-visibility-shell.json'), ref(t / 'agent/audit-rebuild/execution.json'), ref(t / 'agent/audit-rebuild-shell.json'), ref(t / 'verifier/test-stdout.txt')])
        envrecords.append({'trial': r['trial'], 'model_request_record': modelref, 'snapshots': snapshots, 'visibility': visrefs, 'visibility_scope': scan['scope'], 'visibility_exclusions': scan['excluded_roots'], 'raw_input_unchanged': r['input_contrasts'], 'submission_to_native_typed_values_identical': r['context_values_identical']})
    assert len(scores) == 6
    envref = write(D / 'coupon-environment-v1.json', {'task_key': KEY, 'records': envrecords, 'evidence': unique(envrefs), 'image_binding': ident['base_identity']['backend_binding'], 'model_requests': 0, 'native_rewards_modified': False, 'interpretation': 'Agent PATH includes installed Node for locked Qwen shell; verifier PATH omits Node. Both use /usr/local/bin/python3 (3.12), dbt-core 1.10.22, dbt-duckdb 1.10.0, DuckDB 1.2.2 and read the same actual outputs before/after native execution. Shell fields may be null in Python observer; actual shell-service transcript is separately bound. Profiles env in the observer is null because the build command exports it in its own shell; the actual execution and resolved profile are checked by workflow_v2. Visibility pass is only the recorded declared-hidden-file, host-path, readable-text and structured scan scope; no general information-flow proof.'})
    requirements = []
    case_by_id = {x['requirement']: x for x in cases['records']}
    for q in original['audit']['requirements']:
        if not q.get('critical'):
            continue
        rule = {k: deepcopy(q[k]) for k in ('id', 'critical', 'source', 'source_lines', 'source_quote') if k in q}
        rule['description'] = q.get('source_quote', q['description'])
        rule['prior_description'] = q['description']
        rule['review_scope'] = 'Current fixed staging inputs and declared blind interpretation; test fixtures represent output errors, not extra SQL benchmark executions.'
        rule['tested_case'] = case_by_id[q['id']]['target']
        if q['id'] == 'G-coupon-zero-redemption-policy':
            rule['description'] = 'Reviewed phase3 active-only promotion population; preserve zero-redemption master promotions in diagnostic input profile rather than invent undefined output metrics.'
            rule['review_scope'] = 'A disclosed phase3 interpretation backed by blind author decisions and formal native acceptance, not a claim that the original prose uniquely specifies zero-redemption behavior.'
        if q['id'] == 'G-coupon-rounding-stage':
            rule['description'] = 'Reviewed phase3 raw-total-then-round summary/cumulative arithmetic; exact nearest rounding ties retain both adjacent legal values.'
            rule['review_scope'] = 'A disclosed phase3 interpretation. Historical displayed-subtotal alternative and earlier audit failures are retained, not relabeled as invalid historical answers.'
        requirements.append(rule)
    policy = {'input_scope': 'All typed columns, rows, NULLs and multiplicities from task-declared stg_orders__orders, stg_pos__coupons, stg_pos__promotions, stg_pos__coupon_usage. Initial and final copies exactly equal; no gold or evaluator called by independent Python arithmetic.', 'workflow': 'Live echo DB_TYPE before installation; original model paths under /app/dbt_models_duckdb, retail_dw_master profile resolved against captured environment, selected-ancestor dbt run exactly as author build.txt. Required source leaves inspected from actual compiled AST and actual model/result manifest.', 'project_path_conflict': 'Instruction live-backend header says /app/dbt_models_duckdb and body says /app/dbt_transforms. Use actual initialized DuckDB project and blind decisions; preserve conflict and do not assert a filesystem alias.', 'normalization': 'Independent Python trims IDs, maps empty strings to NULL, resolves latest _loaded_at for non-NULL entity keys, retains distinct normalized NULL-ID usage tuples, normalizes type codes and uniquely resolvable one-letter forms, parses money/dates. This implements reviewed blind choices for current input, not a generic data-cleaning certificate.', 'population_and_tiers': 'Active-only promotions; raw subtotals then rounding; NTILE(5) revenue tier interpretation. Current actual population is 59 redemptions, 27 effectiveness, 16 summary, 58 monthly trend rows. Master promotions with no eligible redemptions stay diagnostic.', 'numeric': 'Exact identifiers, counts, dates and labels. Decimal arithmetic; monetary 2 places, percentages 1, ratio/averages 2. Exact mathematical nearest ties accept either adjacent rounded result. Classification labels must separately agree with displayed rounded metrics; no global tolerance.', 'interface': 'Three marts are tables, intermediate is view. Declared final columns/types required; extra columns and physical row/column ordering allowed. Composite keys preserve row association and multiplicity including explicit nullable grouping semantics. No fixed 17-column internal projection is invented.', 'ordering': 'Stated ORDER BY is checked using actual executed compiled SELECT output; physical table scan order is not semantic.', 'limits': 'Fixed-data and disclosed interpretation scope only. Does not prove all unseen inputs, unique meaning of underspecified prose, model-context fidelity, or general hidden-data noninterference.'}
    contractref = write(D / 'coupon-contract-v1.json', {'schema_version': 1, 'task_key': KEY, 'review_status': 'reviewed', 'workflow': 'complete_existing_dbt_coupon_models', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'requirements': requirements, 'inputs': deepcopy(original['inputs']), 'deliverables': deepcopy(original['deliverables']), 'phase3_interpretation': policy, 'source_contract': ref(HERE.parent / 'contract.yaml'), 'instruction': instruction, 'blind_decisions': decisions, 'formal_reproduction': rebuilt['reconciliation'], 'scope': 'Current phase3 blind build and explicit fixed-input audit; original native and phase2 contracts remain unchanged.', 'model_requests': 0})
    all_actual = unique([freshref, rebuilt['reconciliation'], envref] + fresh['evidence'])
    evidence = {'assets': [ident['batch_manifest'], instruction, ident['native_verifier'], *ident['submission_sources'], freshref], 'contract': [contractref, instruction, decisions, freshref], 'reference': all_actual, 'reference_repeat': all_actual, 'nop': [freshref] + [x['initial_nop'][k] for x in fresh['records'] for k in ('spec', 'audit')], 'stability': all_actual, 'environment_agent': [envref] + unique(envrefs), 'environment_verifier': [envref] + unique(envrefs), 'cross_context': all_actual, 'agent_visibility': [envref] + unique(envrefs), 'independent_semantics': [freshref, cases['independent_expected'], cases['original_input'], contractref] + [x['original_input'] for x in fresh['records']], 'negatives': [casesref, cases['retained_workflow_binding']] + [x[k] for x in cases['records'] for k in ('independent_proof', 'fixture', 'audit')], 'equivalent': [casesref] + [x['audit'] for x in cases['legal_controls']] + [hostref, sifref], 'comparator': [hostref, sifref, sifexec, ref(D / 'coupon-selftest-sif-v2.log'), ref(D / 'coupon-selftest-host-v1.json'), casesref]}
    reasons = {'assets': 'Pinned formal task, original verifier, author submission files and exact image binding verified; all actual raw captures and checker dependencies have hashes.', 'contract': 'Reviewed 48 critical rules, actual input/output columns, source workflow and declared legal representations. Disclosed current blind interpretation resolves active-only/raw-subtotal/NTILE choices within this scope; old ambiguity findings remain untouched.', 'reference': 'Blind dbt files rebuilt from clean declared inputs through locked Qwen shell with actual echo/profile/source workflow; original native1 and independent whole audit1 at all 3 stages in each trial.', 'reference_repeat': 'Two independent formal clean builds, each native1 and audit1 for submission, verifier-before and verifier-after. No copy-gold or full-project build claim.', 'nop': 'Audit actually rejects both untouched initial snapshots with reward0 because required outputs are absent; formal build follows independently.', 'stability': 'Complete declared output schemas and typed row multisets equal across two clean builds and all 3 stages; all four input relations are unchanged. Only ISO datetime space/T representation normalized; no column or value omitted.', 'environment_agent': 'Actual locked Qwen shell execution and initial/submission contexts show usable Python/dbt/DuckDB/profile/source paths and database reads; complete required build succeeds.', 'environment_verifier': 'Observers execute around the real unchanged test.sh path; verifier reads the actual submitted database, runs native build/tests and returns1. Actual before/after typed values match.', 'cross_context': 'Actual locked Qwen shell build transfers to native verifier on same snapshot: 6 stage audits1, unchanged complete input/output data and actual source/profile bytes. Agent Node PATH difference does not affect Python/dbt/DB compatibility.', 'agent_visibility': 'Both clean initial locked-shell scans find no declared hidden-file content, /solution or /tests, or host work/training/home path visibility. Exact scans retain exclusions, size limit and absent archive/semantic search scope.', 'independent_semantics': 'Executed independent Python/Decimal recomputation from all four actual initial staging relations checks 59 redemption rows and all declared 27/16/58 mart values under documented normalization/business interpretation, with key/value alignment. Does not call author SQL, gold or tested checker to derive expectations.', 'negatives': '48/48 targeted critical rules detected: 45 positive-based output/comparator fixtures plus 3 independently proved actual old workflow violations replayed at targeted components after full typed input identity binding. Validity established before audit; fixtures are not new benchmark runs.', 'equivalent': 'Actually executed physical row and column reorder fixtures both accepted. Host/SIF controls also accept legal extra columns with consistent capture schema and either exact mathematical nearest half-cent result, with downstream labels independently checked.', 'comparator': 'Host and exact task SIF each pass28 tests including integer and >2^53 IDs, keyed tolerance, row association, duplicate/missing rows, NULL, nonfinite, declared SQL types/materialization/order and provenance tamper rejection. Retained v1 failure was malformed extra-column fixture inventory, corrected only in v2 fixture; checker unchanged.'}
    checks = {name: {'status': 'pass', 'reason': reasons[name], 'evidence': unique(refs), 'execution_evidence': unique(refs)} for name, refs in evidence.items()}
    coverage = {x['requirement']: {'status': 'covered', 'reason': 'Independently established violation rejected by the targeted audit component within the declared fixed-input interpretation. ' + x['scope'], 'case_validity': x['case_validity'], 'independent_proof': x['independent_proof'], 'checker_evidence': x['audit'], 'evidence': [x['independent_proof'], x['fixture'], x['audit'], casesref]} for x in cases['records']}
    codepaths = [HERE / n for n in ('expected_v3.py', 'audit_v2.py', 'run_audit_v2.py', 'workflow_v2.py', 'source_inspection_v1.py', 'collect_v2.py', 'selftest_v2.py', 'seal_v2.py')]
    codepaths += [ROOT / 'scripts/audit_tables.py', ROOT / 'src/dbt_bench/comparison.py', ROOT / 'src/dbt_bench/io.py', D / 'replay_coupon_v2.py', D / 'coupon_cases_v1.py', D / 'parse_coupon_v1.py']
    overlay = {'schema_version': 1, 'task_key': KEY, 'target': 'audit', 'checker_version': VERSION, 'scope': 'Current frozen task/input/environment and reviewed blind active-only/raw-subtotal/NTILE interpretation. Independent complete declared output arithmetic, workflow and exact comparator with nearest rounding tie alternatives. Original native verifier/rewards/contracts unchanged.', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'review_status': 'reviewed', 'base_identity': ident['base_identity'], 'phase3_identity': ident, 'contract': contractref, 'code_refs': [ref(p) for p in codepaths], 'checks': checks, 'requirement_coverage': coverage, 'model_requests': 0, 'native_rewards_modified': False, 'diagnoses': []}
    overlaypath = HERE / 'audit-overlay-v1.json'
    overlayref = write(overlaypath, overlay)
    with cached_hashes():
        checked = precheck(overlaypath)
    checkref = write(D / 'coupon-audit-precheck-v1.json', checked)
    assert checked['conclusion'] == 'validated' and checked['check_counts'] == {'pass': 14}
    candidate = {'schema_version': 1, 'task_key': KEY, 'phase3_identity': ident, 'qualified_overlay': overlayref, 'checker_version': VERSION, 'code_refs': overlay['code_refs'], 'records': scores, 'candidate_pass': True, 'model_requests': 0, 'native_rewards_modified': False}
    candidatepath = D / 'coupon-formal-candidate-review-v1.json'
    candidateref = write(candidatepath, candidate)
    judged = candidate_evaluation(candidatepath, ident, rebuilt, qualified_overlay=overlay)
    judgedref = write(D / 'coupon-candidate-validation-v1.json', judged)
    assert judged['status'] == 'pass' and judged['scores'] == [1] * 6
    handoff = {'task_key': KEY, 'ready_for_root_review': True, 'phase3_identity': ident, 'native_overlay': None, 'audit_overlay': overlayref, 'overlay': overlayref, 'precheck': checkref, 'contract': contractref, 'review': freshref, 'cases': casesref, 'environment': envref, 'formal_candidate_review': candidateref, 'candidate_validation': judgedref, 'candidate_pass': True, 'formal_candidate_audit_reward': 1, 'native_conclusion': base['native']['conclusion'], 'conclusion': checked['conclusion'], 'critical_rules': 48, 'audit_target_negatives_detected': 48, 'equivalence_controls': 2, 'host_selftests': 28, 'sif_selftests': 28, 'native_rewards_unchanged': True, 'model_requests': 0, 'scope': overlay['scope'], 'limitations': policy['limits'], 'reconciliation': rebuilt['reconciliation']}
    finalref = write(D / 'coupon-handoff-v1.json', handoff)
    print(json.dumps({'handoff': finalref, 'precheck': checked['conclusion'], 'candidate': judged['status'], 'scores': judged['scores']}))
if __name__ == '__main__':
    main()

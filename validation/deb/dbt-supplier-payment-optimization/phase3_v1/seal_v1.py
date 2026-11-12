"""Seal the executed payment review; original task/native ledger stay untouched."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]
from dbt_bench import io
from phase3_review import binding, precheck
HERE = Path(__file__).parent
BASE = Path(_release_expand('${ARTIFACT_ROOT}/validation'))
D = BASE / 'phase3-review-v1/deb/root-three-v1'
B = BASE / 'phase3-deb-batch04-rebuild-v2'
KEY = 'deb__dbt-supplier-payment-optimization'

def ref(path):
    return io.evidence_ref(path, ROOT)

def save(path, value):
    if path.exists():
        raise ValueError('Keep reviews immutable')
    io.write_json(path, value)
    return ref(path)

def main():
    fresh_path = D / 'payment-fresh-v3/review.json'
    cases_path = D / 'payment-cases-v1/review.json'
    equivalent_path = D / 'payment-equivalent-v1/review.json'
    fresh, cases, equiv = [io.read_json(p) for p in (fresh_path, cases_path, equivalent_path)]
    assert len(fresh['records']) == 2
    assert all((r['initial']['reward'] == 0 and len(r['stages']) == 3 and all((s['reward'] == 1 and (not s['non_pass']) for s in r['stages'])) for r in fresh['records']))
    assert cases['target_detected'] == cases['target_count'] == 33
    assert len(equiv['records']) == 16 and all((r['reward'] == (0 if r['stage'] == 'initial' else 1) for r in equiv['records']))
    tests = [D / ('payment-selftest-' + name + '-v1.json') for name in ('host', 'sif')]
    assert all((io.read_json(p)['passed'] == 17 and io.read_json(p)['failed'] == 0 for p in tests))
    ident = binding(B, KEY + '--phase3-blind-v1')
    old = io.read_json(HERE.parent / 'contract.yaml')
    contexts, visibility, executions = ([], [], [])
    for row in fresh['records']:
        t = Path(row['trial'])
        result = io.read_json(t / 'result.json')
        assert result['verifier_result']['rewards'] == {'reward': 1.0} and result['exception_info'] is None
        assert io.read_json(t / 'agent/no-model.json')['model_requests'] == 0
        for part in ('agent/audit-initial', 'agent/audit-submission', 'verifier/mechanical-before', 'verifier/mechanical-after'):
            path = t / part / 'snapshot.json'
            j = io.read_json(path)
            ctx = j['context']
            assert j['task_key'] == KEY and ctx['duckdb_version'] == '1.2.2'
            assert ctx['dbt']['version']['returncode'] == 0 and all((d.get('rows') == [[1]] for d in ctx['databases']))
            contexts.append({'snapshot': ref(path), 'stage': j['stage'], 'context': ctx})
        v = t / 'agent/audit-initial-visibility'
        facts, scan = (io.read_json(v / 'facts.json'), io.read_json(v / 'scan.json'))
        assert all(facts['declared_absent_paths'].values()) and (not any(facts['host_paths_visible'].values()))
        assert not scan['errors'] and (not scan['matches'])
        visibility.append({'evidence': [ref(v / name) for name in ('facts.json', 'scan.json', 'structured.json', 'files.jsonl', 'mountinfo.txt')], 'scope': scan['scope'], 'excluded_roots': scan['excluded_roots']})
        executions += [ref(t / name) for name in ('result.json', 'agent/no-model.json', 'agent/locked-install.json', 'agent/audit-rebuild/execution.json')]
    environment_path = D / 'payment-environment-v1.json'
    environment = save(environment_path, {'task_key': KEY, 'contexts': contexts, 'visibility': visibility, 'executions': executions, 'scope': 'Actual Qwen shell and native test.sh observers. SIF fixture process warnings about default package copying do not certify initialization; actual Harbor trials do.'})
    requirements = [r for r in old['audit']['requirements'] if r.get('critical', True)]
    assert {r['id'] for r in requirements} == {r['rule_id'] for r in cases['records']}
    coverage = {}
    for row in cases['records']:
        assert row['detected_target'] and row['target_status'] == 'fail'
        coverage[row['rule_id']] = {'status': 'covered', 'reason': 'Actual prior erroneous submission reexecuted after exact typed current-input binding; named target fails independently of any background workflow result.', 'case_validity': 'business_error', 'independent_proof': row['independent_proof'], 'checker_evidence': row['audit'], 'target_check': row['target'], 'evidence': [ref(cases_path), row['independent_proof'], row['input_binding'], row['audit']]}
    contract_path = D / 'payment-contract-v1.json'
    contract = save(contract_path, {'schema_version': 1, 'task_key': KEY, 'review_status': 'reviewed', 'workflow': old['workflow'], 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'requirements': [{k: v for k, v in r.items() if k in ('id', 'critical', 'description', 'source', 'kind', 'mapped_business_rule_ids')} for r in requirements], 'original_contract': ref(HERE.parent / 'contract.yaml'), 'policy': ref(HERE / 'policy.json'), 'instruction': ref(ROOT / 'tasks' / KEY / 'instruction.md'), 'blind_decisions': ref(Path(_release_expand('${SCRATCH_ROOT}/claude-two-version-pilot/dbt-supplier-payment-optimization/decisions.md'))), 'scope': 'Current original raw inputs and all12x32 declared output fields. Status-selected future invoices retained; negative age contribution floored at0. Coherent raw/displayed concentration and exact mathematical rounding ties allowed. Original0-100constraint and independent blind interpretation/formal reconstruction support this explicit policy. Non-USD/orphan/multiple-invoice future data are not certified by current-data equality.'})
    checks = {}

    def check(name, reason, paths):
        checks[name] = {'status': 'pass', 'reason': reason, 'evidence': [ref(p) for p in paths], 'execution_evidence': [ref(p) for p in paths]}
    check('assets', 'Original image/task/verifier and installed blind sources bound by formal manifest and verified execution hashes.', [B / 'manifest.json', fresh_path, environment_path])
    check('contract', 'All33 critical rules mapped; policy choices stated from original instruction and blind decisions, then actually independently reproduced.', [contract_path, fresh_path, cases_path])
    for name in ('reference', 'reference_repeat'):
        check(name, 'Two real clean blind dbt builds pass native1 and all12x32 independently recomputed audit values/workflow; old EIO attempts are not witnesses.', [fresh_path, B / 'reconciliation-v1.json'])
    check('nop', 'Two actual untouched initial submissions return audit0; this does not invent additional native nop trials.', [fresh_path])
    check('stability', 'Complete typed output, schema and key/value multiset identical across both clean initializations and three evaluation stages.', [fresh_path])
    check('environment_agent', 'Actual locked Qwen tool context and read-only raw/build observers recorded; host and pinned image checker tests executed.', [environment_path, fresh_path, *tests])
    check('environment_verifier', 'Actual native test.sh context reads output made by Qwen shell; native before/after observations and native1 retained.', [environment_path, fresh_path, B / 'reconciliation-v1.json'])
    check('cross_context', 'Same complete submission accepted at agent output and native before/after by independent audit, with all current raw/staging values unchanged.', [fresh_path, environment_path])
    check('agent_visibility', 'Actual initial declared-hidden-path/hash/mount scans have no matches under their retained scope/exclusions; only blind source files installed.', [environment_path, fresh_path])
    check('independent_semantics', 'Original procurement/finance inputs independently recomputed with Python Decimal and exact fractions; all declared outputs, current arithmetic/identity and frozen staging boundary checked.', [fresh_path, cases_path, contract_path])
    check('negatives', 'All33 independently established current-input target errors rejected after actual checker replay; no background failure counted as target detection.', [cases_path, fresh_path])
    check('equivalent', 'Four retained actual different-SQL controls rechecked against current exact raw inputs; twelve submitted-stage audit1 and four initial audit0. Legitimate row order/extra columns tested.', [equivalent_path, *tests])
    check('comparator', '17host and17locked-image actual tests cover exact large IDs, keyed tolerance, duplicates/missing rows/columns, row association, extra columns, corrupted captures and score bounds.', tests)
    notes = save(D / 'payment-notes-v1.json', {'preserved_attempts': ['batch04-v1 source installation followed by target deletion EIO; native rebuilt and returned1 but no agent dbt command', 'fresh-v1 review stopped on unhandled UNION AST', 'fresh-v2 review stopped serializing a Decimal diagnostic'], 'successful_evidence': ref(fresh_path), 'policy_scope': ref(contract_path), 'image_fixture_warning': 'Standalone Apptainer audit-only selftest reported unable to copy default /app packages; its17 actual Python comparator tests finished0. These fixtures make no reference build/environment initialization claim.', 'model_requests': 0})
    code = [ref(p) for p in sorted(HERE.glob('*.py'))] + [ref(HERE / 'policy.json')]
    code += [ref(ROOT / p) for p in ('scripts/audit_tables.py', 'src/dbt_bench/comparison.py', 'scripts/phase3_capture.py', 'scripts/phase3_parse_sql.py')]
    code += [ref(D / 'payment_selftest_v1.py')]
    overlay_path = HERE / 'overlay-v1.json'
    overlay = save(overlay_path, {'schema_version': 1, 'task_key': KEY, 'target': 'audit', 'checker_version': 'phase3-payment-fixed-input-v1', 'scope': 'Fixed current raw input, complete declared output and actual source workflow under explicit reviewed age/concentration policy; native scores unchanged.', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'review_status': 'reviewed', 'base_identity': ident['base_identity'], 'phase3_identity': ident, 'contract': contract, 'code_refs': code, 'checks': checks, 'requirement_coverage': coverage, 'notes': notes, 'model_requests': 0, 'native_rewards_modified': False, 'diagnoses': []})
    result = precheck(overlay_path)
    assert result['conclusion'] == 'validated' and result['review_complete']
    pre = save(D / 'payment-precheck-v1.json', result)
    print(save(D / 'payment-handoff-v1.json', {'task_key': KEY, 'audit_overlay': overlay, 'native_overlay': None, 'precheck': pre, 'phase3_identity': ident, 'contract': contract, 'model_requests': 0, 'formal_blind_audit_reward': 1, 'critical_rules': 33, 'target_detected': 33}))
if __name__ == '__main__':
    main()

"""Seal executed phase-three ROI evidence without changing original records."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import sys
HERE = Path(__file__).parent
ROOT = HERE.resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]
from dbt_bench import io
from phase3_review import binding, precheck
BASE = Path(_release_expand('${ARTIFACT_ROOT}/validation'))
D = BASE / 'phase3-review-v1/deb/root-three-v1'
B = BASE / 'phase3-deb-batch04-rebuild-v2'
KEY = 'deb__dbt-retail-task-04-market-roi'

def ref(path):
    return io.evidence_ref(path, ROOT)

def save(path, value):
    assert not path.exists(), path
    io.write_json(path, value)
    return ref(path)

def main():
    fp, cp = (D / 'roi-fresh-v1/review.json', D / 'roi-cases-v2/review.json')
    fresh, cases = (io.read_json(fp), io.read_json(cp))
    assert len(fresh['records']) == 2 and len({r['canonical_output_digest'] for r in fresh['records']}) == 1
    assert cases['target_errors_detected'] == 17 and cases['equivalents_accepted'] == 1
    tests = [D / ('roi-selftest-' + name + '-v1.json') for name in ('host', 'sif')]
    assert all((io.read_json(p)['passed'] == 23 and io.read_json(p)['failed'] == 0 for p in tests))
    contexts, visibility = ([], [])
    for row in fresh['records']:
        assert all(row['raw_matches'].values()) and row['context_values_equal']
        assert len(row['stages']) == 4 and all((s['reward'] == (0 if s['stage'] == 'initial' else 1) for s in row['stages']))
        trial = Path(row['trial'])
        native = io.read_json(trial / 'result.json')
        assert native['verifier_result']['rewards'] == {'reward': 1.0} and native['exception_info'] is None
        assert io.read_json(trial / 'agent/no-model.json')['model_requests'] == 0
        for part in ('agent/audit-initial', 'agent/audit-submission', 'verifier/mechanical-before', 'verifier/mechanical-after'):
            p = trial / part / 'snapshot.json'
            data = io.read_json(p)
            ctx = data['context']
            assert data['task_key'] == KEY and ctx['duckdb_version'] == '1.2.2'
            assert ctx['dbt']['version']['returncode'] == 0 and all((d.get('rows') == [[1]] for d in ctx['databases']))
            contexts.append({'snapshot': ref(p), 'context': ctx})
        v = trial / 'agent/audit-initial-visibility'
        facts, scan = (io.read_json(v / 'facts.json'), io.read_json(v / 'scan.json'))
        assert all(facts['declared_absent_paths'].values()) and (not any(facts['host_paths_visible'].values()))
        assert not scan['errors'] and (not scan['matches'])
        visibility.append({'evidence': [ref(v / n) for n in ('facts.json', 'scan.json', 'structured.json', 'files.jsonl', 'mountinfo.txt')], 'scope': scan['scope'], 'excluded_roots': scan['excluded_roots']})
    ep = D / 'roi-environment-v1.json'
    save(ep, {'task_key': KEY, 'contexts': contexts, 'visibility': visibility, 'scope': 'Actual Qwen shell and native test.sh contexts. Separate SIF comparator fixture entrypoint emitted read-only default-copy warnings; it is not initialization evidence.'})
    old = io.read_json(HERE.parent / 'contract.yaml')
    requirements = [{k: v for k, v in r.items() if k in ('id', 'critical', 'description', 'source', 'kind')} for r in old['audit']['requirements']]
    coverage = {}
    for case in cases['records']:
        if case['kind'] != 'business_error':
            continue
        assert case['target_reward'] == 0
        for rid in case['requirements']:
            coverage[rid] = {'status': 'covered', 'case_validity': 'business_error', 'reason': 'Independent current-input wrong-answer proof and actual new checker replay; named target rejects, not a background workflow error.', 'case': case['case'], 'independent_proof': case['independent_proof'], 'checker_evidence': case['audit'], 'evidence': [case['independent_proof'], case['audit'], ref(cp), ref(fp)]}
    assert set(coverage) == {r['id'] for r in requirements}
    contract_path = D / 'roi-contract-v1.json'
    contract = save(contract_path, {'schema_version': 1, 'task_key': KEY, 'review_status': 'reviewed', 'workflow': old['workflow'], 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'requirements': requirements, 'original_contract': ref(HERE.parent / 'contract.yaml'), 'instruction': ref(ROOT / 'tasks' / KEY / 'instruction.md'), 'policy': ref(HERE / 'policy.json'), 'blind_decisions': ref(Path(_release_expand('${SCRATCH_ROOT}/claude-two-version-pilot/dbt-retail-task-04-market-roi/decisions.md'))), 'scope': 'Current five declared staging inputs, all790x25 required output fields, nullable exact composite keys, unchanged documented numeric tolerances and original source/YAML/test/backend workflow. dbt.dateadd(day,literal,qualified-column) is accepted via the actual pinned adapter expansion; handwritten business INTERVAL remains rejected. Three extra output columns are permitted, not added to the required interface. No unseen input generalization claimed.'})
    checks = {}

    def check(name, reason, paths):
        checks[name] = {'status': 'pass', 'reason': reason, 'evidence': [ref(p) for p in paths], 'execution_evidence': [ref(p) for p in paths]}
    check('assets', 'Original task/native hashes and installed blind source hashes verified against actual commands and capture provenance.', [B / 'manifest.json', fp, ep])
    check('contract', 'All17 grouped critical requirements mapped to actual independently proved cases; portable DATEADD policy explicit.', [contract_path, cp, fp])
    for name in ('reference', 'reference_repeat'):
        check(name, 'Two actual clean blind dbt run and dbt test executions, native1 and three complete output-stage audit1 each.', [fp, B / 'reconciliation-v1.json'])
    check('nop', 'Both actually captured untouched initial submissions fail audit; native nop results retained in original record.', [fp])
    check('stability', 'All28 physical output columns, schema and multiplicities agree across both clean runs and all three stages; declared25 business columns independently recomputed.', [fp])
    check('environment_agent', 'Actual locked Qwen execution records user/PATH/Python/dbt/database; comparator fixtures also executed in pinned image.', [fp, ep, *tests])
    check('environment_verifier', 'Original test.sh observer reads the actual Qwen-built database successfully; original native scores preserved.', [fp, ep, B / 'reconciliation-v1.json'])
    check('cross_context', 'Complete same submission at agent/native-before/native-after verified with stable output and unchanged raw input projections.', [fp, ep])
    check('agent_visibility', 'Actual initial hidden-path/content/mount scan under documented exclusions found no reference access.', [fp, ep])
    check('independent_semantics', 'Independent Python Decimal grouping and date-window derivation uses all five declared current inputs; every790x25 required value/key matches. No semantic inference from native score.', [fp, cp, contract_path])
    check('negatives', 'Seventeen actual independently established value/workflow/interface errors rejected by their named components under current exact input binding.', [cp, fp])
    check('equivalent', 'Retained actual reordered/extra-column/exact Decimal-count legal submission passes new audit; independent blind portable DATEADD form also passes both runs.', [cp, fp, *tests])
    check('comparator', '23host+23image actual fixtures include exact IDs/large integers, keyed numeric tolerance, duplicates, NULL keys, row association, missing/extra columns and frozen DATEADD macro drift.', tests)
    notes = save(D / 'roi-notes-v1.json', {'preserved_attempts': ['batch04-v1 failed before agent build; native1 from grader rebuild is not witness', 'final dbt test capture KeyError compiled_code retained; preceding real run hook and manifest give compiled SQL while complete final physical/raw outputs remain available', 'case replay v1 stopped on accidentally including initial audit0 in submitted-stage assertion; v2 explicitly separates stages'], 'scope': contract, 'model_requests': 0, 'native_rewards_modified': False})
    ident = binding(B, KEY + '--phase3-blind-v1')
    overlay_path = HERE / 'overlay-v1.json'
    overlay = save(overlay_path, {'schema_version': 1, 'task_key': KEY, 'target': 'audit', 'checker_version': 'phase3-roi-fixed-input-v1', 'scope': 'All declared current ROI outputs and actual original workflow; native scores unchanged.', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'review_status': 'reviewed', 'base_identity': ident['base_identity'], 'phase3_identity': ident, 'contract': contract, 'code_refs': [ref(p) for p in HERE.glob('*.py')] + [ref(HERE / 'policy.json'), ref(ROOT / 'scripts/phase3_capture.py')], 'checks': checks, 'requirement_coverage': coverage, 'notes': notes, 'model_requests': 0, 'native_rewards_modified': False, 'diagnoses': []})
    result = precheck(overlay_path)
    assert result['conclusion'] == 'validated' and result['review_complete']
    pre = save(D / 'roi-precheck-v1.json', result)
    print(save(D / 'roi-handoff-v1.json', {'task_key': KEY, 'audit_overlay': overlay, 'native_overlay': None, 'precheck': pre, 'phase3_identity': ident, 'contract': contract, 'model_requests': 0, 'formal_blind_audit_reward': 1, 'critical_rules': 17, 'target_detected': 17}))
if __name__ == '__main__':
    main()

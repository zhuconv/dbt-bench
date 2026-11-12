"""Scoped old workflow and low-content examples with current input proof."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import importlib.util
import json
from dbt_bench import io
from audit_tables import decode
from phase3_capture import verify_trial
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/shared_projects/cart-trust-phase3-v1'))
module_path = R / 'validation/deb/dbt-retail-task-07-cart-recovery/phase3_v1/bind_unchanged_cases_v1.py'
s = importlib.util.spec_from_file_location('binding_helpers', module_path)
helper = importlib.util.module_from_spec(s)
s.loader.exec_module(helper)

def main():
    b = R / 'artifacts/validation/phase3-deb-batch06-rebuild-v2'
    key = 'deb__dbt-retail-task-10-trust-risk'
    d = verify_trial(b, b / 'jobs' / b.name / 'deb__dbt-retail-task-10-trust-ri__GYMBiX8')
    entry = d['entry']
    raw = {}
    for name, v in d['initial']['raw'].items():
        rows = [list(map(decode, json.loads(line))) for line in d['capture'].runtime(v).read_text().splitlines()]
        raw[name.removeprefix('main__')] = {'columns': v['columns'], 'sql_schema': v['sql_schema'], 'rows': [dict(zip(v['columns'], r)) for r in rows]}
    old = io.read_json(R / 'validation/deb/dbt-retail-task-10-trust-risk/contract.yaml')
    records = []
    for req in old['requirements']:
        if req['id'] not in {'R-backend-order', 'R-completion-build', 'BR-executed-low-content-flag'}:
            continue
        coverage = req['coverage']['native']
        for rr in coverage['evidence']:
            assert io.evidence_ok(rr, R)
        pp = next((R / rr['path'] for rr in coverage['evidence'] if rr['path'].endswith('/agent/case-proof.json')))
        trial = pp.parent.parent
        batch = trial.parents[2]
        mp = batch / 'manifest.json'
        manifest = io.read_json(mp)
        label = Path(io.read_json(trial / 'config.json')['task']['path']).name
        e = manifest['tasks'][label]
        assert e['base_task']['backend_binding'] == entry['base_task']['backend_binding']
        assert e['native_verifier']['sha256'] == entry['native_verifier']['sha256']
        assert io.evidence_ok(e['native_verifier'], R)
        proof = io.read_json(pp)
        assert proof['tested_checker_invoked_to_define_validity'] is False
        evidence = [helper.ref(pp), helper.ref(mp), e['case'], e['engine']]
        if req['id'] != 'BR-executed-low-content-flag':
            ip = pp.parent / 'trust-input-identity.json'
            observed = io.read_json(ip)['row_multiset_observed']
            assert all((helper.table_identity(raw[k]) == h for k, h in observed.items()))
            for rr in proof['submission_files']:
                if 'path' not in rr:
                    assert rr.get('exists') is False
                    continue
                p = pp.parent / Path(rr['path']).relative_to('/logs/agent')
                assert io.file_hash(p) == rr['sha256']
                evidence.append(helper.ref(p))
            evidence.append(helper.ref(ip))
            matched = 'All ten current typed raw-input tables including schemas and duplicate multiplicities.'
        else:
            ip = pp.parent / 'case-proof-inputs.json'
            assert io.file_hash(ip) == proof['input_artifacts'][0]['sha256']
            expected = io.read_json(ip)['expected_rows']
            current = [{'review_id': r['review_id'], 'low_content_flag': r['review_text'] is None or len(r['review_text']) < 20} for r in raw['stg_product__product_reviews']['rows']]
            assert sorted(current, key=lambda r: r['review_id']) == sorted(expected, key=lambda r: r['review_id'])
            assert proof['facts']['baseline_scope_error_count'] == 0 and proof['facts']['after_scope_error_count'] == 987
            for rr in proof['submission_artifacts']:
                if rr['path'].startswith('/logs/agent/'):
                    p = pp.parent / Path(rr['path']).name
                    assert io.file_hash(p) == rr['sha256']
                    evidence.append(helper.ref(p))
            evidence.append(helper.ref(ip))
            matched = 'Every current original review ID and independently recalculated short/missing-content flag matches the prior explicit case projection. Other semantics are not reused.'
        result = trial / 'result.json'
        native = io.read_json(result)
        assert native['exception_info'] is None
        records.append({'task_key': key, 'requirement_id': req['id'], 'coverage': coverage, 'case': label, 'proof': helper.ref(pp), 'native_result': helper.ref(result), 'native_rewards': native['verifier_result']['rewards'], 'image_and_backend_identical': True, 'native_verifier_identical': True, 'current_input_match_scope': matched, 'evidence': evidence, 'scope': 'Executed original case reused only for the unchanged literal rule. Current instruction is the exact original text plus a NULL-aggregation addition, which does not alter these workflow/low-content requirements.'})
    assert len(records) == 3
    p = D / 'unchanged-workflow-bindings-v1.json'
    assert not p.exists()
    io.write_json(p, {'records': records, 'code': helper.ref(Path(__file__)), 'model_requests': 0})
    print({'bound_cases': len(records), 'path': str(p)})
if __name__ == '__main__':
    main()

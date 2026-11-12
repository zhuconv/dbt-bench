"""Score each actual persisted wrong field, separately from background defects."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from dataclasses import replace
from datetime import date, datetime
import importlib.util
import json
from dbt_bench import io
from dbt_bench.comparison import compare_tables
from audit_tables import decode, load_observer_table
from phase3_capture import verify_trial
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/shared_projects/cart-trust-phase3-v1'))
B = R / 'artifacts/validation/phase3-deb-cart-trust-native-cases-v1'
spec = importlib.util.spec_from_file_location('cart_trust_current_engine', D / 'cart_trust_engine_v3.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

def ref(p):
    return io.evidence_ref(p, R)

def save(p, value):
    assert not p.exists(), p
    io.write_json(p, value)
    return ref(p)

def main():
    reconciliation = io.read_json(B / 'reconciliation-v1.json')
    assert reconciliation['complete'] and (not reconciliation['errors'])
    records = []
    for result in sorted((B / 'jobs' / B.name).glob('*/result.json')):
        trial = result.parent
        d = verify_trial(B, trial)
        cap, entry = (d['capture'], d['entry'])
        setup = entry['case_setup']
        for rr in (setup['case'], setup['engine']):
            assert io.evidence_ok(rr, R)
        q = io.read_json(R / setup['case']['path'])
        proofpath = trial / 'agent/case-proof.json'
        proof = cap.read(proofpath)
        assert proof['validity'] == 'business_error' and proof['tested_checker_invoked_to_define_validity'] is False
        assert proof['build_returncode'] == 0 and proof['changed_rows'] > 0
        assert q['task_key'] == proof['task_key'] == entry['task_key']
        assert proof['source_before_sha256'] == q['source_sha256']
        assert io.file_hash(trial / 'agent/case-build.log') == proof['build_log_sha256']
        raw = {name.removeprefix('main__'): [dict(zip([c.lower() for c in v['columns']], map(decode, json.loads(line)))) for line in cap.runtime(v).read_text().splitlines()] for name, v in d['initial']['raw'].items()}
        ex, facts = engine.expected(q['task_key'], raw)
        expected_file = Path(setup['case']['path']).parent / 'expected.json'
        assert io.file_hash(expected_file) == proof['independent_expected']['sha256']
        oldspec = importlib.util.spec_from_file_location('native_projection_engine', R / setup['engine']['path'])
        older = importlib.util.module_from_spec(oldspec)
        oldspec.loader.exec_module(older)
        columns = q['keys'] + [q['column']]
        projected = [tuple((row[c] for c in columns)) for row in ex[q['model']]]
        if q['model'] == 'rpt_review_moderation_kpis':
            projected = [tuple((v.date() if c == 'review_month' and type(v) is datetime else v for c, v in zip(columns, row))) for row in projected]
        assert older.equivalent_projection(projected, io.read_json(expected_file), q['column'])
        stages = []
        for stage, sub in [('agent-submission', 'agent/audit-submission'), ('verifier-before', 'verifier/mechanical-before'), ('verifier-after', 'verifier/mechanical-after')]:
            sp = trial / sub / 'snapshot.json'
            sr = ref(sp)
            snapshot = cap.read(sp)
            observed = [v for v in snapshot['files'] if v['path'] == '/app/dbt_models_duckdb/' + q['source_path']]
            assert len(observed) == 1 and observed[0]['sha256'] == proof['source_after_sha256']
            table = load_observer_table(sr, task_key=q['task_key'], database='/app/database/retail.duckdb', relation='main.' + q['model'], stage=stage, root=R)
            full = engine.score_table(q['task_key'], q['model'], table, ex[q['model']])
            values = engine.semantic_values(q['task_key'], q['model'], table, ex[q['model']])
            tc = engine.table_contract(q['task_key'], q['model'])
            tc = replace(tc, columns=tuple((c for c in tc.columns if c.name in columns)), allow_extra_columns=True)
            actual, invalid = engine.normalize(q['task_key'], q['model'], table.rows)
            expected, errors = engine.normalize(q['task_key'], q['model'], ex[q['model']])
            assert not invalid and (not errors)
            if q['model'] == 'rpt_review_moderation_kpis':
                for row in actual:
                    if type(row['review_month']) is date:
                        row['review_month'] = datetime.combine(row['review_month'], datetime.min.time())
            target = compare_tables(expected, actual, tc)
            assert not target.equal and values['equal_observed_business_values'] is False
            stages.append({'stage': stage, 'snapshot': sr, 'source_after_sha256': proof['source_after_sha256'], 'target_field_equal': target.equal, 'target_reason': target.reason, 'business_value_diagnostic': values, 'table_audit': full})
        native = io.read_json(result)
        assert native['exception_info'] is None
        native_log = trial / 'verifier/test-stdout.txt'
        assertions = [line for line in native_log.read_text().splitlines() if line.startswith('FAILED ')]
        assert assertions, 'Expected actual assertion failures, not an execution error'
        row = {'task_key': q['task_key'], 'case': q['case'], 'model': q['model'], 'column': q['column'], 'independent_proof': ref(proofpath), 'expected': ref(expected_file), 'input_facts': facts, 'native_result': ref(result), 'native_reward': native['verifier_result']['rewards']['reward'], 'native_test_log': ref(native_log), 'native_failed_assertions': assertions, 'audit_target_detected_all_stages': True, 'stages': stages, 'actual_evidence': list(cap.evidence.values()), 'scope': 'Current raw-input recomputation, exact expected projection, actual successful wrong source build and persistent before/after snapshots. A missing unrelated column or Trust CASE/type defect is not counted as detection; the named field/value comparison itself must fail.', 'model_requests': 0}
        rr = save(D / 'native-case-review-v1' / (trial.name + '.json'), row)
        records.append({'task_key': q['task_key'], 'case': q['case'], 'column': q['column'], 'model': q['model'], 'native_reward': row['native_reward'], 'audit_target_detected_all_stages': True, 'review': rr, 'independent_proof': ref(proofpath), 'native_result': ref(result)})
    assert len(records) == 21
    save(D / 'native-case-review-v1/summary.json', {'records': records, 'count': len(records), 'reconciliation': ref(B / 'reconciliation-v1.json'), 'code': ref(Path(__file__)), 'model_requests': 0})
    print(json.dumps({'reviewed': len(records), 'native_missed': sum((v['native_reward'] == 1 for v in records))}))
if __name__ == '__main__':
    main()

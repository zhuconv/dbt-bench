"""Recheck old executed cases only for unchanged rule/input/checker identity."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from datetime import datetime, date, timezone
from decimal import Decimal
import hashlib
import json
from dbt_bench import io
from audit_tables import decode
from phase3_capture import verify_trial
R = Path.cwd()
B = R / 'artifacts/validation/phase3-deb-batch06-rebuild-v2'
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/shared_projects/cart-trust-phase3-v1'))

def ref(p):
    return io.evidence_ref(p, R)

def encoded(v):
    if isinstance(v, (datetime, date, Decimal)):
        return {'$type': type(v).__name__, 'value': str(v)}
    raise TypeError(type(v).__name__)

def canonical(v):
    if isinstance(v, datetime):
        return {'$type': 'datetime', 'value': str(v.astimezone(timezone.utc) if v.tzinfo else v)}
    if isinstance(v, dict):
        return {k: canonical(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [canonical(x) for x in v]
    return v

def table_identity(t):
    t = canonical(t)
    v = {'columns': t['columns'], 'sql_schema': t['sql_schema'], 'rows_with_multiplicity': sorted((json.dumps(r, sort_keys=True, default=encoded) for r in t['rows']))}
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=encoded).encode()).hexdigest()

def main():
    manifest = io.read_json(B / 'manifest.json')
    results = []
    for task, trialname in [('deb__dbt-retail-task-07-cart-recovery', 'deb__dbt-retail-task-07-cart-rec__UZsMsty'), ('deb__dbt-retail-task-10-trust-risk', 'deb__dbt-retail-task-10-trust-ri__GYMBiX8')]:
        trial = B / 'jobs' / B.name / trialname
        d = verify_trial(B, trial)
        entry = manifest['tasks'][task + '--phase3-blind-v1']
        oldins = R / 'tasks' / task / 'instruction.md'
        newins = B / 'tasks' / (task + '--phase3-blind-v1') / 'instruction.md'
        assert newins.read_text().startswith(oldins.read_text().rstrip() + '\n')
        tables = {}
        for name, v in d['initial']['raw'].items():
            rows = [list(map(decode, json.loads(line))) for line in d['capture'].runtime(v).read_text().splitlines()]
            tables[name.removeprefix('main__')] = {'columns': v['columns'], 'sql_schema': v['sql_schema'], 'rows': [dict(zip(v['columns'], row)) for row in rows]}
        old = io.read_json(R / 'validation/deb' / task.removeprefix('deb__') / 'contract.yaml')
        for requirement in old['requirements']:
            cov = requirement.get('coverage', {}).get('native', {})
            refs = cov.get('evidence', [])
            for rr in refs:
                assert io.evidence_ok(rr, R), rr
            mp = next((R / rr['path'] for rr in refs if rr['path'].endswith('/manifest.json')), None)
            pp = next((R / rr['path'] for rr in refs if rr['path'].endswith('/agent/case-proof.json')), None)
            if not mp or not pp:
                continue
            mm = io.read_json(mp)
            proof = io.read_json(pp)
            result = pp.parent.parent / 'result.json'
            raw_result = io.read_json(result)
            label = cov.get('case')
            if label not in mm['tasks']:
                label = next((k for k, v in mm['tasks'].items() if v['task_key'] == task and io.read_json(R / v['case']['path']).get('case_id') == proof.get('case_id')), None)
            if not label:
                continue
            e = mm['tasks'][label]
            assert e['base_task']['backend_binding'] == entry['base_task']['backend_binding']
            assert e['native_verifier']['sha256'] == entry['native_verifier']['sha256']
            assert io.evidence_ok(e['native_verifier'], R)
            assert proof['tested_checker_invoked_to_define_validity'] is False
            matched = {}
            if '-07-' in task:
                for name, observation in proof['raw_input_observations'].items():
                    rows = [tuple(({k.lower(): v for k, v in row.items()}[c.lower()] for c in observation['columns'])) for row in tables[name]['rows']]
                    digest = hashlib.sha256(json.dumps(sorted(rows, key=lambda r: json.dumps(r, default=str)), default=str, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    matched[name] = digest == observation['sha256'] and len(rows) == observation['rows']
                submission = [e['case'], e['engine']]
                scope = 'Exact archived locked source bytes and deterministic engine mutation, pre-verifier proof, unchanged native result; current input projections match every ten-table input used by the archived case.'
            else:
                if 'input_identity' not in proof:
                    continue
                ip = pp.parent / Path(proof['input_identity']['path']).name
                assert io.file_hash(ip) == proof['input_identity']['sha256']
                expected = io.read_json(ip)['row_multiset_observed']
                matched = {name: table_identity(tables[name]) == digest for name, digest in expected.items()}
                submission = []
                for rr in proof['submission_files']:
                    if 'path' not in rr:
                        assert rr.get('exists') is False and rr.get('original_path')
                        continue
                    p = pp.parent / Path(rr['path']).relative_to('/logs/agent')
                    assert io.file_hash(p) == rr['sha256']
                    submission.append(ref(p))
                scope = 'Exact archived submitted source/config/YAML bytes, native checker, input typed schemas and all rows match current identity. Only the unchanged named rule is reused; this is not a whole-new-task certificate.'
            assert matched and all(matched.values()), (task, requirement['id'], matched)
            assert raw_result['exception_info'] is None
            results.append({'task_key': task, 'requirement_id': requirement['id'], 'case': label, 'image_and_backend_identical': True, 'native_verifier_identical': True, 'current_input_matches': matched, 'original_instruction_is_exact_prefix': True, 'instruction_refs': [ref(oldins), ref(newins)], 'current_batch': ref(B / 'manifest.json'), 'prior_manifest': ref(mp), 'proof': ref(pp), 'native_result': ref(result), 'native_rewards': raw_result['verifier_result']['rewards'], 'submission_refs': submission, 'coverage': cov, 'scope': scope})
    p = D / 'unchanged-case-bindings-v1.json'
    assert not p.exists()
    io.write_json(p, {'records': results, 'count': len(results), 'code': ref(Path(__file__)), 'model_requests': 0, 'native_rewards_modified': False})
    print(json.dumps({'bound_cases': len(results), 'path': str(p)}))
if __name__ == '__main__':
    main()

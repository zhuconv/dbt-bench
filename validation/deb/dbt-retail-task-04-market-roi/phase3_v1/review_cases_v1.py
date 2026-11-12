"""Replay retained actual ROI cases only after exact current-input binding."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import argparse
import json
import sys
import time
HERE = Path(__file__).parent
ROOT = HERE.resolve().parents[3]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
import check_v5
BASE = Path(_release_expand('${ARTIFACT_ROOT}/validation'))
D = BASE / 'phase3-review-v1/deb/root-three-v1'
OLD = BASE / 'phase2-review-v1/shared_projects/attribution-roi-v1/deb__dbt-retail-task-04-market-roi/final-replay-v2/summary.json'

def run(output):
    output.mkdir(parents=True, exist_ok=False)
    fresh_path = D / 'roi-fresh-v1/review.json'
    fresh = io.read_json(fresh_path)
    assert len(fresh['records']) == 2
    for row in fresh['records']:
        assert all((s['reward'] == (0 if s['stage'] == 'initial' else 1) for s in row['stages']))
        assert all(row['raw_matches'].values())
    contract = io.read_json(HERE.parent / 'contract.yaml')
    mappings = {r['id']: r['coverage']['audit'] for r in contract['audit']['requirements']}
    rows = []
    for old in io.read_json(OLD)['cases']:
        for field in ('proof', 'request', 'result'):
            assert io.evidence_ok(old[field], ROOT), field
        proof = io.read_json(ROOT / old['proof']['path'])
        assert (proof.get('proved_before_audit_unix') or proof.get('proof_before_audit_unix')) < time.time()
        request = io.read_json(ROOT / old['request']['path'])
        started = time.time()
        result = check_v5.run(request, ROOT)
        assert result == check_v5.run(request, ROOT)
        target = 'values' if old.get('requirement_ids') else 'workflow' if old['case'] == 'missing-model' else 'interface' if old['case'] == 'missing-column' else 'project_workflow'
        if old['kind'] == 'legal_equivalent':
            assert result['reward'] == 1, (old['case'], result)
        else:
            assert result['components'][target]['reward'] == 0, (old['case'], target, result)
        dest = output / old['case'] / 'audit.json'
        io.write_json(dest, result)
        requirements = [k for k, v in mappings.items() if v.get('case') == old['case']]
        rows.append({'case': old['case'], 'kind': old['kind'], 'requirements': requirements, 'reward': result['reward'], 'target': target, 'target_reward': result['components'][target]['reward'], 'request': old['request'], 'independent_proof': old['proof'], 'audit': io.evidence_ref(dest, ROOT), 'started_unix': started, 'prior_execution': io.evidence_ref(OLD, ROOT), 'input_binding': io.evidence_ref(fresh_path, ROOT), 'scope': 'Actual retained submitted output/source; current typed initial inputs exactly bound. Historical projections do not certify absent physical metadata; named semantic failure only.'})
    assert len(rows) == 18 and sum((r['kind'] == 'business_error' for r in rows)) == 17
    io.write_json(output / 'review.json', {'task_key': check_v5.TASK, 'records': rows, 'target_errors_detected': 17, 'equivalents_accepted': 1, 'model_requests': 0, 'code': io.evidence_ref(Path(__file__), ROOT), 'fresh_input_binding': io.evidence_ref(fresh_path, ROOT)})
    print({'cases': len(rows), 'target_errors_detected': 17, 'equivalents_accepted': 1})
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    run(p.parse_args().output)

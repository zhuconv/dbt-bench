"""Phase3 App001 package-interface audit, with separately trusted workflow."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import argparse, json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/spider2'))
import app001_raw_audit_v1 as data
import workflow_suite_v1 as workflow
import check_suite_v2 as old_context

def run(spec, root):
    if spec.get('mode') == 'nop':
        value = old_context.nop(spec, root, 'app_reporting001')
        return {'task_key': data.TASK, 'audit_reward': value['reward'], 'audit_status': value['status'], 'absence_audit': value, 'model_requests': 0}
    wf = workflow.check_trusted_rebuild(spec['batch_manifest'], spec['harbor_result'], spec['raw_snapshot'], root, 'app_reporting001')
    rp = Path(root) / spec['harbor_result']['path']
    stages = {}
    for folder, stage in [('agent/audit-submission', 'agent-submission'), ('verifier/mechanical-before', 'verifier-before'), ('verifier/mechanical-after', 'verifier-after')]:
        stages[stage] = data.replay({**spec, 'candidate_snapshot': data.ref(rp.parent / folder / 'snapshot.json'), 'candidate_stage': stage}, root)
    scores = [wf['reward']] + [v['audit_reward'] for v in stages.values()]
    score = 0.0 if 0 in scores else 1.0 if all((v == 1 for v in scores)) else None
    return {'schema_version': 1, 'task_key': data.TASK, 'audit_reward': score, 'audit_status': 'scored' if score is not None else 'unknown', 'workflow': wf, 'stages': stages, 'model_requests': 0, 'scope': 'Explicit frozen package-report interface and actual clean-input build, not universal all-raw metrics.'}
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--root', default='.')
    a = p.parse_args()
    v = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    Path(a.output).write_text(json.dumps(v, indent=2, default=data.encode) + '\n')
    print(json.dumps({'reward': v['audit_reward']}))

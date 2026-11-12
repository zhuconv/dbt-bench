"""Phase3 Workday002 trusted-build and full original-input audit."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import sys, argparse, json
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/spider2'))
from workday2_phase3_audit_v1 import run as rebuild_run, encode
from phase3_nop_context_v1 import nop

def run(spec, root):
    if spec.get('mode') == 'nop':
        result = nop(spec, root, 'workday002')
        return {'task_key': 'spider2__workday002', 'audit_reward': result['reward'], 'audit_status': result['status'], 'absence_audit': result, 'model_requests': 0}
    return rebuild_run(spec, root)
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--root', default='.')
    a = p.parse_args()
    v = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    Path(a.output).write_text(json.dumps(v, indent=2, default=encode) + '\n')
    print(json.dumps({'reward': v['audit_reward']}))

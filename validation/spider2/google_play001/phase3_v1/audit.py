"""Phase3 Google Play original-input and trusted-build audit."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import sys, argparse, json
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/spider2'))
from google_phase3_audit_v1 import run as rebuild_run, encode
import check_suite_v1 as old_context

def run(spec, root):
    if spec.get('mode') == 'nop':
        value = old_context.nop(spec, root, 'google_play001')
        return {'task_key': 'spider2__google_play001', 'audit_reward': value['reward'], 'audit_status': value['status'], 'absence_audit': value, 'model_requests': 0}
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

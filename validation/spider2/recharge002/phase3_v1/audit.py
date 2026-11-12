"""Phase3 raw Recharge calendar and running-total audit."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import sys, argparse, json
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/spider2'))
from recharge2_phase3_audit_v1 import run as rebuild, encode
import check_suite_v2 as old_context

def run(spec, root):
    if spec.get('mode') == 'nop':
        return old_context.run(spec, root, 'recharge002')
    return rebuild(spec, root)
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--root', default='.')
    args = p.parse_args()
    value = run(json.loads(Path(args.spec).read_text()), Path(args.root))
    Path(args.output).write_text(json.dumps(value, indent=2, default=encode) + '\n')
    print(json.dumps({'reward': value['audit_reward']}))

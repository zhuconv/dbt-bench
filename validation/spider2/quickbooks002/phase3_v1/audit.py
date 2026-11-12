"""Phase3 raw AP/AR audit; unresolved unpaid overdue policy stays unknown."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import sys, argparse, json
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/app-quickbooks-v1'))
from check_suite_v2 import run as check
from common_io_v1 import encode

def run(spec, root):
    return check(spec, root, 'quickbooks002')
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--root', default='.')
    args = p.parse_args()
    value = run(json.loads(Path(args.spec).read_text()), Path(args.root))
    Path(args.output).write_text(json.dumps(value, indent=2, default=encode) + '\n')
    print(json.dumps({'reward': value['audit_reward']}))

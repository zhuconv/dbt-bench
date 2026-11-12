from dbt_bench.local_config import expand as _release_expand
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/sap-zuora-v1')]
from check_suite_v1 import main
if __name__ == '__main__':
    main('sap001')

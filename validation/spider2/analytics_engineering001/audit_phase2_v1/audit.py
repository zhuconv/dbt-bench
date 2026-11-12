from dbt_bench.local_config import expand as _release_expand
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/analytics-asset-v1')]
from audit_suite_v2 import main
if __name__ == '__main__':
    main('analytics_engineering001')

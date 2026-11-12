from dbt_bench.local_config import expand as _release_expand
import sys
from pathlib import Path
HELPER = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/quickbooks-hive-v1'))
sys.path.insert(0, str(HELPER))
from audit_suite_v3 import main
if __name__ == '__main__':
    main('hive001')

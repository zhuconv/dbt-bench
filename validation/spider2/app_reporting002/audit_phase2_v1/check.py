"""app_reporting002 combined trusted-capture supplemental audit."""
from dbt_bench.local_config import expand as _release_expand
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/app-reporting-store-v1'))
from check_reports_v3 import main
if __name__ == '__main__':
    main('app_reporting002')

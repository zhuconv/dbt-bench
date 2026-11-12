from dbt_bench.local_config import expand as _release_expand
import sys
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/ade-airbnb-v1'))
from cli_v3 import main
if __name__ == '__main__':
    main('ade-bench-airbnb007')

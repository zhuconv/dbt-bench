from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/reddit-scd-v1'))
from check_suite_reddit_v2 import main
if __name__ == '__main__':
    main('reddit001')

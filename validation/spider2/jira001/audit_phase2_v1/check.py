"""jira001 supplemental combined audit entrypoint."""
from dbt_bench.local_config import expand as _release_expand
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/jira-marketo-v1'))
from check_suite_v1 import main
if __name__ == '__main__':
    main('jira001')

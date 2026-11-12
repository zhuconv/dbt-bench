from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/google-qualtrics-v1'))
from audit_suite_google_v1 import main
if __name__ == '__main__':
    main('google_play002')

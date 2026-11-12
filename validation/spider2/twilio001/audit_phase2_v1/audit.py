"""twilio001 supplemental audit entrypoint."""
from dbt_bench.local_config import expand as _release_expand
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, _release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/intercom-twilio-v1'))
from audit_suite_v1 import main
if __name__ == '__main__':
    main('twilio001')

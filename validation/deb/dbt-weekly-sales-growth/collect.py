"""Read-only capture after native rebuild; native reward is unchanged."""
import json,sys
from pathlib import Path
sys.path.insert(0,'/tests')
from case_engine import snapshot,encoded
observed,path=snapshot(Path('/logs/verifier/weekly-after'))
before=json.loads(Path('/logs/agent/weekly-before/snapshot.json').read_text())
out={'source_input_unchanged':observed['raw_input_sha256']==before['raw_input_sha256'],
     'submission_values_and_structure_unchanged':encoded(observed['models'])==encoded(before['models']),
     'before':before,'after':observed,'scope':'Read-only capture after native test; no reward override.'}
Path('/logs/verifier/weekly-comparison.json').write_text(encoded(out))

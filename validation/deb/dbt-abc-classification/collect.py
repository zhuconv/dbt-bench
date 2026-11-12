import json,sys
from pathlib import Path
sys.path.insert(0,'/tests')
from case_engine import snapshot,encoded
observed,path=snapshot(Path('/logs/verifier/abc-after'));before=json.loads(Path('/logs/agent/abc-before/snapshot.json').read_text())
Path('/logs/verifier/abc-comparison.json').write_text(encoded({'source_input_unchanged':before['raw_input_sha256']==observed['raw_input_sha256'],
 'submission_values_and_structure_unchanged':encoded(before['models'])==encoded(observed['models']),'before':before,'after':observed,'scope':'Read-only snapshots before/after unchanged native rebuild; no reward override.'}))

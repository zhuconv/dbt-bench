import json,sys
from pathlib import Path
sys.path.insert(0,'/tests')
from case_engine import snapshot,encoded
from independent import comparable
observed,path=snapshot(Path('/logs/verifier/channel-after'))
before=json.loads(Path('/logs/agent/channel-before/snapshot.json').read_text())
stable=all(encoded(before[k])==encoded(observed[k]) for k in ['columns','types','table_type']) and encoded(comparable(before['rows']))==encoded(comparable(observed['rows']))
out={'source_input_unchanged':before['raw_input_sha256']==observed['raw_input_sha256'],
 'stable_values_and_structure_unchanged':stable,'before':before,'after':observed,
 'scope':'Read-only before/after native capture; current_timestamp separately checked at each query, native reward unchanged.'}
Path('/logs/verifier/channel-comparison.json').write_text(encoded(out))

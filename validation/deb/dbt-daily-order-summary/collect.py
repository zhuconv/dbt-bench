"""Collect the same submission after native rebuild without altering its score."""
import json, sys
from pathlib import Path
sys.path.insert(0,'/tests')
from case_engine import snapshot, encoded

observed,path=snapshot(Path('/logs/verifier/daily-after'))
before=json.loads(Path('/logs/agent/daily-before/snapshot.json').read_text())
fields=['rows','columns','types','table_type','raw_input_sha256']
out={'source_input_unchanged':observed['raw_input_sha256']==before['raw_input_sha256'],
     'submission_values_and_structure_unchanged':all(encoded(observed[k])==encoded(before[k]) for k in fields),
     'before':before,'after':observed,'scope':'Read-only collection after native test execution; no reward override.'}
Path('/logs/verifier/daily-comparison.json').write_text(encoded(out))

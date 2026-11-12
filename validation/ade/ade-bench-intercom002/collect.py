"""Retain actual native-rebuild outputs without changing the native reward."""
import json
from pathlib import Path
import shutil
import duckdb
from case_engine import snapshot

spec = json.loads(Path('/solution/validation-case/case.json').read_text())
names = ['intercom__threads'] if spec['task_id'] == 'intercom001' else ['intercom__threads', 'intercom__conversation_metrics'] if spec['task_id'] == 'intercom002' else ['intercom__conversation_metrics']
out = Path('/logs/verifier/intercom'); out.mkdir(parents=True, exist_ok=True)
with duckdb.connect('/app/intercom.duckdb', read_only=True) as c:
    observed = snapshot(c, names)
(out / 'after-native.json').write_text(json.dumps(observed, sort_keys=True, indent=2, default=str) + '\n')
for name in ['manifest.json', 'run_results.json']:
    source = Path('/app/target') / name
    if source.exists(): shutil.copyfile(source, out / name)
for source in Path('/app/target/compiled').rglob('*.sql'):
    destination = out / 'compiled' / source.relative_to('/app/target/compiled')
    destination.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, destination)

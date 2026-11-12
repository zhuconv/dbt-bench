"""Retain post-native state and disclose stale run_results after parse failures."""
import hashlib
import json
from pathlib import Path
import shutil
import duckdb
from case_engine import snapshot
state = json.loads(Path('/logs/agent/intercom/submitted-state.json').read_text())
out = Path('/logs/verifier/intercom'); out.mkdir(parents=True, exist_ok=True)
with duckdb.connect('/app/intercom.duckdb', read_only=True) as c: after = snapshot(c, state['required_models'])
(out / 'after-native.json').write_text(json.dumps(after, indent=2, sort_keys=True, default=str) + '\n')
current_results = Path('/app/target/run_results.json')
observation = {'source_files_present': {name: (Path('/app/models') / (name + '.sql')).is_file() for name in state['required_models']},
               'target_run_results_present': current_results.is_file(), 'target_run_results_changed_since_agent': None,
               'scope': 'Unchanged run_results after native parse failure is historical agent-build output, not evidence that native tests executed.'}
if current_results.is_file():
    observation['target_run_results_changed_since_agent'] = hashlib.sha256(current_results.read_bytes()).hexdigest() != state['agent_run_results']['sha256']
    shutil.copyfile(current_results, out / 'target-run-results.json')
(out / 'native-execution-observation.json').write_text(json.dumps(observation, indent=2, sort_keys=True) + '\n')
for source in Path('/app/target/compiled').rglob('*.sql'):
    destination = out / 'compiled' / source.relative_to('/app/target/compiled'); destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)

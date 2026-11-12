"""Require the current delivered SQL as well as the trusted build manifest."""
from pathlib import Path
import importlib.util
import json

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('cart_phase3_workflow_base', HERE / 'project_workflow_v1.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def evaluate(request, root):
    answer = base.evaluate(request, root)
    facts = json.loads(base.read(request['workflow'], root))
    if facts.get('initial_required_models_absent'):
        return answer
    manifest = json.loads(base.read(facts['manifest'], root))
    checks = {}
    for model in base.MODELS:
        found = [v for v in manifest['nodes'].values()
                 if v.get('resource_type') == 'model' and v['name'] == model]
        if len(found) != 1:
            checks[model + ':delivered_source'] = False
            continue
        node = found[0]
        observed = facts['observed_files'].get(node['original_file_path'], {})
        present = observed.get('exists') is True and observed.get('file') is not None
        checks[model + ':delivered_source'] = present
        checks[model + ':delivered_source_matches_build'] = bool(present and
            base.read(observed['file'], root).decode().strip() == node['raw_code'].strip())
    return {**answer, 'checks': {**answer.get('checks', {}), **checks},
            'reward': 0.0 if not all(checks.values()) else answer['reward'],
            'source_delivery_scope': 'Required current SQL must exist and match its captured successful build. Build artifacts alone do not prove source delivery.'}

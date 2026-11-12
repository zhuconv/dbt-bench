"""New version of the risk audit; original checker and outputs are immutable."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from profile_runtime_v1 import extend

OLD = Path(__file__).resolve().parent.parent / 'audit_phase2_v1'
sys.path.insert(0, str(OLD))
spec = importlib.util.spec_from_file_location('risk_legacy_cli', OLD / 'run_audit_v3.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)

def run(spec, root):
    previous = legacy.run(spec, root)
    facts = json.loads(legacy.checked(spec['trusted_workflow'], root)) if spec.get('trusted_workflow') else {}
    return extend(previous, facts, 'risk')

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True); p.add_argument('--root', default='.'); p.add_argument('--output', required=True)
    a = p.parse_args(); path = Path(a.output)
    if path.exists(): raise ValueError('Refuse to replace an existing result')
    result = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    path.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'status': result['status'], 'audit_reward': result['reward']}))

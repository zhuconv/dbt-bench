"""New version of the daily audit; original checker and outputs are immutable."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
SHARED = Path(__file__).resolve().parents[2] / 'dbt-customer-risk-scoring' / 'phase4_v1'
sys.path.insert(0, str(SHARED))
from profile_runtime_v2 import extend
OLD = Path(__file__).resolve().parent.parent / 'audit_phase2_v1'
sys.path.insert(0, str(OLD))
spec = importlib.util.spec_from_file_location('daily_legacy_cli', OLD / 'run_audit_v3.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)

def run(spec, root):
    previous = legacy.run(spec, root)
    facts = json.loads(legacy.checked(spec['trusted_workflow_facts'], root)) if spec.get('trusted_workflow_facts') else {}
    return extend(previous, facts, 'daily')

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True); p.add_argument('--root', default='.'); p.add_argument('--output', required=True)
    a = p.parse_args(); path = Path(a.output)
    if path.exists(): raise ValueError('Refuse to replace an existing result')
    result = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    path.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'status': result['status'], 'audit_reward': result['reward']}))

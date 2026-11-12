"""Inventory variant audit: selected runtime profile and explicit column names."""
import argparse
import json
from pathlib import Path
from check_v1 import run as previous, legacy
from named_columns_v1 import check


def run(request, root):
    result = previous(request, root)
    capture = json.loads(legacy.module('workflow').read_ref(request['snapshot'], root))
    tables = [r for r in capture['outputs'] if r['specification']['relation'] == 'inventory_analytics.fct_warehouse_inventory']
    if len(tables) != 1 or tables[0].get('error'):
        component = {'status': 'unknown', 'reward': None, 'reason': 'Required output schema was not captured'}
    else:
        component = check([c['name'] for c in tables[0]['schema']])
    result['components']['variant_column_names'] = component
    rewards = [c.get('reward') for c in result['components'].values()]
    result['reward'] = 0.0 if 0 in rewards else None if None in rewards else 1.0
    result['status'] = 'unknown' if result['reward'] is None else 'scored'
    result['checker_version'] = 'phase4-inventory-explicit-names-v2'
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--request', type=Path, required=True); p.add_argument('--output', type=Path, required=True); p.add_argument('--repo-root', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists(): raise ValueError('Existing result')
    try: result = run(json.loads(a.request.read_text()), a.repo_root)
    except Exception as exc: result = {'task_key': 'deb__dbt-inventory-analysis', 'target': 'audit', 'status': 'unknown', 'reward': None, 'reason': str(exc), 'evidence': json.loads(a.request.read_text())}
    a.output.write_text(json.dumps(result, indent=2) + '\n')

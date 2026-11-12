#!/usr/bin/env python3
"""Declare untouched controls from reviewed requests, with no author payload."""
import argparse
from copy import deepcopy
from pathlib import Path
from dbt_bench import io
from phase4_prepare import PILOT, plan_entry
from review_tools import ROOT


def nop_entry(entry):
    result = deepcopy(entry)
    result.pop('mode', None)
    result['label'] = entry['task_key'] + '--phase4-nop-v1'
    rebuild = result['rebuild']
    rebuild.update(mode='nop', commands=[], capture_hooks=[], fresh_artifact_paths=[])
    rebuild['submission'] = {'files': [], 'reset_paths': [], 'delete_paths': []}
    return result


def prepare_requests(paths, output, include_originals=False, root=ROOT, pilot=PILOT):
    root, pilot, output = Path(root), Path(pilot), Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Use a new SCRATCH request file')
    catalog = io.unique_index(io.read_jsonl(root/'catalog/tasks.jsonl'), 'task_key')
    results = io.read_json(pilot/'results_test.json')
    rows, seen = [], set()
    for path in paths:
        value = io.read_json(path)
        if value.get('model_requests') != 0: raise ValueError('No model requests allowed')
        for entry in value['tasks']:
            identity = plan_entry(entry, catalog, results, pilot, root)
            if not include_originals and not identity['task_variant']: continue
            if entry['task_key'] in seen: raise ValueError('Duplicate requested nop task')
            seen.add(entry['task_key']); rows.append(nop_entry(entry))
    if not rows: raise ValueError('No authorized control tasks selected')
    io.write_json(output, {'schema_version': 1, 'model_requests': 0, 'n_attempts': 1,
        'tasks': rows, 'source_requests': [io.evidence_ref(p, root) for p in paths],
        'scope': 'Actual untouched current-instruction controls; no author files, build commands or capture-hook payload are installed.'})
    return {'tasks': len(rows), 'output': str(output), 'model_requests': 0}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--requests', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--include-originals', action='store_true')
    a=p.parse_args(); print(prepare_requests(a.requests, a.output, a.include_originals))

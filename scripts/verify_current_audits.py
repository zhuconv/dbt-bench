#!/usr/bin/env python3
"""Check frozen audit reviews against current task identities and bound evidence."""
import argparse
from pathlib import Path

from dbt_bench import io
from dbt_bench.validation import CHECKS, identity
from phase2_publish import verify_freeze
from review_tools import ROOT, cached_hashes


def references(value):
    if isinstance(value, dict):
        if {'path', 'sha256'} <= value.keys():
            yield value
        else:
            for child in value.values():
                yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def verify_records(root, rows, records, baseline, keys, identity_fn=identity):
    """Inspect declared audit references, without inferring hidden nested schemas."""
    root = Path(root)
    if set(keys) - (rows.keys() & records.keys() & baseline.keys()):
        raise ValueError('Completed task absent from catalog or frozen ledgers')
    results = []
    with cached_hashes():
        for key in sorted(keys):
            record = records[key]
            contract = (root / rows[key]['validation_ref']).parent / 'contract.yaml'
            current = identity_fn(root, rows[key], contract, record['identity']['backend'])
            refs = list(references(record['audit']))
            missing = [ref for ref in refs if not io.evidence_ok(ref, root)]
            checks = record['audit']['checks']
            results.append({
                'task_key': key, 'identity_current': current == record['identity'],
                'native_unchanged': record['native'] == baseline[key]['native'],
                'audit14_review_complete': set(checks) == set(CHECKS)
                    and all(c['status'] in {'pass', 'fail', 'gap'} for c in checks.values()),
                'evidence_ref_count': len(refs), 'evidence_complete': bool(refs) and not missing,
                'invalid_evidence': missing,
            })
    return {'schema_version': 1, 'tasks': results,
            'passed': all(all(row[name] for name in ('identity_current', 'native_unchanged',
                'audit14_review_complete', 'evidence_complete')) for row in results),
            'model_requests': 0,
            'scope': 'Current task identity and references declared in each frozen audit branch; '
                     'immutable pool consistency is verified separately. This is not a new semantic execution.'}


def verify_current(pool):
    pool = Path(pool)
    verified = verify_freeze(pool)
    manifest = io.read_json(pool / 'manifest.json')
    rows = io.unique_index(io.read_jsonl(ROOT / 'catalog/tasks.jsonl'), 'task_key')
    records = io.unique_index(io.read_jsonl(ROOT / manifest['ledger']['path']), 'task_key')
    baseline = io.unique_index(io.read_jsonl(ROOT / manifest['baseline_ledger']['path']), 'task_key')
    result = verify_records(ROOT, rows, records, baseline, manifest['completed_new_audit14'])
    result['freeze'] = verified['manifest']
    result['checker'] = io.evidence_ref(Path(__file__), ROOT)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite an evidence check')
    result = verify_current(args.pool)
    io.write_json(args.output, result)
    print({'passed': result['passed'], 'tasks': len(result['tasks'])})
    raise SystemExit(0 if result['passed'] else 1)

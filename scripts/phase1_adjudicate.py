#!/usr/bin/env python3
"""Bind existing reviewed native misses to their proof/result, without rerunning or refreezing."""
import argparse
from copy import deepcopy
from pathlib import Path
from review_tools import ROOT, cached_hashes
from evidence_archive import archive_review_refs
from dbt_bench import io
from dbt_bench.catalog import load_catalog
from dbt_bench.validation import build_record, identity, review_inputs, reusable, summarize


def check_binding(key, correction, reconciliation, proof):
    candidates = [r for r in reconciliation['records'] if r['result']['sha256'] == correction['checker_evidence']['sha256']]
    if len(candidates) != 1:
        raise ValueError('Expected exactly one reconciled trial')
    record = candidates[0]
    if (record['task_key'] != key or record['kind'] != 'negative'
            or not record['proof_valid_for_declared_scope'] or record['proof_completed_before_verifier'] is not True
            or record['native_rewards'] != {'reward': 1.0}
            or correction['case_validity'] != 'business_error'
            or proof.get('validity') != 'business_error'
            or proof.get('tested_checker_invoked_to_define_validity') is not False):
        raise ValueError('Not an independently proved, accepted native error for this task')
    expected = correction['independent_proof']
    if not any(a['sha256'] == expected['sha256'] and
               (ROOT / a['path']).resolve() == (ROOT / expected['path']).resolve()
               for a in record['artifacts']):
        raise ValueError('Proof belongs to a different trial')
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--correction', type=Path, action='append', required=True)
    p.add_argument('--archive-task', action='append', default=[], help='Preserve published review bytes for another completed task')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    assert not args.output.exists()
    args.output.mkdir(parents=True)
    rows = load_catalog(ROOT)
    by_row = {r['task_key']: r for r in rows}
    records = {r['task_key']: r for r in io.read_jsonl(ROOT / 'validation/ledger.jsonl')}
    state = io.read_json(ROOT / 'artifacts/validation/phase1-review-v1/state.json')
    saved, results, seen = {}, [], set()
    with cached_hashes():
        try:
            inputs = [(source, io.read_json(source)) for source in args.correction]
            inputs += [(None, {'task_key': key, 'base_identity': records[key]['identity'], 'corrections': []})
                       for key in args.archive_task]
            for source, correction in inputs:
                key = correction['task_key']
                assert key in state['completed'] and key not in seen
                seen.add(key)
                row, old_record = by_row[key], records[key]
                directory = (ROOT / row['validation_ref']).parent
                path = directory / 'contract.yaml'
                current = identity(ROOT, row, path, 'apptainer')
                assert current == correction['base_identity'] == old_record['identity']
                old = io.read_json(path)
                contract = deepcopy(old)
                requirements = {r['id']: r for r in contract['requirements']}
                corrected_ids = set()
                for change in correction['corrections']:
                    rule = change['requirement_id']
                    assert rule not in corrected_ids
                    corrected_ids.add(rule)
                    coverage = requirements[rule]['coverage']['native']
                    assert coverage['status'] == 'missed_wrong'
                    for field in ('independent_proof', 'checker_evidence', 'reconciliation'):
                        assert io.evidence_ok(change[field], ROOT), (key, rule, field)
                    reconciliation = io.read_json(ROOT / change['reconciliation']['path'])
                    proof = io.read_json(ROOT / change['independent_proof']['path'])
                    check_binding(key, change, reconciliation, proof)
                    coverage.update({f: change[f] for f in ('independent_proof', 'checker_evidence', 'case_validity')})
                    for field in ('independent_proof', 'checker_evidence', 'reconciliation'):
                        if change[field] not in coverage.setdefault('evidence', []):
                            coverage['evidence'].append(change[field])
                for name in ('contract.yaml', 'record.json'):
                    file = directory / name
                    saved[file] = file.read_bytes()
                    backup = args.output / 'before' / key / name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(saved[file])
                contract.setdefault('review_history', []).append(io.evidence_ref(args.output / 'before' / key / 'contract.yaml', ROOT))
                if source:
                    contract.setdefault('review_evidence', []).append(io.evidence_ref(source, ROOT))
                contract['reviewed_inputs'] = review_inputs(current)
                contract = archive_review_refs(contract, ROOT, ROOT / 'artifacts/validation/phase1-freeze-objects')
                io.write_json(path, contract)
                updated = build_record(ROOT, row, path, old_record.get('execution'), backend='apptainer')
                assert updated['review_current'] and not updated['blockers'] and reusable(updated, updated['identity'], ROOT)
                assert updated['native']['conclusion'] == ('weak' if corrected_ids else old_record['native']['conclusion'])
                for check, old_check in old_record['native']['checks'].items():
                    new_check = updated['native']['checks'][check]
                    assert new_check['status'] == old_check['status'] and new_check['reason'] == old_check['reason']
                    assert [e['sha256'] for e in new_check['evidence']] == [e['sha256'] for e in old_check['evidence']]
                assert updated['audit'] == old_record['audit']
                io.write_json(directory / 'record.json', updated)
                records[key] = updated
                results.append({'task_key': key, 'previous_conclusion': old_record['native']['conclusion'],
                    'conclusion': updated['native']['conclusion'], 'bound_requirement_ids': sorted(corrected_ids),
                    'correction': io.evidence_ref(source, ROOT) if source else None})
        except BaseException:
            for path, data in saved.items():
                path.write_bytes(data)
            io.write_json(args.output / 'rollback.json', {'canonical_restored': True, 'ledger_modified': False})
            raise
        summary = summarize(ROOT, rows, [records[r['task_key']] for r in rows])
        manifest = {'tasks': results, 'new_trials': 0, 'new_model_requests': 0,
            'native_rewards_modified': False, 'audit_modified': False, 'frozen_pools_modified': False,
            'completed_native14': len(state['completed']), 'native_counts': summary['native'],
            'ledger': io.evidence_ref(ROOT / 'validation/ledger.jsonl', ROOT),
            'publisher': io.evidence_ref(Path(__file__), ROOT),
            'scope': 'Current ledger adjudication bindings corrected from existing evidence. Previous frozen snapshots stay immutable; next regular freeze incorporates these records.'}
        io.write_json(args.output / 'manifest.json', manifest)
        delivery = io.read_json(ROOT / 'validation/delivery-status.json')
        delivery['native'] = summary['native']
        delivery.setdefault('post_freeze_adjudication_corrections', []).append(io.evidence_ref(args.output / 'manifest.json', ROOT))
        io.write_json(ROOT / 'validation/delivery-status.json', delivery)
        print({'corrected_tasks': len(results), 'native': summary['native'], 'new_trials': 0})


if __name__ == '__main__':
    main()

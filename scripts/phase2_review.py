#!/usr/bin/env python3
"""Read-only validation of task-specific supplemental-audit review overlays.

This uses the existing adjudication protocol and leaves the native ledger and
all frozen versions untouched. Partial reviews are allowed but cannot certify
pending checks or be counted as completed audit14 reviews.
"""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from dbt_bench import io
from dbt_bench.validation import CHECKS, REF_KINDS, WITNESS_KINDS, adjudicate, identity
from review_tools import cached_hashes

EXECUTED_CHECKS = set(CHECKS) - {'assets', 'contract'}
STATUSES = {'pending', 'pass', 'fail', 'gap'}
COVERAGE_STATUSES = {'pending', 'covered', 'gap', 'missed_wrong', 'rejected_legal'}


def _refs(refs, root, label, required=True):
    if not isinstance(refs, list) or required and not refs:
        raise ValueError('Missing evidence: ' + label)
    for ref in refs:
        if not io.evidence_ok(ref, root):
            raise ValueError('Changed or unavailable evidence: ' + label + ': ' + str(ref))


def check_overlay(overlay, base_record, contract, current_identity, root):
    """Validate scope/evidence and adjudicate an audit without changing native."""
    if overlay.get('schema_version') != 1 or overlay.get('target') != 'audit':
        raise ValueError('Expected audit overlay schema 1')
    task_key = base_record['task_key']
    if overlay.get('task_key') != task_key or contract['task_key'] != task_key:
        raise ValueError('Task identity mismatch')
    if base_record['native']['conclusion'] != 'weak':
        raise ValueError('Phase 2 audits are scoped to weak tasks; isolated tasks stay untouched')
    if overlay.get('base_identity') != current_identity or base_record['identity'] != current_identity:
        raise ValueError('Stale task/data/environment/native review identity')
    if overlay.get('model_requests') != 0:
        raise ValueError('Phase 2 forbids new model requests')
    if not overlay.get('scope') or not overlay.get('checker_version'):
        raise ValueError('Explicit audit version and scope required')
    if overlay.get('reference_kind') not in REF_KINDS or overlay.get('witness_kind') not in WITNESS_KINDS:
        raise ValueError('Unknown reference or witness kind')
    code_refs = overlay.get('code_refs')
    _refs(code_refs, root, 'checker code')
    code_paths = [str((Path(root) / ref['path']).resolve()) for ref in code_refs]
    if len(code_paths) != len(set(code_paths)):
        raise ValueError('Duplicate checker source reference')
    for name in ('scripts/audit_tables.py', 'src/dbt_bench/comparison.py'):
        if str((Path(root) / name).resolve()) not in code_paths:
            raise ValueError('Unbound shared audit dependency: ' + name)
    if len(code_paths) < 3:
        raise ValueError('Task-specific checker source required')
    checks = overlay.get('checks', {})
    if set(checks) != set(CHECKS):
        raise ValueError('Audit review must account for all 14 checks')
    for name, check in checks.items():
        if check.get('status') not in STATUSES or not check.get('reason'):
            raise ValueError('Invalid check status/reason: ' + name)
        done = check['status'] != 'pending'
        _refs(check.get('evidence', []), root, name, required=done)
        if done and name in EXECUTED_CHECKS:
            _refs(check.get('execution_evidence'), root, name + ' execution')
    requirements = deepcopy(contract['requirements'])
    critical = {r['id'] for r in requirements if r.get('critical', True)}
    all_ids = {r['id'] for r in requirements}
    coverage = overlay.get('requirement_coverage', {})
    if not critical <= coverage.keys() or coverage.keys() - all_ids:
        raise ValueError('Coverage must account for each declared critical rule, without invented IDs')
    for req in requirements:
        audit = coverage.get(req['id'], {'status': 'pending', 'reason': 'Not reviewed', 'evidence': []})
        status = audit.get('status')
        if status not in COVERAGE_STATUSES:
            raise ValueError('Invalid audit coverage: ' + req['id'])
        if status != 'pending':
            if not audit.get('reason'):
                raise ValueError('Missing coverage rationale: ' + req['id'])
            _refs(audit.get('evidence'), root, req['id'])
        if status in {'missed_wrong', 'rejected_legal'}:
            _refs([audit.get('independent_proof'), audit.get('checker_evidence')], root, req['id'] + ' independent proof')
            expected = 'business_error' if status == 'missed_wrong' else 'legal_equivalent'
            if audit.get('case_validity') != expected:
                raise ValueError('Wrong independent case validity: ' + req['id'])
        req.setdefault('coverage', {})['audit'] = audit
    merged = {**contract, 'requirements': requirements, 'review_status': overlay.get('review_status'),
              'reference_kind': overlay['reference_kind'], 'witness_kind': overlay['witness_kind'],
              'diagnoses': overlay.get('diagnoses', [])}
    conclusion, uncovered, reasons = adjudicate(merged, checks, identity_complete=True, root=root, target='audit')
    complete = all(c['status'] != 'pending' for c in checks.values()) and all(coverage[k]['status'] != 'pending' for k in critical)
    return {'schema_version': 1, 'task_key': task_key, 'target': 'audit', 'overlay_valid': True,
        'audit14_review_complete': complete, 'conclusion': conclusion,
        'check_counts': dict(Counter(c['status'] for c in checks.values())),
        'uncovered_requirements': uncovered, 'reasons': reasons,
        'checker_version': overlay['checker_version'],
        'checker_code_hash': io.digest(sorted(code_refs, key=lambda x: x['path'])),
        'native_conclusion_unchanged': base_record['native']['conclusion'],
        'native_rewards_modified': False, 'model_requests': 0,
        'scope': overlay['scope']}


def precheck(root, path):
    root = Path(root)
    overlay = io.read_json(path)
    rows = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')
    row = rows[overlay['task_key']]
    record_path = root / row['validation_ref']
    contract_path = record_path.parent / 'contract.yaml'
    base = io.read_json(record_path)
    with cached_hashes():
        current = identity(root, row, contract_path, base['identity']['backend'])
        result = check_overlay(overlay, base, io.read_json(contract_path), current, root)
    return {**result, 'overlay': io.evidence_ref(path, root),
            'base_record': io.evidence_ref(record_path, root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = precheck(args.root, args.overlay)
    if args.output.exists():
        raise ValueError('Refuse to overwrite a precheck result')
    io.write_json(args.output, result)
    print({k: result[k] for k in ('task_key', 'overlay_valid', 'audit14_review_complete', 'conclusion', 'check_counts')})


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Read-only adjudication of new train-only native/audit companion reviews."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from dbt_bench import io
from dbt_bench.validation import CHECKS, REF_KINDS, WITNESS_KINDS, adjudicate, identity
from phase2_review import _refs, STATUSES, COVERAGE_STATUSES, EXECUTED_CHECKS
from phase3_prepare import FORMAL
from review_tools import ROOT, cached_hashes


def binding(batch, label, root=ROOT):
    batch, root = Path(batch), Path(root)
    manifest = io.read_json(batch / 'manifest.json')
    if manifest.get('phase') != 3 or manifest.get('model_requests') != 0:
        raise ValueError('Not a formal phase 3 batch')
    entry = manifest['tasks'][label]
    info = entry['phase3']
    key = entry['task_key']
    row = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')[key]
    if row['split'] != 'train' or info['category'] not in FORMAL:
        raise ValueError('Outside formal train scope')
    if not io.evidence_ok(info['base_record'], root):
        raise ValueError('Original record changed')
    target = batch / 'tasks' / label
    if io.tree_hash(target) != entry['validation_copy_hash']:
        raise ValueError('Formal validation copy changed')
    if not io.evidence_ok(entry['native_verifier'], root):
        raise ValueError('Native verifier changed')
    base = io.read_json(root / info['base_record']['path'])
    extension = entry['base_task'].get('environment_extension')
    if entry['base_task']['backend_binding'] != base['identity']['backend_binding']:
        if not extension or extension['before_binding'] != base['identity']['backend_binding'] or not io.evidence_ok(extension['summary'], root):
            raise ValueError('Formal batch uses an unreviewed different environment binding')
        layer = io.read_json(root / extension['summary']['path'])
        if (not all(layer.get(k) is True for k in ('passed', 'task_files_exact', 'task_packages_exact', 'task_python_exact'))
                or layer['before_image']['sha256'] != base['identity']['backend_binding']['image_sha256']
                or layer['after_image']['sha256'] != entry['base_task']['backend_binding']['image_sha256']
                or not io.evidence_ok(layer['after_image'], root)):
            raise ValueError('Transport layer is not valid for this execution')
    result = {'schema_version': 1, 'task_key': key, 'task_version': info['task_version'],
        'category': info['category'], 'task_variant': info['task_variant'],
        'gold_derived_correction': info['gold_derived_correction'],
        'instruction_sha256': info['instruction_after_sha256'],
        'base_record': info['base_record'], 'base_identity': base['identity'],
        'native_verifier': entry['native_verifier'],
        'batch_manifest': io.evidence_ref(batch / 'manifest.json', root),
        'label': label, 'submission_sources': entry['submission_source_refs'],
        'request': entry['request'], 'model_requests': 0}
    if extension:
        result.update(environment_extension=extension, actual_backend_binding=entry['base_task']['backend_binding'])
    return result


def check_overlay(overlay, contract, expected, root=ROOT):
    target = overlay.get('target')
    if overlay.get('schema_version') != 1 or target not in {'native', 'audit'}:
        raise ValueError('Expected phase 3 native/audit companion schema 1')
    key = expected['task_key']
    if overlay.get('task_key') != key or contract.get('task_key') != key:
        raise ValueError('Task mismatch')
    if overlay.get('phase3_identity') != expected or overlay.get('base_identity') != expected['base_identity']:
        raise ValueError('Stale phase 3 or original identity')
    if overlay.get('model_requests') != 0 or not overlay.get('checker_version') or not overlay.get('scope'):
        raise ValueError('Require no-model scope and explicit checker version')
    if overlay.get('reference_kind') not in REF_KINDS or overlay.get('witness_kind') not in WITNESS_KINDS:
        raise ValueError('Unknown reference/witness kind')
    _refs([overlay.get('contract')], root, 'new contract')
    code = overlay.get('code_refs')
    _refs(code, root, 'checker dependencies')
    paths = [str((Path(root) / x['path']).resolve()) for x in code]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate checker dependency')
    if target == 'audit':
        for name in ('scripts/audit_tables.py', 'src/dbt_bench/comparison.py'):
            if str((Path(root) / name).resolve()) not in paths:
                raise ValueError('Missing shared audit dependency: ' + name)
        if len(paths) < 3:
            raise ValueError('Task audit dependency required')
    checks = overlay.get('checks', {})
    if set(checks) != set(CHECKS):
        raise ValueError('All 14 checks must be accounted for')
    for name, check in checks.items():
        if check.get('status') not in STATUSES or not check.get('reason'):
            raise ValueError('Invalid check: ' + name)
        done = check['status'] != 'pending'
        _refs(check.get('evidence', []), root, name, required=done)
        if done and name in EXECUTED_CHECKS:
            _refs(check.get('execution_evidence'), root, name + ' actual execution')
    requirements = deepcopy(contract['requirements'])
    ids = [r['id'] for r in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate requirement IDs')
    critical = {r['id'] for r in requirements if r.get('critical', True)}
    coverage = overlay.get('requirement_coverage', {})
    if not critical <= coverage.keys() or coverage.keys() - set(ids):
        raise ValueError('Coverage must describe all critical rules')
    for req in requirements:
        value = coverage.get(req['id'], {'status': 'pending', 'evidence': []})
        status = value.get('status')
        if status not in COVERAGE_STATUSES:
            raise ValueError('Invalid coverage status')
        if status != 'pending':
            if not value.get('reason'):
                raise ValueError('Missing coverage rationale')
            _refs(value.get('evidence'), root, req['id'])
        if status in {'missed_wrong', 'rejected_legal'}:
            _refs([value.get('independent_proof'), value.get('checker_evidence')], root, req['id'])
            if value.get('case_validity') != ('business_error' if status == 'missed_wrong' else 'legal_equivalent'):
                raise ValueError('Case not independently established')
        req.setdefault('coverage', {})[target] = value
    merged = {**contract, 'requirements': requirements, 'review_status': overlay.get('review_status'),
        'reference_kind': overlay['reference_kind'], 'witness_kind': overlay['witness_kind'],
        'diagnoses': overlay.get('diagnoses', [])}
    conclusion, gaps, reasons = adjudicate(merged, checks, identity_complete=True, root=root, target=target)
    return {'schema_version': 1, 'task_key': key, 'task_version': expected['task_version'],
        'target': target, 'category': expected['category'], 'conclusion': conclusion,
        'review_complete': all(c['status'] != 'pending' for c in checks.values()) and
                           all(coverage[k]['status'] != 'pending' for k in critical),
        'check_counts': dict(Counter(c['status'] for c in checks.values())),
        'uncovered_requirements': gaps, 'reasons': reasons, 'model_requests': 0,
        'native_rewards_modified': False, 'checker_version': overlay['checker_version']}


def precheck(path, root=ROOT):
    root = Path(root)
    overlay = io.read_json(path)
    supplied = overlay['phase3_identity']
    manifest_path = root / supplied['batch_manifest']['path']
    if not io.evidence_ok(supplied['batch_manifest'], root):
        raise ValueError('Batch manifest changed')
    expected = binding(manifest_path.parent, supplied['label'], root)
    row = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')[expected['task_key']]
    base = io.read_json(root / expected['base_record']['path'])
    with cached_hashes():
        current = identity(root, row, (root / row['validation_ref']).parent / 'contract.yaml', base['identity']['backend'])
        if current != expected['base_identity']:
            raise ValueError('Current original environment/task/checker identity drift')
        contract = io.read_json(root / overlay['contract']['path'])
        result = check_overlay(overlay, contract, expected, root)
    return {**result, 'overlay': io.evidence_ref(path, root), 'contract': overlay['contract'],
            'phase3_identity': expected}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--overlay', type=Path)
    p.add_argument('--batch', type=Path)
    p.add_argument('--label')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    value = precheck(a.overlay) if a.overlay else binding(a.batch, a.label)
    if a.output:
        if a.output.exists():
            raise ValueError('Do not overwrite a prior review')
        io.write_json(a.output, value)
    print(value if not a.output else {'output': str(a.output), 'task_key': value['task_key']})

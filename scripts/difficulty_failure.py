"""Require concrete same-submission contract evidence for direct audit failures."""
from pathlib import Path
from dbt_bench import io
from difficulty_submission_v2 import verify_capture


def same_ref(left, right, root):
    return (left.get('sha256') == right.get('sha256') and
            (Path(root) / left['path']).resolve() == (Path(root) / right['path']).resolve())


def validate_failure(receipt, score, root):
    root = Path(root)
    if (receipt.get('evidence_kind') != 'direct_contract_failure' or receipt.get('rebuild_result') is not None
            or type(score.get('reward')) not in (int, float) or score['reward'] != 0
            or score.get('status') != 'scored'):
        raise ValueError('Direct contract failure may only certify an actual audit0')
    proof_ref = receipt.get('failure_evaluation_ref')
    if not proof_ref or not io.evidence_ok(proof_ref, root):
        raise ValueError('Missing direct failure evaluation')
    proof = io.read_json(root / proof_ref['path'])
    for field in ('task_key', 'task_version', 'original_result', 'qualified_review', 'checker'):
        if proof.get(field) != receipt[field]:
            raise ValueError('Failure proof belongs to another submission/checker: ' + field)
    if (proof.get('evaluation_kind') != 'direct_contract_failure' or proof.get('complete_capture') is not True
            or proof.get('model_requests') != 0 or type(proof.get('reward')) not in (int, float)
            or proof.get('reward') != 0 or proof.get('status') != 'scored'
            or proof.get('checker_result') != receipt['audit_result']):
        raise ValueError('Failure evaluation does not bind the actual checker outcome')
    rules = proof.get('rule_ids')
    if not isinstance(rules, list) or not rules or any(not isinstance(x, str) or not x.strip() for x in rules):
        raise ValueError('Direct failure must identify the violated reviewed requirements')
    for field in ('rule_evidence', 'observations'):
        refs = proof.get(field)
        if not isinstance(refs, list) or not refs or any(not io.evidence_ok(r, root) for r in refs):
            raise ValueError('Missing or changed failure evidence: ' + field)
    trial = (root / receipt['original_result']['path']).parent
    verify_capture(trial, receipt['task_key'])
    actual_capture = io.evidence_ref(trial / 'artifacts/difficulty-submission/capture.json', root)
    if not same_ref(proof['capture'], actual_capture, root):
        raise ValueError('Failure evaluation does not use the original complete capture')
    return proof_ref

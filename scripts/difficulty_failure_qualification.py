"""Check additive failure-evaluator provenance without replacing frozen scores."""
from pathlib import Path
from dbt_bench import io
from difficulty_failure import same_ref

CONTROLS = {'healthy_submission_rejected_as_failure', 'incomplete_capture_rejected', 'contract_failure_detected'}


def validate(receipt, qualified, overlay, root):
    root = Path(root)
    original = receipt.get('qualified_checker')
    if not original or not any(same_ref(original, r, root) for r in overlay['code_refs']):
        raise ValueError('Direct evaluator lacks a qualified original checker binding')
    reference = receipt.get('failure_evaluator_validation_ref')
    if not reference or not io.evidence_ok(reference, root):
        raise ValueError('Direct evaluator has no intact control evidence')
    result = io.read_json(root / reference['path'])
    if (result.get('evaluator') != receipt['checker'] or result.get('model_requests') != 0
            or result.get('fixture_only') is not True):
        raise ValueError('Failure evaluator controls use a different version or purpose')
    checks = result.get('checks', {})
    if not CONTROLS <= set(checks):
        raise ValueError('Failure evaluator lacks positive/negative/incomplete controls')
    for name in CONTROLS:
        row = checks[name]
        refs = row.get('evidence')
        if row.get('status') != 'pass' or not refs or any(not io.evidence_ok(r, root) for r in refs):
            raise ValueError('Unexecuted or changed evaluator control: ' + name)
    proof = io.read_json(root / receipt['failure_evaluation_ref']['path'])
    allowed = [qualified['audit']['overlay'], *overlay['code_refs']]
    if not any(same_ref(r, bound, root) for r in proof['rule_evidence'] for bound in allowed):
        raise ValueError('Direct failure requirements are not bound to the qualified contract/checker')
    return reference

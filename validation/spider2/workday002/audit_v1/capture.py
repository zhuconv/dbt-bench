"""Bind offline audit exports to the exact pre-native case capture."""
from pathlib import Path
from dbt_bench.io import evidence_ok, file_hash, read_json, tree_hash


def verify_capture(root, trial):
    root = Path(root)
    for key in ['result', 'case', 'independent_proof']:
        if not evidence_ok(trial.get(key), root):
            raise ValueError('Missing or changed original ' + key)
    case_path = root / trial['case']['path']
    if tree_hash(case_path.parent) != trial['case_hash']:
        raise ValueError('Prepared case payload changed')
    case = read_json(case_path)
    for name, digest in case['helpers'].items():
        if file_hash(case_path.parent / name) != digest:
            raise ValueError('Executed helper differs from prepared version')
    result_path = root / trial['result']['path']
    # Trial config is used only to bind its prepared task, never its reward.
    task = Path(read_json(result_path)['config']['task']['path'])
    if tree_hash(task) != trial['validation_copy_hash']:
        raise ValueError('Executed validation task changed')
    proof = read_json(root / trial['independent_proof']['path'])
    if proof['mode'] != trial['mode'] or proof['data_scope'] != trial['data_scope']:
        raise ValueError('Capture identity mismatch')
    agent = result_path.parent / 'agent'
    for field in ['input_artifacts', 'submission_artifacts']:
        if not proof[field]:
            raise ValueError('Empty captured artifact inventory')
        for reference in proof[field]:
            path = reference['path']
            if not path.startswith('/logs/agent/'):
                raise ValueError('Unexpected captured artifact path')
            local = (agent / path.removeprefix('/logs/agent/')).resolve()
            if not local.is_relative_to(agent.resolve()) or file_hash(local) != reference['sha256']:
                raise ValueError('Pre-native captured export changed')
    # Only artifact bindings are consumed; validity/outcome fields are not used
    # by the scoring function and cannot determine its answer.

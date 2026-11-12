"""Bind retained exports to the original pre-native proof and task version."""
from pathlib import Path
from dbt_bench.io import evidence_ok, file_hash, read_json, tree_hash


def exported(agent, reference, phase=None):
    prefix = '/logs/agent/' + (phase + '/' if phase else '')
    if not reference['path'].startswith(prefix):
        raise ValueError('Undeclared capture path')
    path = (agent / reference['path'].removeprefix('/logs/agent/')).resolve()
    if not path.is_relative_to(agent.resolve()) or not path.is_file() or file_hash(path) != reference['sha256']:
        raise ValueError('Original captured export changed')
    return path


def verify(root, batch, trial):
    for name in ['result', 'independent_proof']:
        if not evidence_ok(trial[name], root):
            raise ValueError('Retained result/proof changed')
    manifest = read_json(batch / 'manifest.json')
    entry = manifest['tasks'][trial['task_label']]
    raw = read_json(root / trial['result']['path'])
    if Path(raw['config']['task']['path']) != batch / 'tasks' / trial['task_label']:
        raise ValueError('Executed validation task path differs')
    if tree_hash(batch / 'tasks' / trial['task_label']) != entry['validation_copy_hash']:
        raise ValueError('Executed task copy changed')
    if not evidence_ok(entry['case'], root):
        raise ValueError('Case metadata changed')
    case_dir = (root / entry['case']['path']).parent
    if tree_hash(case_dir) != entry['case_hash']:
        raise ValueError('Original case payload changed')
    case = read_json(case_dir / 'case.json')
    for name, digest in case['helpers'].items():
        if file_hash(case_dir / name) != digest:
            raise ValueError('Executed helper changed')
    agent = (root / trial['result']['path']).parent / 'agent'
    proof = read_json(root / trial['independent_proof']['path'])
    if proof['case'] != trial['mode'] or proof['data_scope'] != trial['data_scope']:
        raise ValueError('Case/proof identity differs')
    for field in ['input_artifacts', 'submission_artifacts']:
        for reference in proof[field]:
            if reference['path'] == '/app/dbt_models_duckdb/models/marts/attribution_report.sql':
                if file_hash(agent / 'after-case/models/marts/attribution_report.sql') != reference['sha256']:
                    raise ValueError('Final model differs from original proof')
            else:
                exported(agent, reference)
    phases = ['before-case'] + proof['phases'] if 'phases' in proof else ['original-output', 'before-case', 'after-case']
    for phase in phases:
        snapshot = read_json(agent / phase / 'snapshot.json')
        for reference in snapshot['files']:
            exported(agent, reference, phase)
        if 'model_present' in snapshot:
            model = agent / phase / 'models/marts/attribution_report.sql'
            if model.exists() != snapshot['model_present'] or (file_hash(model) if model.exists() else None) != snapshot['model_sha256']:
                raise ValueError('Model presence/hash differs from original capture')
    return agent, proof

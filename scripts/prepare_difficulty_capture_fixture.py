"""Prepare native oracle/nop capture fixtures from an intact production task.

No model is called. Each resulting manifest is explicitly non-measurement,
and a difficulty GPU runner must reject it by purpose.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
from dbt_bench import io
from dbt_bench.prepare import check_harbor


def prepare(run, output, agent):
    run, output = Path(run), Path(output)
    if agent not in ('oracle', 'nop') or output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Select a fresh SCRATCH fixture and native oracle/nop agent')
    original = io.read_json(run/'manifest.json'); identity = deepcopy(original['identity'])
    config = io.read_json(run/'config.yaml')
    task = Path(config['tasks'][0]['path'])
    if (original['experiment_id'] != io.digest(identity) or config != identity['config']
            or original['config_hash'] != io.file_hash(run/'config.yaml')
            or io.tree_hash(task) != identity['actual_task_hash']):
        raise ValueError('Production fixture source changed')
    output.mkdir(parents=True)
    config.update(agents=[{'name': agent}], n_attempts=1, n_concurrent_trials=1,
                  job_name=output.name, jobs_dir=str(output/'jobs'), retry={'max_retries': 0})
    config = check_harbor(config, [task])
    identity.update(config=config, purpose='audit_conformance_only',
                    prepared_source=io.evidence_ref(run/'manifest.json', ROOT),
                    fixture_agent=agent, fixture_only=True, model_requests=0)
    io.write_json(output/'config.yaml', config)
    io.write_json(output/'manifest.json', {**original, 'identity': identity,
        'experiment_id': io.digest(identity), 'config_hash': io.file_hash(output/'config.yaml'),
        'planned': [{'task_key': identity['task_key'], 'fixture': agent}],
        'fixture_only': True, 'model_requests': 0, 'training_prohibited': True,
        'builder': io.evidence_ref(Path(__file__), ROOT)})
    return {'task_key': identity['task_key'], 'agent': agent, 'fixture_only': True,
            'config': str(output/'config.yaml'), 'model_requests': 0}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--agent', choices=['oracle', 'nop'], required=True)
    a = p.parse_args(); print(prepare(a.run, a.output, a.agent))

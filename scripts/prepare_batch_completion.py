#!/usr/bin/env python3
"""Prepare missing terminal slots in a new Harbor job; never rewrite old jobs.

Run only after independently confirming that the original job has stopped.
Failed terminal trials count as completed, regardless of their reward.
"""
import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.io import read_json, evidence_ref, write_json


def no_model_agent_key(agent):
    """Allow only the explicitly implemented no-model validation agents."""
    if agent.get('model_name') or agent.get('base_url'):
        raise ValueError('Model configuration is forbidden in validation recovery')
    name, path = agent.get('name'), agent.get('import_path')
    if name in {'oracle', 'nop'} and not path:
        return ('name', name)
    if name is None and path in {
        'native_mechanical_agent:NativeMechanicalAgent',
        'spider_environment_agent:SpiderEnvironmentAgent',
        'initial_database_agent:InitialDatabaseAgent',
    }:
        return ('import_path', path)
    raise ValueError('Recovery accepts only known no-model agents')


def validate_completion_config(original, resumed):
    allowed = {'tasks', 'n_attempts', 'job_name', 'jobs_dir'}
    if {k: v for k, v in original.items() if k not in allowed} != {
            k: v for k, v in resumed.items() if k not in allowed}:
        raise ValueError('Recovery changed execution configuration')
    if resumed['n_attempts'] != 1:
        raise ValueError('Recovery must fill individual missing slots')
    originals = {t['path']: t for t in original['tasks']}
    paths = [t['path'] for t in resumed['tasks']]
    if not paths or len(paths) != len(set(paths)) or any(originals.get(t['path']) != t for t in resumed['tasks']):
        raise ValueError('Recovery changed task identity or duplicated a task')
    no_model_agent_key(resumed['agents'][0])


def missing_slots(config, results):
    agents = config['agents']
    if len(agents) != 1:
        raise ValueError('Completion utility accepts a single no-model agent only')
    agent_key = no_model_agent_key(agents[0])
    paths = [t['path'] for t in config['tasks']]
    if len(paths) != len(set(paths)) or config['n_attempts'] < 1:
        raise ValueError('Duplicate tasks or invalid attempt count')
    expected = Counter({path: config['n_attempts'] for path in paths})
    observed = Counter()
    ids = set()
    for result in results:
        if result['id'] in ids:
            raise ValueError('Duplicate trial ID')
        ids.add(result['id'])
        if not result.get('finished_at'):
            raise ValueError('Nonterminal result requires manual review')
        if no_model_agent_key(result['config']['agent']) != agent_key:
            raise ValueError('Unexpected agent')
        observed[result['config']['task']['path']] += 1
    if observed - expected:
        raise ValueError('Unexpected or excess terminal trials')
    return expected - observed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--stopped-evidence', type=Path, required=True)
    a = p.parse_args()
    cfg = read_json(a.batch / 'config.yaml')
    files = [p for p in (a.batch / 'jobs').rglob('result.json') if 'trial_name' in read_json(p)]
    missing = missing_slots(cfg, [read_json(p) for p in files])
    if not missing:
        raise ValueError('No missing slots')
    a.output.mkdir(parents=True, exist_ok=False)
    plans = []
    for attempt in range(max(missing.values())):
        tasks = [t for t in cfg['tasks'] if missing[t['path']] > attempt]
        name = a.output.name + f'-part{attempt + 1}'
        new = {**cfg, 'tasks': tasks, 'n_attempts': 1, 'job_name': name,
               'jobs_dir': str(a.output.resolve() / 'jobs')}
        validate_completion_config(cfg, new)
        path = a.output / f'config-{attempt + 1}.yaml'
        write_json(path, new)
        plans.append({'config': evidence_ref(path, ROOT), 'planned_trials': len(tasks)})
    write_json(a.output / 'completion.json', {
        'schema_version': 1, 'original_config': evidence_ref(a.batch / 'config.yaml', ROOT),
        'original_manifest': evidence_ref(a.batch / 'manifest.json', ROOT),
        'retained_results': [evidence_ref(f, ROOT) for f in files],
        'stopped_evidence': evidence_ref(a.stopped_evidence, ROOT),
        'missing_slots': dict(missing), 'plans': plans, 'new_model_requests': 0,
        'policy': 'All terminal outcomes retained. New jobs fill missing slots; interrupted artifacts remain untouched.'})
    print({'retained': len(files), 'missing': sum(missing.values()), 'configs': plans})


if __name__ == '__main__':
    main()

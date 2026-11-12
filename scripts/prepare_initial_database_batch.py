#!/usr/bin/env python3
"""Group initial DB probes by exact image/environment and bind each task."""
import argparse
from collections import defaultdict
from pathlib import Path
import shutil
import tomllib
from review_tools import ROOT, cached_hashes
from dbt_bench import io
from dbt_bench.backends import configure_backend, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.prepare import check_harbor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--include-spider', action='store_true', help='Use only after the explicit Spider environment/review resume')
    a = p.parse_args()
    out = a.output.resolve()
    assert not out.exists() and str(out).startswith('/scratch/')
    specs = {}
    for source in a.spec:
        value = io.read_json(source)
        entries = value['tasks'] if 'tasks' in value else list(value.values())
        for spec in entries:
            key = spec['task_key']
            assert key not in specs
            specs[key] = spec
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    if any(rows[key]['source'] == 'spider2' for key in specs):
        pause = io.read_json(ROOT / 'artifacts/validation/phase1-review-v1/spider-pause-20261106.json')
        assert a.include_spider and pause.get('pause_semantic_agents') is False
        assert pause.get('user_resume') and io.evidence_ok(pause['user_resume'], ROOT)
    out.mkdir(parents=True)
    (out / 'tools').mkdir()
    for name in ('native_mechanical_agent.py', 'initial_database_agent.py', 'initial_database_probe.py'):
        shutil.copyfile(ROOT / 'scripts' / name, out / 'tools' / name)
    config = io.read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, 'apptainer')
    bundle = ROOT / '.cache/qwen-runtime/locked-v1/bundle.json'
    config.update(job_name=out.name, jobs_dir=str(out / 'jobs'), n_attempts=1, n_concurrent_trials=4,
        agents=[{'import_path': 'initial_database_agent:InitialDatabaseAgent', 'env': {'DB_TYPE': 'duckdb'},
                 'kwargs': {'installation_bundle': str(bundle.resolve()), 'installation_bundle_sha256': io.file_hash(bundle)}}],
        verifier={'disable': True}, tasks=[])
    groups = defaultdict(list)
    entries = {}
    with cached_hashes():
        for key, spec in specs.items():
            row = rows[key]
            assert row['source'] in ('ade', 'deb', 'spider2')
            assert key != 'spider2__movie_recomm001', 'Isolated broken task'
            bound = resolve_task(ROOT, row, 'apptainer')
            native = tomllib.loads((bound / 'task.toml').read_text())
            binding = io.read_json(ROOT / 'catalog/backends/apptainer' / (key + '.json'))
            token = io.digest({'image': binding['image'], 'environment': native['environment'],
                               'database_paths': spec['database_paths'], 'agent_env': {'DB_TYPE': 'duckdb'}})
            groups[token].append((key, bound, binding))
        for token, members in groups.items():
            key, bound, binding = members[0]
            task = out / 'tasks' / key
            shutil.copytree(bound, task, symlinks=True)
            payload = task / 'mechanical'
            payload.mkdir()
            io.write_json(payload / 'spec.json', {'task_key': key, 'database_paths': specs[key]['database_paths']})
            shutil.copyfile(out / 'tools/initial_database_probe.py', payload / 'initial_database_probe.py')
            config['tasks'].append({'path': str(task)})
            entries[key] = {'group_identity': token, 'task_hash': io.tree_hash(task), 'image': binding['image'],
                'task_bindings': [{'task_key': member, 'binding': io.evidence_ref(ROOT / 'catalog/backends/apptainer' / (member + '.json'), ROOT),
                                  'review_specification': specs[member]} for member, _, _ in members]}
    io.write_json(out / 'config.yaml', check_harbor(config, [Path(t['path']) for t in config['tasks']]))
    io.write_json(out / 'manifest.json', {'schema_version': 1, 'tasks': entries, 'candidate_count': len(specs),
        'planned_trials': len(entries), 'config': io.evidence_ref(out / 'config.yaml', ROOT),
        'specifications': [io.evidence_ref(s, ROOT) for s in a.spec], 'model_requests': 0,
        'tools': [io.evidence_ref(f, ROOT) for f in sorted((out / 'tools').glob('*.py'))],
        'preparer': io.evidence_ref(Path(__file__), ROOT),
        'scope': 'Actual initial Qwen-shell DB metadata; reuse only exact image, native environment config and DB paths.'})
    print({'tasks': len(specs), 'shared_environment_groups': len(entries)})


if __name__ == '__main__':
    main()

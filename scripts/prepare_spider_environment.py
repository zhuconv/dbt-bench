#!/usr/bin/env python3
"""Prepare all 55 unmodified-project probes as native Harbor trials."""
import argparse
from pathlib import Path
import shutil
from review_tools import ROOT, cached_hashes
from dbt_bench import io
from dbt_bench.backends import configure_backend, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.prepare import check_harbor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--task', action='append')
    p.add_argument('--concurrent', type=int, default=6)
    a = p.parse_args()
    out = a.output.resolve()
    assert str(out).startswith('/scratch/') and not out.exists()
    rows = [r for r in load_catalog(ROOT) if r['source'] == 'spider2' and (not a.task or r['task_key'] in a.task)]
    assert len(rows) == (len(set(a.task)) if a.task else 55)
    out.mkdir(parents=True)
    (out / 'tools').mkdir()
    names = ('native_mechanical_agent.py', 'spider_environment_agent.py', 'spider_dbt_probe.py', 'context_probe.py')
    for name in names:
        shutil.copyfile(ROOT / 'scripts' / name, out / 'tools' / name)
    config = io.read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, 'apptainer')
    bundle = ROOT / '.cache/qwen-runtime/locked-v1/bundle.json'
    config.update(job_name=out.name, jobs_dir=str(out / 'jobs'), n_attempts=1, n_concurrent_trials=a.concurrent,
        agents=[{'import_path': 'spider_environment_agent:SpiderEnvironmentAgent', 'kwargs': {
            'installation_bundle': str(bundle.resolve()), 'installation_bundle_sha256': io.file_hash(bundle)}}],
        verifier={'disable': True}, tasks=[])
    entries = {}
    with cached_hashes():
        for row in rows:
            key = row['task_key']
            binding = io.read_json(ROOT / 'catalog/backends/apptainer' / (key + '.json'))
            # Refuse to accidentally probe any historical floating environment.
            provenance = [r for r in binding['evidence'] if r['path'].endswith('/provenance.json')][-1]
            record = io.read_json(ROOT / provenance['path'])
            assert any('pin DuckDB 1.2.2' in s for s in record['reviewed_differences']), key
            bound = resolve_task(ROOT, row, 'apptainer')
            task = out / 'tasks' / key
            shutil.copytree(bound, task, symlinks=True)
            payload = task / 'mechanical'
            payload.mkdir()
            native = ROOT / row['run_path']
            db = io.read_json(native / 'solution/config.json')['result_db']
            io.write_json(payload / 'spec.json', {'task_key': key, 'database_paths': [db], 'reference_kind': 'none'})
            for name in ('spider_dbt_probe.py', 'context_probe.py'):
                shutil.copyfile(out / 'tools' / name, payload / name)
            entries[key] = {'binding': io.evidence_ref(ROOT / 'catalog/backends/apptainer' / (key + '.json'), ROOT),
                'task_hash': io.tree_hash(task), 'native_task_hash': row['adapted_task_hash'],
                'scope': 'Environment check only; includes isolated broken task per explicit 55-task request.'}
            config['tasks'].append({'path': str(task)})
    io.write_json(out / 'config.yaml', check_harbor(config, [Path(t['path']) for t in config['tasks']]))
    io.write_json(out / 'manifest.json', {'schema_version': 1, 'tasks': entries, 'planned_trials': len(rows),
        'config': io.evidence_ref(out / 'config.yaml', ROOT), 'model_requests': 0, 'verifier_disabled': True,
        'scope': 'Actual locked Qwen shell dbt probes; no hidden reference access and no benchmark reward.'})
    print({'tasks': len(rows), 'config': str(out / 'config.yaml')})


if __name__ == '__main__':
    main()

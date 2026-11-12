#!/usr/bin/env python3
"""Prepare one immutable Harbor run directory; never start a model or a job."""
import argparse
from copy import deepcopy
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench import io
from dbt_bench.prepare import check_harbor, emit_config
from submission_declarations import bind_versioned_submission


def finalize_run(root, output, base_path, *, capture=None):
    """Hash the final config, including an explicit job name, exactly once."""
    root, output, base_path = Path(root), Path(output), Path(base_path)
    config = io.read_json(base_path)
    base = io.read_json(base_path.with_suffix('.manifest.json'))
    if io.file_hash(base_path) != base['config_hash'] or config != base['identity']['config']:
        raise ValueError('Generated base config and manifest disagree')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', output.name):
        raise ValueError('Run directory basename must be a simple explicit job name')
    final = output / 'config.yaml'
    manifest_path = output / 'manifest.json'
    if final.exists() or manifest_path.exists():
        raise ValueError('Refuse to replace a prepared run')
    identity = deepcopy(base['identity'])
    config['job_name'] = output.name
    config['jobs_dir'] = str(output / 'jobs')
    if capture is not None:
        spec, binding = capture
        if set(identity['tasks']) != {spec['task_key']}:
            raise ValueError('Capture declaration requires exactly its reviewed task')
        if config.get('artifacts'):
            raise ValueError('Do not overwrite existing artifact declarations')
        snapshot = output / 'submission-spec.json'
        if snapshot.exists():
            raise ValueError('Refuse to replace a capture declaration snapshot')
        io.write_json(snapshot, spec)
        config['artifacts'] = deepcopy(spec['artifacts'])
        identity['submission_capture'] = {**binding, 'spec': io.evidence_ref(snapshot, root)}
    config = check_harbor(config)
    identity['config'] = config
    identity['configuration_identity_version'] = 'explicit-job-name-v1'
    identity['configuration_builder'] = io.evidence_ref(Path(__file__), ROOT)
    experiment_id = io.digest(identity)
    # Nothing in config/identity may change after this point. In particular the
    # job name must not be derived from a digest that includes that same name.
    io.write_json(final, config)
    manifest = {
        'experiment_id': experiment_id, 'identity': identity,
        'config_hash': io.file_hash(final), 'runtime_schema_checked': True,
        'planned': base['planned'],
        'preparation': {
            'base_config': io.evidence_ref(base_path, root),
            'base_manifest': io.evidence_ref(base_path.with_suffix('.manifest.json'), root),
            'base_identity_self_consistent': io.digest(base['identity']) == base['experiment_id'],
            'scope': 'Original generator output retained; only the final manifest identifies this run',
        },
    }
    io.write_json(manifest_path, manifest)
    return manifest


def prepare_run(root, output, *, source, model, endpoint, deployment, tasks,
                backend='docker', attempts=1, concurrent=1, submission_spec=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not tasks or len(tasks) != len(set(tasks)):
        raise ValueError('Choose distinct explicit task IDs')
    if output.exists():
        raise ValueError('Run directory already exists; choose a new run name')
    capture = None
    if submission_spec is not None:
        if len(tasks) != 1:
            raise ValueError('A capture declaration requires one task')
        capture = bind_versioned_submission(root, {}, tasks[0], submission_spec)
    output.mkdir(parents=True)
    base = emit_config(root, source, model, endpoint, deployment,
                       output=output / 'base-config.yaml', tasks=tasks, backend=backend,
                       attempts=attempts, concurrent=concurrent)
    return finalize_run(root, output, base, capture=capture)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', choices=['ade', 'spider2', 'deb'], required=True)
    p.add_argument('--task', action='append', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--endpoint', required=True)
    p.add_argument('--deployment', required=True)
    p.add_argument('--backend', choices=['docker', 'apptainer'], default='docker')
    p.add_argument('--attempts', type=int, default=1)
    p.add_argument('--concurrent', type=int, default=1)
    p.add_argument('--submission-spec', type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    result = prepare_run(ROOT, a.output_dir, source=a.source, tasks=a.task,
                         model=a.model, endpoint=a.endpoint, deployment=a.deployment,
                         backend=a.backend, attempts=a.attempts, concurrent=a.concurrent,
                         submission_spec=a.submission_spec)
    print({'config': str(a.output_dir.resolve() / 'config.yaml'),
           'manifest': str(a.output_dir.resolve() / 'manifest.json'),
           'experiment_id': result['experiment_id'], 'model_requests': 0})


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Prepare explicit task copies/config for Harbor's native reference/nop job.

Harbor owns scheduling and execution. This command only freezes the plan and
inputs; run harbor run -c CONFIG, then reconcile_validation_batch.py.
"""
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import binding_identity, configure_backend, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, read_json, tree_hash, write_json
from dbt_bench.prepare import check_harbor
from dbt_bench.submission import bind_submission
from submission_declarations import bind_versioned_submission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', action='append', required=True)
    parser.add_argument('--backend', choices=['docker', 'apptainer'], default='docker')
    parser.add_argument('--concurrent', type=int, default=4)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--capture-submission', action='store_true', help='Use the reviewed single-task native artifact declaration')
    parser.add_argument('--submission-spec', type=Path, help='Explicit versioned declaration; requires --capture-submission')
    args = parser.parse_args()
    if len(args.task) != len(set(args.task)) or args.concurrent < 1:
        raise ValueError('Use distinct task IDs and positive concurrency')
    capture_config = {}
    reviewed_capture = None
    if args.submission_spec and not args.capture_submission:
        raise ValueError('--submission-spec requires --capture-submission')
    if args.capture_submission:
        if len(args.task) != 1:
            raise ValueError('Submission capture currently requires one explicitly reviewed task per job')
        if args.submission_spec:
            reviewed_capture = bind_versioned_submission(ROOT, capture_config, args.task[0], args.submission_spec)
        else:
            reviewed_capture = bind_submission(ROOT, capture_config, args.task[0])
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    if set(args.task) - set(rows):
        raise ValueError('Unknown task IDs: ' + str(sorted(set(args.task) - set(rows))))
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    snapshots = out / 'input-snapshots'
    snapshots.mkdir()
    refs = {}
    for label, source in [('catalog', ROOT / 'catalog/tasks.jsonl'),
                          ('source_lock', ROOT / 'sources.lock.yaml'),
                          ('runtime_lock', ROOT / 'runtime/uv.lock'),
                          ('preparation_script', Path(__file__))]:
        dest = snapshots / source.name
        shutil.copy2(source, dest)
        refs[label] = evidence_ref(dest, ROOT)
    config = read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, args.backend)
    config.update(job_name=out.name, jobs_dir=str(out / 'jobs'),
                  n_concurrent_trials=args.concurrent, n_attempts=2, tasks=[])
    if {a['name'] for a in config['agents']} != {'oracle', 'nop'} or config['retry']['max_retries'] != 0:
        raise ValueError('Expected native oracle/nop and no retries in protocol configuration')
    task_records = {}
    planned = []
    copies = []
    for key in args.task:
        row = rows[key]
        source = resolve_task(ROOT, row, args.backend)
        copied = out / 'tasks' / key
        shutil.copytree(source, copied, symlinks=True)
        config['tasks'].append({'path': str(copied)})
        copies.append(copied)
        task_records[key] = {'original_task_hash': row['adapted_task_hash'],
                             'validation_copy_hash': tree_hash(copied),
                             'backend_binding': binding_identity(ROOT, row, args.backend)}
        planned.extend({'task_key': key, 'agent': agent, 'attempt': attempt}
                       for agent in ('oracle', 'nop') for attempt in (1, 2))
    capture = None
    if args.capture_submission:
        if config.get('artifacts'):
            raise ValueError('Do not overwrite existing artifact declarations')
        spec, binding = reviewed_capture
        config['artifacts'] = capture_config['artifacts']
        write_json(snapshots / 'submission-spec.json', spec)
        capture = {**binding, 'snapshot': evidence_ref(snapshots / 'submission-spec.json', ROOT)}
    config = check_harbor(config, copies)
    write_json(out / 'config.yaml', config)
    write_json(out / 'manifest.json', {'schema_version': 1, 'kind': 'native_reference_and_nop_validation',
        'backend': args.backend, **refs, 'config': evidence_ref(out / 'config.yaml', ROOT),
        'tasks': task_records, 'planned': planned, 'qwen_smoke': False, 'baseline': False,
        'submission_capture': capture,
        'scope': 'Raw native reference/nop outcomes; no automatic solvability or semantic certification'})
    print({'tasks': len(copies), 'trials': len(planned), 'config': str(out / 'config.yaml')})


if __name__ == '__main__':
    main()

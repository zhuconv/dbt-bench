"""Prepare isolated native Harbor DQ cases; Harbor owns execution/scheduling."""
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import binding_identity, configure_backend, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, file_hash, read_json, tree_hash, write_json
from dbt_bench.prepare import check_harbor
from dbt_bench.validation import _wrap_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--case', action='append', help='Explicit case directory name; default all')
    parser.add_argument('--concurrent', type=int, default=3)
    parser.add_argument('--case-version', choices=['v1','v2'], default='v2')
    args = parser.parse_args()
    if args.concurrent < 1:
        raise ValueError('Positive concurrency required')
    source = Path(__file__).parent / 'cases'
    if args.case_version == 'v2':
        source = source / 'v2'
    selected = args.case or [p.parent.name for p in sorted(source.glob('*/case.json'))]
    if len(selected) != len(set(selected)) or any('/' in s or not (source / s / 'case.json').is_file() for s in selected):
        raise ValueError('Select distinct existing case names')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    key = 'deb__dbt-dq-macro-enforcement'
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == key)
    config = read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, 'apptainer')
    config.update(job_name=out.name, jobs_dir=str(out / 'jobs'), n_concurrent_trials=args.concurrent,
                  n_attempts=1, agents=[{'name': 'oracle', 'env': {'DB_TYPE': 'duckdb'}}], tasks=[])
    records, planned = {}, []
    for mode in selected:
        path = source / mode / 'case.json'
        case = read_json(path)
        assert row['adapted_task_hash'] == case['original_task_hash']
        for name, field in [('case_engine.py', 'engine_sha256'), ('independent.py', 'independent_sha256')]:
            assert file_hash(path.parent / name) == case[field]
        for repetition in range(1, 3 if mode == 'reference' else 2):
            label = f'{key}--{mode}--{repetition}'
            copied = out / 'tasks' / label
            shutil.copytree(resolve_task(ROOT, row, 'apptainer'), copied, symlinks=True)
            _wrap_case(copied, path.parent, case)
            assert file_hash(copied / 'tests/native-test.sh') == file_hash(ROOT / row['run_path'] / 'tests/test.sh')
            assert file_hash(copied / 'tests/test_outputs.py') == file_hash(ROOT / row['run_path'] / 'tests/test_outputs.py')
            config['tasks'].append({'path': str(copied)})
            records[label] = {'task_key': key, 'case_mode': mode, 'repetition': repetition,
                              'original_task_hash': row['adapted_task_hash'], 'validation_copy_hash': tree_hash(copied),
                              'backend_binding': binding_identity(ROOT, row, 'apptainer'),
                              'case': evidence_ref(path, ROOT), 'case_payload_hash': tree_hash(path.parent),
                              'native_verifier': evidence_ref(copied / 'tests/native-test.sh', ROOT)}
            planned.append({'task_key': key, 'task_label': label, 'case_mode': mode,
                            'reference_repetition': repetition, 'agent': 'oracle', 'attempt': 1})
    config = check_harbor(config, [Path(t['path']) for t in config['tasks']])
    write_json(out / 'config.yaml', config)
    for name, path in [('sources.lock.yaml', ROOT / 'sources.lock.yaml'), ('runtime-uv.lock', ROOT / 'runtime/uv.lock'),
                       ('runner.py', Path(__file__))]:
        shutil.copyfile(path, out / name)
    write_json(out / 'manifest.json', {'schema_version': 1, 'kind': 'original_data_native_case_validation',
               'backend': 'apptainer', 'tasks': records, 'config': evidence_ref(out / 'config.yaml', ROOT),
               'source_lock': evidence_ref(out / 'sources.lock.yaml', ROOT),
               'runtime_lock': evidence_ref(out / 'runtime-uv.lock', ROOT), 'planned': planned,
               'qwen_smoke': False, 'automatic_certification': False,
               'scope': 'Declared DQ cases only; remaining flag semantics, negative coverage and actual Qwen context are not certified.'})
    print({'trials': len(planned), 'config': str(out / 'config.yaml')})


if __name__ == '__main__':
    main()

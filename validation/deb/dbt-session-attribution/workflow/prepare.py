"""Prepare original-input workflow cases; Harbor schedules all execution."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[4]
BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'src'), str(BASE.parent / 'semantics')]
from common import PLAN
from dbt_bench.backends import binding_identity, configure_backend, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, file_hash, read_json, tree_hash, tree_manifest, write_json
from dbt_bench.prepare import check_harbor
from dbt_bench.validation import _wrap_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--case', choices=list(PLAN), action='append')
    parser.add_argument('--concurrent', type=int, default=3)
    args = parser.parse_args()
    selected = args.case or list(PLAN)
    assert len(selected) == len(set(selected)) and args.concurrent > 0
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    key = 'deb__dbt-session-attribution'
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == key)
    native = resolve_task(ROOT, row, 'apptainer')
    sql = (native / 'solution/solve.sh').read_text().split("cat > models/marts/attribution_report.sql << 'EOF'\n", 1)[1].split('\nEOF', 1)[0] + '\n'
    model_hash = hashlib.sha256(sql.encode()).hexdigest()
    assert model_hash == 'd57c4c690ec4f97223d481f5b044e120cf4ba85bc22fe0c7e2b4b886af3123cf'
    helpers = {'engine.py': BASE / 'engine.py', 'common.py': BASE / 'common.py',
               'independent.py': BASE.parent / 'semantics/independent.py'}
    for name in ['contract.yaml', 'record.json']:
        shutil.copyfile(BASE.parent / name, out / ('before-' + name))
    write_json(out / 'before-cases.json', tree_manifest(BASE.parent / 'cases'))
    config = read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, 'apptainer')
    config.update(job_name=out.name, jobs_dir=str(out / 'jobs'), n_attempts=1,
                  n_concurrent_trials=args.concurrent, agents=[{'name': 'oracle', 'env': {'DB_TYPE': 'duckdb'}}], tasks=[])
    planned, records = [], {}
    binding = binding_identity(ROOT, row, 'apptainer')
    for mode in selected:
        kind, requirements = PLAN[mode]
        case = {'schema_version': 1, 'mode': mode, 'kind': kind, 'reviewed': True,
                'requirement_ids': requirements, 'original_task_hash': row['adapted_task_hash'],
                'reference_model_sha256': model_hash,
                'pristine_inputs_sha256': 'f2f343c8a75c59759bd3a0cef97426e743c9d7538ec4cc6ccaed4c7493b4a536',
                'independent_derivation': 'Explicit required source path, materialization type, staging inventory, compiled-query row order, schema and repeated-build multiset observations. Output values are separately reconstructed from unchanged public inputs. Native grader is not invoked to define validity.',
                'submission_script': 'submission.sh', 'proof_script': 'proof.sh', 'run_native_reference': True,
                'data_scope': 'original_pinned_inputs', 'helpers': {name: file_hash(p) for name, p in helpers.items()}}
        directory = BASE.parent / 'cases/workflow-v1' / mode
        if directory.exists():
            assert read_json(directory / 'case.json') == case, 'Use a new case version if case code changes'
            assert all(file_hash(directory / name) == sha for name, sha in case['helpers'].items())
        else:
            directory.mkdir(parents=True)
            for name, source in helpers.items(): shutil.copyfile(source, directory / name)
            write_json(directory / 'case.json', case)
            (directory / 'submission.sh').write_text('#!/bin/bash\nset -euo pipefail\npython3 /solution/validation-case/engine.py ' + mode + '\n')
            (directory / 'proof.sh').write_text('#!/bin/bash\nset -euo pipefail\ntest -s "$DBT_BENCH_PROOF"\n')
        for repeat in range(1, 3 if kind == 'witness' else 2):
            label = key + '--' + mode + '--' + str(repeat)
            dest = out / 'tasks' / label
            shutil.copytree(native, dest, symlinks=True)
            _wrap_case(dest, directory, case)
            for original, copied in [('tests/test.sh', 'tests/native-test.sh'), ('tests/test_outputs.py', 'tests/test_outputs.py')]:
                assert file_hash(native / original) == file_hash(dest / copied)
            config['tasks'].append({'path': str(dest)})
            records[label] = {'case': evidence_ref(directory / 'case.json', ROOT), 'case_hash': tree_hash(directory),
                              'validation_copy_hash': tree_hash(dest), 'backend_binding': binding}
            planned.append({'task_label': label, 'task_key': key, 'mode': mode, 'repetition': repeat,
                            'data_scope': 'original_pinned_inputs'})
    config = check_harbor(config, [Path(t['path']) for t in config['tasks']])
    write_json(out / 'config.yaml', config)
    for name, source in [('runner.py', Path(__file__)), ('sources.lock.yaml', ROOT / 'sources.lock.yaml'), ('runtime-uv.lock', ROOT / 'runtime/uv.lock')]:
        shutil.copyfile(source, out / name)
    write_json(out / 'manifest.json', {'schema_version': 1, 'planned': planned, 'tasks': records,
        'config': evidence_ref(out / 'config.yaml', ROOT), 'source_lock': evidence_ref(out / 'sources.lock.yaml', ROOT),
        'qwen_smoke': False, 'scope': 'Original fixed-input workflow cases and repeated-build controls; native verifier unchanged.'})
    print({'planned': len(planned), 'config': str(out / 'config.yaml')})


if __name__ == '__main__': main()

"""Prepare isolated Workday semantic cases; Harbor owns execution and scheduling."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import binding_identity, configure_backend, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, file_hash, read_json, tree_hash, write_json
from dbt_bench.prepare import check_harbor
from dbt_bench.validation import _wrap_case
from independent import encoded
from task_copy import copy_task

PLAN = {
    'fixture-reference': ('witness', ['R-family-details', 'R-output-grain']),
    'fixture-deleted-profile-link': ('negative', ['R-family-details']),
    'fixture-deleted-global-bridge': ('negative', ['R-family-details']),
    'fixture-deleted-group': ('negative', ['R-family-details']),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--case', action='append', choices=list(PLAN))
    parser.add_argument('--concurrent', type=int, default=3)
    args = parser.parse_args()
    selected = args.case or list(PLAN)
    if len(set(selected)) != len(selected) or args.concurrent < 1:
        raise ValueError('Duplicate cases or invalid concurrency')
    key = 'spider2__workday002'
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == key)
    native = resolve_task(ROOT, row, 'apptainer')
    binding = binding_identity(ROOT, row, 'apptainer')
    pristine_path = ROOT / 'artifacts/validation/workday-model-source-rebuild-v1/snapshots/initial/inputs.json'
    pristine = read_json(pristine_path)
    input_hash = hashlib.sha256((encoded(pristine) + '\n').encode()).hexdigest()
    captured = ROOT / 'jobs/spider2-qwen-gh-dev-1124050-attempt-2/spider2__workday002__YWRFyUb/artifacts/submission/workspace'
    package_inventory = {p.relative_to(captured).as_posix(): file_hash(p)
                         for p in (captured / 'dbt_packages').rglob('*') if p.is_file()}
    source = ROOT / 'artifacts/validation/workday-semantics-source-v1/upstream'
    if file_hash(source / 'models/workday__job_overview.sql') != 'ea731faecf0e1c5f02dd3530c4fd0ace5bb0bc2c6a80d2841187fb66388c97c6':
        raise ValueError('Upstream reference changed')
    for name in ['docs.md', 'workday.yml']:
        if file_hash(source / 'models' / name) != file_hash(ROOT / row['run_path'] / 'environment/dbt_project/models' / name):
            raise ValueError('Semantic source does not match benchmark project documentation')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    config = read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, 'apptainer')
    config.update(job_name=out.name, jobs_dir=str(out / 'jobs'), n_attempts=1,
                  n_concurrent_trials=args.concurrent, agents=[{'name': 'oracle', 'env': {'DB_TYPE': 'duckdb'}}], tasks=[])
    planned = []
    base = Path(__file__).parent
    for mode in selected:
        kind, requirements = PLAN[mode]
        directory = out / 'cases' / mode
        directory.mkdir(parents=True)
        case = {
            'schema_version': 1, 'mode': mode, 'kind': kind, 'reviewed': True,
            'requirement_ids': requirements, 'pristine_inputs_sha256': input_hash,
            'data_scope': 'additional_input_fixture' if mode.startswith('fixture-') else 'original_pinned_inputs',
            'independent_derivation': 'Raw profile/family/global-group traversal with deletion filtering and distinct non-NULL members; fixture expectations also manually enumerated. Native grader/gold never defines validity.',
            'submission_script': 'submission.sh', 'proof_script': 'proof.sh', 'run_native_reference': False,
            'helpers': {name: file_hash(base / name) for name in ['engine.py', 'independent.py', 'fixtures.py']},
            'empty_aggregate_policy': 'Do not distinguish NULL, empty string, or empty list when no members; other core NULLs remain exact. Case evidence only; final representation adjudication remains pending.',
        }
        for name in case['helpers']:
            shutil.copyfile(base / name, directory / name)
        shutil.copyfile(base.parent / 'reconstruction.py', directory / 'reconstruction.py')
        shutil.copyfile(source / 'models/workday__job_overview.sql', directory / 'upstream-reference.sql')
        shutil.copyfile(source / 'LICENSE', directory / 'UPSTREAM-LICENSE')
        for name in ['packages.yml', 'package-lock.yml']:
            shutil.copyfile(captured / name, directory / name)
        shutil.copyfile(captured / 'models/workday__job_overview.sql', directory / 'model-submission.sql')
        write_json(directory / 'dependency-inventory.json', package_inventory)
        write_json(directory / 'case.json', case)
        (directory / 'submission.sh').write_text('#!/bin/bash\nset -euo pipefail\npython3 /solution/validation-case/engine.py\n')
        (directory / 'proof.sh').write_text('#!/bin/bash\nset -euo pipefail\ntest -s "$DBT_BENCH_PROOF"\n')
        for repetition in range(1, 3 if mode.endswith('-reference') else 2):
            label = f'{key}--{mode}--{repetition}'
            dest = out / 'tasks' / label
            copy_task(native, dest)
            _wrap_case(dest, directory, case)
            removed = []
            for path in (dest / 'solution').iterdir():
                if path.name not in {'solve.sh', 'validation-case'}:
                    removed.append({'name': path.name, 'sha256': tree_hash(path) if path.is_dir() else file_hash(path)})
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
            for original in (native / 'tests').rglob('*'):
                if original.is_file():
                    rel = original.relative_to(native / 'tests')
                    changed = Path('native-test.sh') if str(rel) == 'test.sh' else rel
                    if file_hash(original) != file_hash(dest / 'tests' / changed):
                        raise ValueError('Native verifier material changed')
            config['tasks'].append({'path': str(dest)})
            planned.append({'task_label': label, 'task_key': key, 'mode': mode, 'repetition': repetition,
                            'data_scope': case['data_scope'], 'kind': kind,
                            'case': evidence_ref(directory / 'case.json', ROOT), 'case_hash': tree_hash(directory),
                            'validation_copy_hash': tree_hash(dest), 'removed_oracle_material': removed})
    config = check_harbor(config, [Path(t['path']) for t in config['tasks']])
    write_json(out / 'config.yaml', config)
    for name, path in [('sources.lock.yaml', ROOT / 'sources.lock.yaml'), ('runtime-uv.lock', ROOT / 'runtime/uv.lock'),
                       ('runner.py', Path(__file__)), ('prior-contract.json', base.parent / 'contract.yaml'),
                       ('prior-ledger.jsonl', ROOT / 'validation/ledger.jsonl'),
                       ('task_copy.py', base / 'task_copy.py')]:
        shutil.copyfile(path, out / name)
    write_json(out / 'manifest.json', {
        'schema_version': 1, 'planned': planned, 'backend_binding': binding,
        'native_task_hash': row['adapted_task_hash'], 'qwen_smoke': False,
        'scope': 'Case analysis; additional-input fixture rewards are not original benchmark scores. No ledger or audit certification is promoted by this runner.',
        'config': evidence_ref(out / 'config.yaml', ROOT), 'source_lock': evidence_ref(out / 'sources.lock.yaml', ROOT),
        'runtime_lock': evidence_ref(out / 'runtime-uv.lock', ROOT), 'initial_inputs': evidence_ref(pristine_path, ROOT),
        'upstream_fetch': evidence_ref(source.parent / 'fetch.json', ROOT),
        'task_copy_strategy': 'Only immutable environment build inputs are hardlinked. The prebuilt SIF executes them; mutable solution, tests and instruction remain independent copies.',
        'backend_task_path': str(native.relative_to(ROOT)), 'backend_task_hash': tree_hash(native),
    })
    print(json.dumps({'config': str(out / 'config.yaml'), 'planned_trials': len(planned), 'qwen_smoke': False}))


if __name__ == '__main__':
    main()

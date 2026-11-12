"""Prepare reviewed original-data cases with native Harbor and SCRATCH storage."""
from dbt_bench.local_config import expand as _release_expand
import argparse, os, shutil, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import binding_identity, configure_backend, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, read_json, write_json, tree_hash, file_hash
from dbt_bench.prepare import check_harbor
from dbt_bench.validation import _wrap_case

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--case', action='append')
    parser.add_argument('--attempts', type=int, default=2)
    parser.add_argument('--concurrent', type=int, default=3)
    args = parser.parse_args()
    here = Path(__file__).parent
    available = {p.parent.name: p.parent for p in (here / 'cases').glob('*/case.json')}
    selected = args.case or sorted(available)
    if min(args.attempts, args.concurrent) < 1 or not selected or len(set(selected)) != len(selected) or set(selected) - available.keys():
        parser.error('Choose distinct known cases and positive limits')
    output = Path(os.path.abspath(args.output_dir))
    relative = output.relative_to(ROOT / 'artifacts/validation')
    if len(relative.parts) != 1:
        parser.error('Use a new direct child of artifacts/validation')
    destination = Path(_release_expand('${ARTIFACT_ROOT}/validation')) / relative
    if output.exists() or output.is_symlink() or destination.exists() or destination.is_symlink():
        parser.error('Refuse existing output; use a new directory')
    row = next((r for r in load_catalog(ROOT) if r['task_key'] == 'deb__dbt-weekly-sales-growth'))
    for mode in selected:
        case = read_json(available[mode] / 'case.json')
        assert case['reviewed'] and (not case['run_native_reference']) and (case['mode'] == mode)
        assert case['engine_sha256'] == file_hash(available[mode] / 'case_engine.py')
        assert case['independent_sha256'] == file_hash(available[mode] / 'independent.py')
        assert case['sql_models_sha256'] == file_hash(available[mode] / 'sql_models.py')
    bound = resolve_task(ROOT, row, 'apptainer')
    binding = binding_identity(ROOT, row, 'apptainer')
    destination.mkdir(parents=True)
    output.symlink_to(destination, target_is_directory=True)
    config = read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, 'apptainer')
    config.update(job_name=output.name, jobs_dir=str(output / 'jobs'), n_attempts=args.attempts, n_concurrent_trials=args.concurrent, agents=[{'name': 'oracle', 'env': {'DB_TYPE': 'duckdb'}}], tasks=[])
    entries = {}
    for mode in selected:
        case = output / 'cases' / mode
        shutil.copytree(available[mode], case)
        spec = read_json(case / 'case.json')
        task = output / 'tasks' / (row['task_key'] + '--' + mode)
        shutil.copytree(bound, task, symlinks=True)
        shutil.rmtree(task / 'solution')
        (task / 'solution').mkdir()
        _wrap_case(task, case, spec)
        for name in ['case_engine.py', 'independent.py', 'sql_models.py']:
            shutil.copyfile(case / name, task / 'tests' / name)
        shutil.copyfile(here / 'collect.py', task / 'tests/collect.py')
        shutil.copyfile(here / 'collector-wrapper.sh', task / 'tests/test.sh')
        shutil.copyfile(ROOT / 'scripts/context_probe.py', task / 'tests/context_probe.py')
        assert file_hash(task / 'tests/native-test.sh') == file_hash(ROOT / row['run_path'] / 'tests/test.sh')
        entries[task.name] = {'case': evidence_ref(case / 'case.json', ROOT), 'kind': spec['kind'], 'case_mode': mode, 'case_payload_hash': tree_hash(case), 'validation_copy_hash': tree_hash(task), 'native_verifier': evidence_ref(task / 'tests/native-test.sh', ROOT)}
        config['tasks'].append({'path': str(task)})
    write_json(output / 'config.yaml', check_harbor(config, [Path(t['path']) for t in config['tasks']]))
    write_json(output / 'manifest.json', {'schema_version': 1, 'task_key': row['task_key'], 'tasks': entries, 'planned_trials': len(entries) * args.attempts, 'backend_binding': binding, 'config': evidence_ref(output / 'config.yaml', ROOT), 'preparer': evidence_ref(Path(__file__), ROOT), 'source_lock': evidence_ref(ROOT / 'sources.lock.yaml', ROOT), 'runtime_lock': evidence_ref(ROOT / 'runtime/uv.lock', ROOT), 'physical_storage': str(destination), 'model_requests': 0, 'status': 'prepared_not_executed'})
    print({'config': str(output / 'config.yaml'), 'planned_trials': len(entries) * args.attempts})
if __name__ == '__main__':
    main()

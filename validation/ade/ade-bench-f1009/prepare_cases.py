"""Prepare isolated Driver calendar-age validation copies; Harbor executes the generated job."""
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
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--case', action='append', dest='cases')
    parser.add_argument('--attempts', type=int, default=2)
    parser.add_argument('--concurrent', type=int, default=2)
    args = parser.parse_args()
    if min(args.attempts, args.concurrent) < 1:
        parser.error('attempts and concurrent must be positive')
    task_key = 'ade__ade-bench-f1009'
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == task_key)
    case_root = Path(__file__).parent / 'cases'
    available = {p.parent.name: p.parent for p in case_root.glob('*/case.json')}
    selected = sorted(available) if args.cases is None else args.cases
    if not selected or len(selected) != len(set(selected)) or set(selected) - available.keys():
        parser.error('select distinct known case names')
    payloads = []
    for name in selected:
        case = available[name]
        spec = read_json(case / 'case.json')
        if not spec.get('reviewed') or spec.get('run_native_reference'):
            parser.error(f'{name}: requires a reviewed reference-free case')
        payloads.append((name, case, spec))
    bound = resolve_task(ROOT, row, 'apptainer')
    output = args.output_dir.resolve()
    output.relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=False)
    config = read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, 'apptainer')
    config.update(job_name=output.name, jobs_dir=str(output / 'jobs'), n_attempts=args.attempts,
                  n_concurrent_trials=args.concurrent, agents=[{'name': 'oracle', 'env': {'DB_TYPE': 'duckdb'}}], tasks=[])
    entries = {}
    for name, source, spec in payloads:
        case = output / 'cases' / name
        shutil.copytree(source, case)
        task = output / 'tasks' / (task_key + '--' + name)
        shutil.copytree(bound, task, symlinks=True)
        shutil.rmtree(task / 'solution')
        (task / 'solution').mkdir()
        _wrap_case(task, case, spec)
        shutil.copy2(ROOT / 'scripts/context_probe.py', task / 'tests/context_probe.py')
        shutil.copy2(Path(__file__).with_name('collect.py'), task / 'tests/collect.py')
        wrapper = task / 'tests/test.sh'
        text = wrapper.read_text()
        marker = 'exec bash /tests/native-test.sh\n'
        assert text.count(marker) == 1
        text = text.replace(marker, "cd /app\npython3 /tests/context_probe.py --db /app/f1.duckdb --query 'SELECT CURRENT_DATE::VARCHAR' > /logs/verifier/context.json\n" + "bash /tests/native-test.sh\npython3 /tests/collect.py\n")
        wrapper.write_text(text)
        assert sorted(p.name for p in (task / 'solution').iterdir()) == ['solve.sh', 'validation-case']
        assert file_hash(task / 'tests/native-test.sh') == file_hash(ROOT / row['run_path'] / 'tests/test.sh')
        entries[task.name] = {'case': evidence_ref(case / 'case.json', ROOT), 'case_payload_hash': tree_hash(case),
                              'validation_copy_hash': tree_hash(task), 'expected_kind': spec['kind'],
                              'native_verifier': evidence_ref(task / 'tests/native-test.sh', ROOT)}
        config['tasks'].append({'path': str(task)})
    write_json(output / 'config.yaml', check_harbor(config, [Path(t['path']) for t in config['tasks']]))
    write_json(output / 'manifest.json', {'schema_version': 1, 'task_key': task_key,
               'planned_trials': len(selected) * args.attempts, 'tasks': entries,
               'config': evidence_ref(output / 'config.yaml', ROOT), 'backend_binding': binding_identity(ROOT, row, 'apptainer'),
               'preparer': evidence_ref(Path(__file__), ROOT), 'status': 'prepared_not_executed', 'model_requests': 0})
    print(output / 'config.yaml')

if __name__ == '__main__':
    main()

"""Prepare original-input Intercom cases; Harbor owns execution and retries."""
import argparse
from pathlib import Path
import shutil
import sys
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
    if min(args.attempts, args.concurrent) < 1: parser.error('Positive limits required')
    here = Path(__file__).parent; key = 'ade__' + here.name
    task_id = here.name.removeprefix('ade-bench-')
    row = next(row for row in load_catalog(ROOT) if row['task_key'] == key)
    available = {p.parent.name: p.parent for p in (here / 'cases').glob('*/case.json')}
    selected = args.case or sorted(available)
    if not selected or len(selected) != len(set(selected)) or set(selected) - available.keys(): parser.error('Select distinct known cases')
    payloads = []
    for name in selected:
        case = available[name]; spec = read_json(case / 'case.json')
        if not spec.get('reviewed') or spec.get('run_native_reference') or spec.get('task_id') != task_id:
            parser.error('Reviewed reference-free payload for this task required')
        if spec.get('data_scope') == 'additional_input_fixture': parser.error('Use the separately versioned fixture preparer for additional input')
        payloads.append((name, case, spec))
    bound = resolve_task(ROOT, row, 'apptainer')
    output = args.output_dir.resolve(); output.relative_to(ROOT); output.mkdir(parents=True, exist_ok=False)
    config = read_json(ROOT / 'configs/validation.yaml'); configure_backend(config, 'apptainer')
    config.update(job_name=output.name, jobs_dir=str(output / 'jobs'), n_attempts=args.attempts,
                  n_concurrent_trials=args.concurrent, agents=[{'name': 'oracle', 'env': {'DB_TYPE': 'duckdb'}}], tasks=[])
    entries = {}; binding = binding_identity(ROOT, row, 'apptainer')
    for name, source, spec in payloads:
        case = output / 'cases' / name; shutil.copytree(source, case)
        task = output / 'tasks' / (key + '--' + name); shutil.copytree(bound, task, symlinks=True)
        shutil.rmtree(task / 'solution'); (task / 'solution').mkdir(); _wrap_case(task, case, spec)
        shutil.copyfile(ROOT / 'scripts/context_probe.py', task / 'tests/context_probe.py')
        shutil.copyfile(case / 'case_engine.py', task / 'tests/case_engine.py')
        delivery = (case / 'workflow_engine.py').exists()
        shutil.copyfile(here / ('collect_delivery.py' if delivery else 'collect.py'), task / 'tests/collect.py')
        wrapper = task / 'tests/test.sh'; text = wrapper.read_text(); marker = 'exec bash /tests/native-test.sh\n'; assert text.count(marker) == 1
        wrapper.write_text(text.replace(marker, "cd /app\npython3 /tests/context_probe.py --db /app/intercom.duckdb --query 'SELECT 1' > /logs/verifier/context.json\nbash /tests/native-test.sh\npython3 /tests/collect.py\n"))
        assert file_hash(task / 'tests/native-test.sh') == file_hash(ROOT / row['run_path'] / 'tests/test.sh')
        entries[task.name] = {'case': evidence_ref(case / 'case.json', ROOT), 'case_payload_hash': tree_hash(case),
                             'validation_copy_hash': tree_hash(task), 'native_verifier': evidence_ref(task / 'tests/native-test.sh', ROOT),
                             'kind': spec['kind'], 'collector_kind': 'delivery' if delivery else 'outputs'}
        config['tasks'].append({'path': str(task)})
    write_json(output / 'config.yaml', check_harbor(config, [Path(t['path']) for t in config['tasks']]))
    write_json(output / 'manifest.json', {'schema_version': 1, 'task_key': key, 'planned_trials': len(entries) * args.attempts,
               'tasks': entries, 'config': evidence_ref(output / 'config.yaml', ROOT), 'backend_binding': binding,
               'preparer': evidence_ref(Path(__file__), ROOT), 'status': 'prepared_not_executed', 'model_requests': 0})
    print(output / 'config.yaml')


if __name__ == '__main__': main()

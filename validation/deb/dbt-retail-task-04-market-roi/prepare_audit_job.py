"""Prepare a native Harbor validation job with a read-only pre-verifier collector.

The source task/verifier stay untouched. The complete derived task and config,
including collection-only changes, are hashed in the companion manifest.
"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT/'src'))
import argparse
import base64
import json
import shlex
import shutil
import uuid

from dbt_bench.backends import configure_backend, resolve_task, binding_identity
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, tree_hash, write_json
from dbt_bench.prepare import check_harbor
from dbt_bench.validation import _wrap_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path)
    parser.add_argument('--fixture-only', action='store_true',
                        help='Additional input fixture only: disable the original-data native grader and retain null native reward')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    row = next(row for row in load_catalog(ROOT) if row['task_key'] == 'deb__dbt-retail-task-04-market-roi')
    original = resolve_task(ROOT, row, 'apptainer')
    task = directory/'task'/row['task_key']
    shutil.copytree(original, task)
    if args.case:
        _wrap_case(task, args.case.parent, json.loads(args.case.read_text()))
    if args.fixture_only and (not args.case or not json.loads(args.case.read_text()).get('additional_input_fixture')):
        raise ValueError('--fixture-only requires an explicitly declared additional-input case')
    snapshot_dir = '/logs/artifacts/roi-audit-'+uuid.uuid4().hex[:12]
    collector = HERE/'collect_snapshot.py'
    encoded = base64.b64encode(collector.read_bytes()).decode()
    code = "import base64,sys;sys.argv=['roi-collector',"+repr(snapshot_dir)+"];exec(compile(base64.b64decode("+repr(encoded)+"),'roi-collector','exec'))"
    command = 'python3 -c '+shlex.quote(code)
    with (task/'task.toml').open('a') as f:
        f.write('\n[[verifier.collect]]\ncommand = '+json.dumps(command)+'\ntimeout_sec = 90\nuser = "root"\n')
    config = json.loads((ROOT/'configs/validation.yaml').read_text())
    configure_backend(config, 'apptainer')
    config.update(tasks=[{'path': str(task)}], job_name=directory.name, jobs_dir=str(directory/'jobs'))
    # Harbor's native collection happens before shared verification. Capture
    # source files too, so a saved output is not mistaken for a rebuild witness.
    config['artifacts'] = [{'source': '/app/dbt_models_duckdb', 'destination': 'submission/project',
                            'exclude': ['target', 'logs', 'dbt_packages']}]
    if args.case:
        config['agents'] = [{'name': 'oracle', 'env': {'DB_TYPE': 'duckdb'}}]
        config['n_attempts'] = 1
    if args.fixture_only:
        config['verifier']['disable'] = True
    write_json(directory/'config.yaml', check_harbor(config, [task]))
    write_json(directory/'manifest.json', {
        'task_key': row['task_key'], 'backend_binding': binding_identity(ROOT, row, 'apptainer'),
        'source_task_hash': row['adapted_task_hash'], 'derived_task_hash': tree_hash(task),
        'case': evidence_ref(args.case, ROOT) if args.case else None,
        'collector': evidence_ref(collector, ROOT), 'snapshot_container_path': snapshot_dir,
        'config': evidence_ref(directory/'config.yaml', ROOT),
        'native_verifier_hash': tree_hash(ROOT/row['run_path']/'tests'),
        'collection_only': True, 'qwen_smoke': False,
        'additional_input_fixture': args.fixture_only,
        'scope': 'Native Harbor oracle/nop or reviewed case; read-only collection before original verifier. No semantic certification.'})
    print(directory/'config.yaml')


if __name__ == '__main__':
    main()

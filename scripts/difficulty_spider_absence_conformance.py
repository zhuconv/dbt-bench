"""Real disposable-container regression for a wholly absent required DB."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from prepare_difficulty_run import prepare, hook_command
from difficulty_spider_conformance import command
from difficulty_spider_failure import score


def run(output):
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('New SCRATCH fixture directory required')
    output.mkdir(parents=True)
    basis = output / 'basis'
    key = 'spider2__lever001'
    prepare(basis, 'spider2', key, [1])
    manifest = io.read_json(basis / 'manifest.json')
    identity = manifest['identity']
    task = Path(identity['config']['tasks'][0]['path'])
    tests_before = io.tree_hash(task / 'tests')
    task_toml = task / 'task.toml'
    before = task_toml.read_text()
    old_hook = hook_command(basis / 'tools/difficulty_collect.py', identity['capture_spec'])
    shutil.copyfile(ROOT / 'scripts/difficulty_collect_v2.py', basis / 'tools/difficulty_collect_v2.py')
    new_hook = hook_command(basis / 'tools/difficulty_collect_v2.py', identity['capture_spec'])
    if before.count(json.dumps(old_hook)) != 1:
        raise ValueError('Expected exactly the original capture hook')
    task_toml.write_text(before.replace(json.dumps(old_hook), json.dumps(new_hook)))
    solution = task / 'solution/solve.sh'
    solution.write_text("#!/bin/bash\nset -eu\npython3 - <<'PY'\nfrom pathlib import Path\nPath('/workspace/lever.duckdb').unlink()\nPY\n")
    if io.tree_hash(task / 'tests') != tests_before:
        raise ValueError('Native verifier changed')
    identity.update(purpose='audit_conformance_only', actual_task_hash=io.tree_hash(task),
        conformance_case='Only in this disposable fixture: deliberately remove the declared output DB, preserve the original native verifier, use v2 absence-aware capture.')
    manifest.update(experiment_id=io.digest(identity), fixture_only=True)
    io.write_json(basis / 'manifest.json', manifest)
    config = deepcopy(identity['config'])
    config.update(agents=[{'name': 'oracle'}], n_attempts=1, n_concurrent_trials=1,
                  job_name='absence', jobs_dir=str(output / 'jobs'))
    io.write_json(output / 'config.json', config)
    command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(output / 'config.json')], output, 'harbor')
    results = list((output / 'jobs/absence').glob('*/result.json'))
    if len(results) != 1:
        raise ValueError('One terminal actual fixture required')
    receipt = score(basis, results[0].parent, output / 'scoring', fixture=True)
    io.write_json(output / 'summary.json', {'fixture_only': True, 'model_requests': 0,
        'training_prohibited': True, 'audit_reward': receipt['audit_reward'],
        'native_result': io.evidence_ref(results[0], ROOT),
        'receipt': io.evidence_ref(output / 'scoring/receipt.json', ROOT),
        'scope': 'Actual missing required DB in disposable fixture, scored without rebuilding or reading image DB; never a model attempt.'})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    run(args.output)

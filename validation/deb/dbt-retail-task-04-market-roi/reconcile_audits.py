"""Score immutable native pre-verifier snapshots and reconcile the exact plan.

No native reward or trial artifact is changed. A report is evidence for review,
not an automatic audit-checker certification or training candidate export.
"""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import argparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT/'src'))
from dbt_bench.io import evidence_ref, evidence_ok, tree_hash, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    directory = args.job_dir.resolve()
    plan = json.loads((directory/'manifest.json').read_text())
    if not evidence_ok(plan['config'], ROOT):
        raise ValueError('Prepared config changed')
    config = json.loads((directory/'config.yaml').read_text())
    expected = Counter()
    manifests = {}
    for task in config['tasks']:
        path = Path(task['path']).resolve()
        for agent in config['agents']:
            expected[(str(path), agent['name'])] += config['n_attempts']
        entry = next((p for p in plan.get('planned', []) if p['task_path'] == str(path)), None)
        mpath = ROOT/entry['manifest']['path'] if entry else directory/'manifest.json'
        if entry and not evidence_ok(entry['manifest'], ROOT):
            raise ValueError('Prepared task manifest changed')
        manifest = json.loads(mpath.read_text())
        if tree_hash(path) != manifest['derived_task_hash']:
            raise ValueError('Derived task changed before reconciliation: '+str(path))
        manifests[str(path)] = (manifest, entry['case'] if entry else 'reference-nop')
    observed, ids, records, errors = Counter(), set(), [], []
    for p in sorted((directory/'jobs'/config['job_name']).glob('*/result.json')):
        native = json.loads(p.read_text())
        task_path = str(Path(native['config']['task']['path']).resolve())
        agent = native['agent_info']['name']
        observed[(task_path, agent)] += 1
        if native['id'] in ids:
            errors.append('Duplicate trial UUID: '+native['id'])
        ids.add(native['id'])
        if task_path not in manifests:
            errors.append('Unknown task path: '+task_path)
            continue
        manifest, case = manifests[task_path]
        container_path = Path(manifest['snapshot_container_path'])
        snapshot = p.parent/'artifacts'/str(container_path).lstrip('/')/'snapshot.json'
        result = {'trial_id': native['id'], 'trial_result': evidence_ref(p, ROOT), 'case': case, 'agent': agent,
                  'native_rewards': (native.get('verifier_result') or {}).get('rewards'),
                  'native_exception': native.get('exception_info'), 'audit': None}
        if not snapshot.is_file():
            errors.append('Missing pre-verifier snapshot: '+str(snapshot))
        else:
            audit_out = output/p.parent.name/'result.json'
            command = [sys.executable, str(HERE/'audit.py'), '--snapshot', str(snapshot.parent), '--output', str(audit_out)]
            run = subprocess.run(command, text=True, capture_output=True)
            log = output/p.parent.name/'command.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(run.stdout+run.stderr)
            if run.returncode or not audit_out.is_file():
                errors.append('Audit did not score: '+p.parent.name)
            if audit_out.is_file():
                audit = json.loads(audit_out.read_text())
                result['audit'] = {'result': evidence_ref(audit_out, ROOT), 'reward': audit['reward'],
                                   'success': audit['success'], 'checker_version': audit['checker_version'],
                                   'snapshot_verified': audit['snapshot_verified'],
                                   'submission': evidence_ref(snapshot, ROOT)}
        records.append(result)
    if observed != expected:
        errors.append('Planned/observed task-agent repeat multiset mismatch')
    report = {'native_rewards_modified': False, 'checker_certified': False, 'qwen_smoke': False,
              'scope': 'Plan reconciliation and audit execution only; review requirement coverage separately',
              'config': evidence_ref(directory/'config.yaml', ROOT),
              'manifest': evidence_ref(directory/'manifest.json', ROOT),
              'planned': sum(expected.values()), 'observed': sum(observed.values()), 'complete': not errors,
              'errors': errors, 'trials': records}
    write_json(output/'summary.json', report)
    print(json.dumps({k: report[k] for k in ('planned', 'observed', 'complete', 'errors')}))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Reconcile planned difficulty slots, raw Harbor trials and actual audit receipts.

This is a read-only report. It neither schedules requests nor retries a trial.
Missing scores remain unknown; fixture/reference executions cannot be measured.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from difficulty_submission import verify_capture


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def load(ref, root=ROOT):
    if not io.evidence_ok(ref, root):
        raise ValueError('Changed evidence: ' + ref['path'])
    return io.read_json(root / ref['path'])


def joint_outcome(native, audit, *, execution_valid):
    """Unknown evaluation is kept distinct even when native already failed."""
    if not execution_valid or native is None or audit is None:
        return None
    return native == 1 and audit == 1


def binary_reward(value):
    # bool is not a recorded numeric reward; other reward maps need explicit rules.
    return value if type(value) in (int, float) and value in (0, 1) else None


def measured_agent(raw, expected):
    actual = raw['config']['agent']
    for field in ('name', 'model_name', 'base_url', 'kwargs'):
        if actual.get(field) != expected.get(field):
            raise ValueError('Actual agent differs from the planned native Qwen configuration: ' + field)
    if actual.get('import_path') or actual['name'] != 'qwen-coder':
        raise ValueError('Reference/custom execution is not a model attempt')


def verify_adapter_dependencies(receipt, root=ROOT):
    dependencies = receipt.get('adapter_dependencies')
    if not dependencies:
        if receipt['task_key'].startswith('deb__'):
            raise ValueError('DEB actual audit requires a complete adapter dependency manifest')
        return
    manifest = load(dependencies, root)
    code = manifest['code_refs']
    checker = manifest['checker_dependencies']
    if not code or not checker:
        raise ValueError('Audit adapter dependency lists are empty')
    for item in [*code, *checker, manifest['image']]:
        if not io.evidence_ok(item, root):
            raise ValueError('Audit adapter dependency changed: ' + item['path'])


def audit_for_trial(receipt_path, raw_ref, key, version, expected_review):
    receipt = io.read_json(receipt_path)
    if (receipt.get('fixture_only') is not False or receipt.get('training_prohibited') is not True
            or receipt.get('original_result') != raw_ref or receipt.get('task_key') != key
            or receipt.get('task_version') != version or receipt.get('qualified_review') != expected_review):
        raise ValueError('Audit is a fixture, wrong task version, wrong submission or different checker qualification')
    qualified = load(expected_review)
    if qualified['audit']['conclusion'] != 'validated':
        raise ValueError('Actual submission audit is not qualified')
    overlay = load(qualified['audit']['overlay'])
    for dependency in overlay['code_refs']:
        if not io.evidence_ok(dependency, ROOT):
            raise ValueError('Qualified checker dependency changed')
    verify_adapter_dependencies(receipt)
    for name in ('checker', 'adapter', 'source_binding', 'checker_request', 'rebuild_result'):
        if not io.evidence_ok(receipt[name], ROOT):
            raise ValueError('Actual audit evidence changed: ' + name)
    score = load(receipt['audit_result'])
    if (score.get('task_key') != key or score.get('target') != 'audit'
            or receipt['audit_reward'] != score.get('reward') or receipt['audit_status'] != score.get('status')):
        raise ValueError('Audit receipt differs from actual checker output')
    reward = binary_reward(score.get('reward')) if score.get('status') == 'scored' else None
    return reward, io.evidence_ref(receipt_path, ROOT)


def reconcile(runs, receipts):
    receipt_index = {}
    for path in receipts:
        receipt = io.read_json(path)
        identity = (receipt['original_result']['path'], receipt['original_result']['sha256'])
        if identity in receipt_index:
            raise ValueError('Multiple audit receipts for one actual trial; select an explicit review version')
        receipt_index[identity] = Path(path)
    seen_slots, seen_trials, used_receipts = set(), set(), set()
    tasks, run_records = {}, []
    for directory in runs:
        directory = Path(directory).resolve()
        manifest = io.read_json(directory / 'manifest.json')
        identity = manifest['identity']
        config = io.read_json(directory / 'config.yaml')
        if (manifest['config_hash'] != io.file_hash(directory / 'config.yaml')
                or config != identity['config'] or manifest['experiment_id'] != io.digest(identity)
                or identity['purpose'] != 'difficulty_only_no_training' or not manifest['training_prohibited']):
            raise ValueError('Prepared difficulty run identity changed')
        rules = load(identity['measurement_rules'])
        if rules['training_prohibited'] is not True or config['retry']['max_retries'] != 0:
            raise ValueError('Unexpected training/retry policy')
        key, version = identity['task_key'], identity['task_version']
        production = identity.get('reviewed_production') or identity['phase3_production']
        expected_review = production[key].get('qualified_review') or production[key]['pool_row']['phase3_record']
        slots = identity['attempt_ids']
        for slot in slots:
            if (key, slot) in seen_slots:
                raise ValueError('Duplicate planned task/attempt slot. An infrastructure retry needs an explicit adjudication.')
            seen_slots.add((key, slot))
        if len(set(slots)) != len(slots) or not set(slots) <= {1, 2, 3, 4}:
            raise ValueError('Invalid planned slots')
        if config['n_concurrent_trials'] != 1 or config['n_attempts'] != len(slots):
            raise ValueError('Unexpected attempt ordering/concurrency')
        task = tasks.setdefault(key, {'task_key': key, 'task_version': version, 'attempts': [],
            'measurement_rules': identity['measurement_rules']})
        if task['task_version'] != version or task['measurement_rules'] != identity['measurement_rules']:
            raise ValueError('Mixed task versions or measurement protocols')
        job = directory / 'jobs' / config['job_name']
        result_paths = list(job.glob('*/result.json'))
        results = [(path, io.read_json(path)) for path in result_paths]
        results.sort(key=lambda pair: (stamp(pair[1]['started_at']), pair[1]['id']))
        if len(results) > len(slots):
            raise ValueError('Excess actual trials')
        run_record = {'manifest': io.evidence_ref(directory / 'manifest.json', ROOT),
            'planned_attempts': slots, 'actual_terminal_trials': len(results),
            'launch_result': io.evidence_ref(directory / 'launch-result.json', ROOT)
                if (directory / 'launch-result.json').exists() else None,
            'slot_binding': 'Single-task concurrency1: chronological actual trial starts mapped to listed planned slots; no retries.'}
        run_records.append(run_record)
        for slot, (path, raw) in zip(slots, results):
            if raw['id'] in seen_trials:
                raise ValueError('Duplicate raw trial ID')
            seen_trials.add(raw['id'])
            measured_agent(raw, config['agents'][0])
            if (Path(raw['config']['task']['path']).resolve() != Path(config['tasks'][0]['path']).resolve()
                    or path.parent.parent.resolve() != job.resolve()):
                raise ValueError('Raw trial is outside the planned task/job')
            raw_ref = io.evidence_ref(path, ROOT)
            reward_map = (raw.get('verifier_result') or {}).get('rewards')
            native = binary_reward(reward_map.get('reward')) if isinstance(reward_map, dict) else None
            gaps = []
            if raw.get('exception_info'):
                gaps.append('Actual exception requires evidence-based validity/budget adjudication; no automatic replacement')
            if not raw.get('finished_at'):
                gaps.append('Trial not terminal')
            try:
                verify_capture(path.parent, key)
                capture_ref = io.evidence_ref(path.parent / 'artifacts/difficulty-submission/capture.json', ROOT)
            except (KeyError, ValueError, OSError) as exc:
                gaps.append('Submission capture: ' + str(exc)); capture_ref = None
            receipt_path = receipt_index.get((raw_ref['path'], raw_ref['sha256']))
            audit = None; receipt_ref = None
            if receipt_path:
                audit, receipt_ref = audit_for_trial(receipt_path, raw_ref, key, version, expected_review)
                used_receipts.add(receipt_path)
            else:
                gaps.append('Actual submission audit not yet executed')
            if native is None:
                gaps.append('Native reward absent or unsupported; preserved as recorded')
            if audit is None:
                gaps.append('Audit score unresolved')
            valid = not gaps
            task['attempts'].append({'attempt': slot, 'trial_id': raw['id'],
                'experiment_id': manifest['experiment_id'], 'original_result': raw_ref,
                'native_rewards': reward_map, 'native_success': None if native is None else native == 1,
                'audit_reward': audit, 'joint_success': joint_outcome(native, audit, execution_valid=valid),
                'execution_valid': valid, 'exception_info': raw.get('exception_info'),
                'agent_result': raw.get('agent_result'),
                'started_at': raw['started_at'], 'finished_at': raw.get('finished_at'),
                'phase_timestamps': {k: raw.get(k) for k in ('environment_setup', 'agent_setup', 'agent_execution', 'verifier')},
                'gaps': gaps, 'capture': capture_ref, 'audit_receipt': receipt_ref,
                'evidence': [raw_ref, run_record['manifest'], *([capture_ref] if capture_ref else []),
                             *([receipt_ref] if receipt_ref else [])], 'training_prohibited': True})
    if set(receipt_index.values()) != used_receipts:
        raise ValueError('Foreign or unused actual audit receipt')
    for task in tasks.values():
        task['attempts'].sort(key=lambda row: row['attempt'])
        task['missing_attempts'] = sorted({1, 2, 3, 4} - {a['attempt'] for a in task['attempts']})
        task['valid_attempt_count'] = sum(type(a['joint_success']) is bool for a in task['attempts'])
        task['native_scored_count'] = sum(type(a['native_success']) is bool for a in task['attempts'])
        task['native_success_count'] = sum(a['native_success'] is True for a in task['attempts'])
        task['joint_success_count'] = (sum(a['joint_success'] for a in task['attempts'])
                                       if task['valid_attempt_count'] == 4 else None)
        task['complete'] = task['valid_attempt_count'] == 4 and not task['missing_attempts']
    return {'schema_version': 1, 'scope': 'Read-only actual difficulty reconciliation; no scheduling/training.',
        'complete': bool(tasks) and all(row['complete'] for row in tasks.values()),
        'tasks': [tasks[key] for key in sorted(tasks)], 'runs': run_records,
        'actual_terminal_trials': len(seen_trials), 'planned_attempt_slots': len(seen_slots),
        'model_requests': 0, 'training_prohibited': True, 'native_rewards_modified': False,
        'reporter': io.evidence_ref(Path(__file__), ROOT)}


def with_population(report, pool_path):
    pool = io.read_json(pool_path)
    if not pool['phase4_review_complete'] or not pool['training_prohibited']:
        raise ValueError('Population is not final or not measurement-only')
    for field in ('measurement_rules', 'split_rules', 'phase3_pool', 'phase4_pool'):
        load(pool[field])
    expected = {row['task_key']: row for row in pool['tasks'] if row['eligible_for_measurement']}
    actual = io.unique_index(report['tasks'], 'task_key')
    if set(actual) - set(expected):
        raise ValueError('Measurement includes a task outside the final candidate population')
    for key, task in actual.items():
        if task['task_version'] != expected[key]['task_version'] or task['measurement_rules'] != pool['measurement_rules']:
            raise ValueError('Measurement version/protocol differs from final candidate population')
    report.update(candidate_pool=io.evidence_ref(pool_path, ROOT), split_rules=pool['split_rules'],
                  missing_tasks=sorted(set(expected) - set(actual)))
    report['complete'] = report['complete'] and not report['missing_tasks']
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', required=True)
    parser.add_argument('--audit-receipt', type=Path, action='append', default=[])
    parser.add_argument('--pool', type=Path, help='Final measurement population; omitted for partial run diagnosis')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve old reports; choose a new output file')
    report = reconcile(args.run, args.audit_receipt)
    if args.pool:
        report = with_population(report, args.pool)
    io.write_json(args.output, report)
    print({key: report[key] for key in ('complete', 'actual_terminal_trials', 'planned_attempt_slots')})

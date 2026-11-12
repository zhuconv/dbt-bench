"""Prepare eight bounded Slurm batches; Harbor still owns every clean trial.

Reuse immutable task bytes and preserve historical experiments. No submission,
model request, retry, reward modification, or split draw happens here.
"""
from dbt_bench.local_config import expand as _release_expand
import argparse
from copy import deepcopy
from pathlib import Path
import sys
import tomllib
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from dbt_bench.prepare import check_harbor
from review_tools import cached_hashes
from run_difficulty_gpu_v2 import validate as validate_member
from difficulty_ade_preflight import observation_only

def remaining_tasks(population, reconciliation):
    eligible = io.unique_index([r for r in population['tasks'] if r['eligible_for_measurement']], 'task_key')
    measured = io.unique_index(reconciliation['tasks'], 'task_key')
    if set(eligible) != set(measured):
        raise ValueError('Population and reconciled tasks differ')
    completed, remaining = ([], [])
    for key, row in sorted(eligible.items()):
        actual = measured[key]
        if actual['task_version'] != row['task_version']:
            raise ValueError('Task version changed')
        if actual['complete']:
            ids = [a['attempt'] for a in actual['attempts']]
            if sorted(ids) != [1, 2, 3, 4] or actual['valid_attempt_count'] != 4:
                raise ValueError('Completed task lacks exactly four valid slots')
            completed.append(row)
        elif actual['attempts']:
            raise ValueError('Partially executed task requires an explicit remaining-slot plan')
        else:
            remaining.append(row)
    return (completed, remaining)

def balance(rows, count=8, walltime_sec=172800, minimum_margin_sec=21600):
    """Deterministic longest-budget-first packing, independent of model scores."""
    if count not in (6, 7, 8):
        raise ValueError('Only the authorized 6–8 batches are permitted')
    if walltime_sec > 172800:
        raise ValueError('gh QOS permits at most 48 hours')
    io.unique_index(rows, 'task_key')
    batches = [{'index': i + 1, 'members': [], 'native_agent_verifier_sec': 0} for i in range(count)]
    for row in sorted(rows, key=lambda r: (-r['native_agent_verifier_sec'], r['task_key'])):
        cost = row['native_agent_verifier_sec']
        if cost <= 0:
            raise ValueError('Finite positive native budgets are required')
        selected = min(batches, key=lambda b: (b['native_agent_verifier_sec'], b['index']))
        selected['members'].append(row)
        selected['native_agent_verifier_sec'] += cost
    for batch in batches:
        batch['walltime_sec'] = walltime_sec
        batch['overhead_margin_sec'] = walltime_sec - batch['native_agent_verifier_sec']
        if not batch['members'] or batch['overhead_margin_sec'] < minimum_margin_sec:
            raise ValueError('Batch exceeds wall time minus six hours reserved overhead')
    return batches

def changed_config(previous, output):
    updated = deepcopy(previous)
    updated.update(job_name=Path(output).name, jobs_dir=str(Path(output) / 'jobs'), n_attempts=4)
    if updated['n_concurrent_trials'] != 1 or updated['retry']['max_retries'] != 0:
        raise ValueError('Native single concurrency / zero retry policy changed')
    return updated

def native_schema_equivalent(config):
    normalized = check_harbor(config, [Path(config['tasks'][0]['path'])])
    for key in ('include_exceptions', 'exclude_exceptions'):
        previous = config['retry'].get(key)
        current = normalized['retry'].get(key)
        if previous is not None and current is not None and (set(previous) == set(current)):
            normalized['retry'][key] = previous
    if normalized != config:
        raise ValueError('Native Harbor normalization changed the prepared configuration')
    return True

def assert_no_execution(run):
    run = Path(run)
    if any(((run / name).exists() for name in ('launch.json', 'launch-intent.json', 'launch-result.json', 'runtime-status.json', 'jobs'))):
        raise ValueError('Unfinished historical execution must be reconciled before migration: ' + str(run))

def prepare(population_path, reconciliation_path, plan_path, amendment_path, output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to('/scratch'):
        raise ValueError('Use a new SCRATCH plan directory')
    population, reconciliation, old_plan, amendment = map(io.read_json, (population_path, reconciliation_path, plan_path, amendment_path))
    if not population['phase4_review_complete'] or not population['training_prohibited']:
        raise ValueError('Final measurement-only population is required')
    if amendment['partition'] != 'gh' or amendment['account'] != _release_expand('${ALLOCATION}') or amendment['batch_count'] != 8 or (amendment['walltime_sec'] != 172800) or (amendment['training_prohibited'] is not True):
        raise ValueError('Scheduling amendment differs from authorized scope')
    for ref in amendment['evidence']:
        if not io.evidence_ok(ref, ROOT):
            raise ValueError('Scheduling evidence changed')
    complete, remaining = remaining_tasks(population, reconciliation)
    pending_keys = {r['task_key'] for r in remaining}
    available = {}
    all_old = old_plan['reused'] + old_plan['new_runs']
    for row in all_old:
        key = row['task_key']
        if key not in pending_keys:
            continue
        assert_no_execution(row['run'])
        available[str(Path(row['run']).resolve())] = row
    selection = amendment['baseline_selection']
    if not io.evidence_ok(selection, ROOT):
        raise ValueError('Reviewed baseline selection changed')
    sources = {}
    for chosen in io.read_json(ROOT / selection['path']):
        old = str(Path(chosen['source_run']).resolve())
        key = chosen['task_key']
        if old not in available or available[old]['task_key'] != key or key in sources:
            raise ValueError('Reviewed baseline is foreign or duplicated')
        if not io.evidence_ok(chosen['source_manifest'], ROOT):
            raise ValueError('Reviewed baseline manifest changed')
        sources[key] = available[old]
    if set(sources) != pending_keys:
        raise ValueError('Missing prepared source task')
    rows = []
    with cached_hashes():
        for candidate in remaining:
            key = candidate['task_key']
            old = Path(sources[key]['run'])
            m, c, _ = validate_member(old)
            identity = m['identity']
            if identity['task_version'] != candidate['task_version'] or identity['measurement_rules'] != population['measurement_rules']:
                raise ValueError('Sealed task identity differs from final population')
            preflight = io.read_json(old / 'preflight.json')
            for field in ('manifest', 'runner', 'slurm_script'):
                if not io.evidence_ok(preflight[field], ROOT):
                    raise ValueError('Old preparation source changed: ' + field)
            task = Path(c['tasks'][0]['path'])
            observation_only(task, old / 'production/tasks' / key, identity, ROOT)
            native = tomllib.loads((task / 'task.toml').read_text())
            budget = {field: native[field]['timeout_sec'] for field in ('agent', 'verifier')}
            budget.update(environment_build=native['environment']['build_timeout_sec'], collect=sum((h['timeout_sec'] for h in native['verifier'].get('collect', []))))
            rows.append({'task_key': key, 'task_version': candidate['task_version'], 'source': candidate['source'], 'original_run': str(old), 'original_manifest': io.evidence_ref(old / 'manifest.json', ROOT), 'original_preflight': io.evidence_ref(old / 'preflight.json', ROOT), 'native_budgets_per_attempt_sec': budget, 'native_agent_verifier_sec': 4 * (budget['agent'] + budget['verifier'])})
    batches = balance(rows)
    output.mkdir(parents=True)
    seed_basis = {'amendment': io.evidence_ref(amendment_path, ROOT), 'population': io.evidence_ref(population_path, ROOT), 'batch_keys': [[r['task_key'] for r in b['members']] for b in batches]}
    plan = {'schema_version': 1, 'seed_basis': seed_basis, 'batches': batches, 'completed_tasks_preserved': complete, 'completed_attempts_preserved': len(complete) * 4, 'remaining_tasks': len(rows), 'remaining_attempts': len(rows) * 4, 'historical_plan': io.evidence_ref(plan_path, ROOT), 'prior_reconciliation': io.evidence_ref(reconciliation_path, ROOT), 'builder': io.evidence_ref(__file__, ROOT), 'model_requests': 0, 'training_prohibited': True}
    io.write_json(output / 'plan.json', plan)
    planned, results = ([], [])
    with cached_hashes():
        for batch in batches:
            directory = output / f"batch-{batch['index']:02d}"
            directory.mkdir()
            seed = int(io.digest({'basis': seed_basis, 'batch': batch['index']})[:8], 16)
            member_refs = []
            slots = []
            for row in batch['members']:
                old = Path(row['original_run'])
                run = directory / 'members' / row['task_key']
                run.mkdir(parents=True)
                previous = io.read_json(old / 'manifest.json')
                identity = deepcopy(previous['identity'])
                config = changed_config(identity['config'], run)
                native_schema_equivalent(config)
                identity.update(config=config, attempt_ids=[1, 2, 3, 4], server_seed=seed, scheduling_amendment=io.evidence_ref(amendment_path, ROOT), shared_model_batch=str(directory), migrated_from=row['original_manifest'])
                member_slots = [{'task_key': row['task_key'], 'attempt': n} for n in (1, 2, 3, 4)]
                io.write_json(run / 'config.yaml', config)
                manifest = {'schema_version': 1, 'identity': identity, 'experiment_id': io.digest(identity), 'config_hash': io.file_hash(run / 'config.yaml'), 'planned': member_slots, 'training_prohibited': True, 'prepared_model_requests': 0}
                io.write_json(run / 'manifest.json', manifest)
                validate_member(run)
                member_refs.append({'run': str(run), 'manifest': io.evidence_ref(run / 'manifest.json', ROOT)})
                slots.extend(member_slots)
                planned.extend([{**s, 'task_version': row['task_version'], 'run': str(run), 'batch': str(directory)} for s in member_slots])
            identity = {'measurement_rules': population['measurement_rules'], 'scheduling_amendment': io.evidence_ref(amendment_path, ROOT), 'server_seed': seed, 'walltime_sec': batch['walltime_sec'], 'native_agent_verifier_sec': batch['native_agent_verifier_sec'], 'overhead_margin_sec': batch['overhead_margin_sec'], 'members': member_refs, 'planned': slots, 'plan': io.evidence_ref(output / 'plan.json', ROOT)}
            manifest = {'schema_version': 1, 'purpose': 'difficulty_only_no_training', 'training_prohibited': True, 'identity': identity, 'experiment_id': io.digest(identity)}
            io.write_json(directory / 'manifest.json', manifest)
            results.append({'batch': str(directory), 'manifest': io.evidence_ref(directory / 'manifest.json', ROOT), 'task_count': len(member_refs), 'attempt_count': len(slots), 'server_seed': seed, 'native_agent_verifier_hours': batch['native_agent_verifier_sec'] / 3600, 'overhead_margin_hours': batch['overhead_margin_sec'] / 3600, 'walltime_hours': 48})
    if len({(r['task_key'], r['attempt']) for r in planned}) != len(rows) * 4:
        raise ValueError('Duplicate or missing migrated attempt slots')
    io.write_jsonl(output / 'planned-attempts.jsonl', planned)
    summary = {'schema_version': 1, 'plan': io.evidence_ref(output / 'plan.json', ROOT), 'batches': results, 'remaining_attempts': len(planned), 'completed_attempts_preserved': len(complete) * 4, 'training_prohibited': True, 'model_requests': 0, 'submitted_jobs': 0, 'planned_attempts': io.evidence_ref(output / 'planned-attempts.jsonl', ROOT), 'sealing_required_before_submission': True}
    io.write_json(output / 'summary.json', summary)
    return summary
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('population', 'reconciliation', 'plan', 'amendment', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.population, args.reconciliation, args.plan, args.amendment, args.output)
    print({'batches': len(result['batches']), 'remaining_attempts': result['remaining_attempts'], 'model_requests': 0})

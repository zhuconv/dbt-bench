"""Prepare new timeout-runtime identities; never submit or issue model requests."""
from dbt_bench.local_config import expand as _release_expand
import argparse
from copy import deepcopy
from pathlib import Path
import shlex
import sys
import tomllib
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from review_tools import cached_hashes
from prepare_difficulty_gh_batches_v2 import native_schema_equivalent
from run_difficulty_gpu_v2 import validate as validate_source
from run_difficulty_gh_batch_v3 import validate_batch, validate_runtime, SLURM_SCRIPT, ALLOCATION_POLICY
KEY = 'deb__dbt-fix-repeat-purchase-cohort-revenue'

def ref(path):
    return io.evidence_ref(path, ROOT)

def runtime_binding(runtime, release):
    manifest = io.read_json(runtime / 'manifest.json')
    site = Path(manifest['package']).parent
    binding = {'manifest': ref(runtime / 'manifest.json'), 'runtime_id': manifest['runtime_id'], 'bootstrap_files': [ref(runtime / 'venv/bin/harbor'), ref(runtime / 'venv/pyvenv.cfg'), ref(site / 'sealed-base-dependencies.pth')], 'cpu_release': ref(release)}
    validate_runtime(binding, ROOT)
    return binding

def phase_budgets(config):
    from harbor.agents.installed.qwen_code import QwenCode
    task = tomllib.loads((Path(config['tasks'][0]['path']) / 'task.toml').read_text())
    health = task['environment'].get('healthcheck', {})
    budgets = {k: task[k]['timeout_sec'] for k in ('agent', 'verifier')}
    budgets.update(environment_build=task['environment']['build_timeout_sec'], agent_setup=QwenCode.DEFAULT_SETUP_TIMEOUT_SEC, collect=sum((h['timeout_sec'] for h in task['verifier'].get('collect', []))), healthcheck=health.get('timeout_sec', 0) * health.get('retries', 1) + health.get('interval_sec', 0) * max(0, health.get('retries', 1) - 1))
    return budgets

def pack(rows, capacity=86400, fixed_margin=4800):
    """At most3 members, full configured phase sums plus startup/cleanup margin."""
    batches = []
    for row in sorted(rows, key=lambda r: (-r['full_phase_seconds'], r['task_key'])):
        if row['full_phase_seconds'] + fixed_margin > capacity:
            raise ValueError('One task cannot fit24h with all four attempts: ' + row['task_key'])
        choices = [b for b in batches if len(b) < 3 and sum((r['full_phase_seconds'] for r in b)) + row['full_phase_seconds'] + fixed_margin <= capacity]
        target = max(choices, key=lambda b: sum((r['full_phase_seconds'] for r in b))) if choices else None
        if target is None:
            target = []
            batches.append(target)
        target.append(row)
    return batches

def prepare(mode, source, runtime, release, population, output):
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Require a new SCRATCH output directory')
    binding = runtime_binding(runtime, release)
    rows = []
    sources = [source] if mode == 'recovery' else sorted(source.glob('batch-*/members/*'))
    with cached_hashes():
        for old in sources:
            if mode == 'rebatch' and any(((old / n).exists() for n in ('launch.json', 'jobs', 'launch-result.json'))):
                raise ValueError('Already-started task cannot be rebatch source: ' + str(old))
            manifest, config, _ = validate_source(old)
            budgets = phase_budgets(config)
            count = 1 if mode == 'recovery' else 4
            rows.append({'task_key': manifest['identity']['task_key'], 'source_run': str(old), 'source_manifest': ref(old / 'manifest.json'), 'budgets_per_attempt_sec': budgets, 'native_seconds': count * (budgets['agent'] + budgets['verifier']), 'full_phase_seconds': count * sum(budgets.values()), 'attempt_ids': [1] if count == 1 else [1, 2, 3, 4]})
        if len(rows) != (1 if mode == 'recovery' else 64) or len({r['task_key'] for r in rows}) != len(rows):
            raise ValueError('Expected exactly one recovery task or64 unique untouched tasks')
        recovery = None
        if mode == 'recovery':
            if rows[0]['task_key'] != KEY:
                raise ValueError('Only repeat-purchase recovery approved')
            prior = sorted(source.glob('jobs/*/*/result.json'), key=lambda p: io.read_json(p)['started_at'])
            if len(prior) != 4 or not prior[0].parent.name.endswith('NKMHv3F'):
                raise ValueError('Original four attempts / invalid slot identity changed')
            adjudication = source.parents[3] / 'actual-audits-repeat-20261112-v1/timeout-adjudication.json'
            facts = io.read_json(adjudication)
            if not facts['external_interruption'] or facts['replacement_attempt_slot'] != 1:
                raise ValueError('Recovery lacks external-interruption adjudication')
            recovery = {'task_key': KEY, 'replaces_trial': 'NKMHv3F', 'attempt': 1, 'adjudication': ref(adjudication), 'replaced_result': ref(prior[0]), 'retained_slots': [2, 3, 4], 'retained_results': [ref(p) for p in prior[1:]], 'purpose': 'Prescribed difficulty slot replacement and full GPU runtime validation; no extra model attempt.'}
        batches = [rows] if mode == 'recovery' else pack(rows)
        output.mkdir(parents=True)
        amendment = {'schema_version': 1, 'approved_date': '2026-11-12', 'account': _release_expand('${ALLOCATION}'), 'partition': 'gh', 'allowed_walltime_sec': [18000] if recovery else [86400], 'training_prohibited': True, 'no_training_use_of_measurement_trajectories': True, 'scope': 'One recovery attempt approved for submission' if recovery else '64tasks rebatched, prepared only; submission requires GPU validation and further user confirmation', 'runtime_patch': binding, 'population': ref(population), 'measurement_rules': ref(ROOT / 'configs/difficulty-v1/measurement-rules.json'), 'split_rules': ref(ROOT / 'configs/difficulty-v1/split-rules.json'), 'seed_policy': 'Recovery preserves prior server seed; new batches retain existing deterministic amendment/population/membership/index hash derivation. Continuous single-service RNG stream, not claimed byte-identical after regrouping.', 'budget_basis': 'Full configured per-attempt phase caps summed; model startup1200s plus3600s shared cleanup/IO margin for24h batches. Caps unchanged; filesystem/network stalls are not hard bounded.'}
        io.write_json(output / 'scheduling-amendment.json', amendment)
        seed_basis = {'amendment': ref(output / 'scheduling-amendment.json'), 'population': ref(population), 'batch_keys': [[r['task_key'] for r in b] for b in batches]}
        plan = {'schema_version': 1, 'mode': mode, 'seed_basis': seed_basis, 'batches': [{'index': i + 1, 'members': b} for i, b in enumerate(batches)], 'runtime_patch': binding, 'builder': ref(__file__), 'recovery': recovery, 'source': str(source), 'task_count': len(rows), 'attempt_count': sum((len(r['attempt_ids']) for r in rows)), 'training_prohibited': True, 'model_requests': 0, 'historical_files_modified': False}
        io.write_json(output / 'plan.json', plan)
        summary = []
        for index, members in enumerate(batches, 1):
            directory = output / f'batch-{index:02d}'
            directory.mkdir()
            seed = io.read_json(Path(members[0]['source_run']) / 'manifest.json')['identity']['server_seed'] if recovery else int(io.digest({'basis': seed_basis, 'batch': index})[:8], 16)
            member_refs = []
            slots = []
            for row in members:
                old = Path(row['source_run'])
                run = directory / 'members' / row['task_key']
                run.mkdir(parents=True)
                previous = io.read_json(old / 'manifest.json')
                identity = deepcopy(previous['identity'])
                config = deepcopy(identity['config'])
                config.update(job_name=run.name, jobs_dir=str(run / 'jobs'), n_attempts=len(row['attempt_ids']))
                native_schema_equivalent(config)
                identity.update(config=config, attempt_ids=row['attempt_ids'], server_seed=seed, shared_model_batch=str(directory), runtime_patch=binding, recovery=recovery, scheduling_amendment=ref(output / 'scheduling-amendment.json'), prepared_from=ref(old / 'manifest.json'))
                planned = [{'task_key': row['task_key'], 'attempt': n} for n in row['attempt_ids']]
                io.write_json(run / 'config.yaml', config)
                io.write_json(run / 'manifest.json', {'schema_version': 1, 'identity': identity, 'experiment_id': io.digest(identity), 'config_hash': io.file_hash(run / 'config.yaml'), 'planned': planned, 'training_prohibited': True})
                member_refs.append({'run': str(run), 'manifest': ref(run / 'manifest.json')})
                slots.extend(planned)
            walltime = 18000 if recovery else 86400
            identity = {'measurement_rules': amendment['measurement_rules'], 'scheduling_amendment': ref(output / 'scheduling-amendment.json'), 'runtime_patch': binding, 'server_seed': seed, 'walltime_sec': walltime, 'authorized_attempt_ids': members[0]['attempt_ids'], 'plan': ref(output / 'plan.json'), 'planned': slots, 'members': member_refs, 'recovery': recovery}
            manifest = {'schema_version': 1, 'identity': identity, 'experiment_id': io.digest(identity), 'purpose': 'difficulty_only_no_training', 'training_prohibited': True}
            io.write_json(directory / 'manifest.json', manifest)
            for d in [directory, *[Path(r['run']) for r in member_refs]]:
                io.write_json(d / 'preflight.json', {'runner': ref(ROOT / 'scripts/run_difficulty_gh_batch_v3.py'), 'slurm_script': ref(ROOT / SLURM_SCRIPT), 'manifest': ref(d / 'manifest.json'), 'allocation_policy': ALLOCATION_POLICY, 'preparer': ref(__file__), 'execution_dependencies': [ref(ROOT / p) for p in ('scripts/run_difficulty_gpu_v2.py', 'scripts/run_gh_smoke.py', 'scripts/review_tools.py', 'src/dbt_bench/io.py', 'scripts/verify_container_cleanup.py')], 'training_prohibited': True})
            validate_batch(directory)
            argv = ['sbatch', '--parsable', _release_expand('--account=${ALLOCATION}'), '--partition=gh', '--nodes=1', '--ntasks=1', '--time=' + ('05:00:00' if recovery else '24:00:00'), '--job-name=' + ('dbt-runtime-recovery' if recovery else f'dbt-diff24-{index:02d}'), '--comment=dbt-difficulty-' + manifest['experiment_id'], '--output=' + str(directory / 'slurm-%j.out'), '--error=' + str(directory / 'slurm-%j.err'), str(ROOT / SLURM_SCRIPT), str(directory), str(runtime / 'venv/bin/python')]
            native = sum((r['native_seconds'] for r in members))
            full = sum((r['full_phase_seconds'] for r in members))
            summary.append({'batch': str(directory), 'manifest': ref(directory / 'manifest.json'), 'tasks': [r['task_key'] for r in members], 'attempts': len(slots), 'server_seed': seed, 'walltime_sec': walltime, 'native_agent_verifier_sec': native, 'all_phase_caps_sec': full, 'plus_model_startup_sec': full + 1200, 'remaining_margin_sec': walltime - full - 1200, 'submit_argv': argv})
        io.write_json(output / 'summary.json', {'batches': summary, 'tasks': len(rows), 'attempts': len(rows) * (1 if recovery else 4), 'prepared_only': True, 'training_prohibited': True})
        io.write_text(output / 'submission-commands.txt', '# Execute from repository root; no submissions performed by preparation.\n' + '\n'.join((shlex.join(r['submit_argv']) for r in summary)) + '\n')
        io.write_json(output / 'local-dry-run.json', {'passed': True, 'validated_batches': len(summary), 'model_requests': 0, 'slurm_submissions': 0, 'summary': ref(output / 'summary.json')})
    return {'passed': True, 'batches': len(summary), 'tasks': len(rows), 'output': str(output)}
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['recovery', 'rebatch'], required=True)
    for name in ('source', 'runtime', 'release', 'population', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    print(prepare(**vars(p.parse_args())))

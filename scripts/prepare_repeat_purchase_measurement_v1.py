"""Register one approved qualification and prepare four attempts; never submit.

Retain population-v1 and every frozen pool row. The new top-level qualification
supersedes this task's historical eligibility only. Existing preparation/schema/
runner checks are reused; no scoring, training, model call, or Slurm command.
"""
from dbt_bench.local_config import expand as _release_expand
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import shlex
import sys
import tomllib
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from phase4_review_identity_v2 import precheck, without_reporting_imports
from prepare_difficulty_run_v3 import prepare as prepare_member
from prepare_difficulty_gh_batches_v2 import changed_config, native_schema_equivalent
from difficulty_ade_preflight import observation_only
from run_difficulty_gh_batch_v2 import validate_batch, ALLOCATION_POLICY, SLURM_SCRIPT
from review_tools import cached_hashes
KEY = 'deb__dbt-fix-repeat-purchase-cohort-revenue'
VERSION = 'phase4-instruction-f401f0d6f8a5c222'

def extend_population(previous, record, previous_ref, record_ref):
    if previous['version'] != 'difficulty-population-v1' or previous['eligible_count'] != 66 or (not previous['training_prohibited']) or (not previous['phase4_review_complete']):
        raise ValueError('Expected the authorized historical 66-task population')
    if record['task_key'] != KEY or record['task_version'] != VERSION or record['eligible_for_measurement'] is not True or (record['eligible_for_sampling'] is not True) or (record['training_use_allowed'] is not False) or (record['audit']['conclusion'] != 'validated') or (record['audit']['check_counts'] != {'pass': 14}) or (record['native']['conclusion'] != 'weak') or (not record['native']['review_complete']) or (not record['reproduction']['native1_both_clean_builds']) or (not record['formal_candidate_evaluation']['passed']) or (record['formal_candidate_evaluation']['scores'] != [1] * 6):
        raise ValueError('The new repeat-purchase qualification is not sufficient')
    result = deepcopy(previous)
    index = io.unique_index(result['tasks'], 'task_key')
    if len(index) != 135 or sum((r['eligible_for_measurement'] for r in index.values())) != 66:
        raise ValueError('Historical population counts differ')
    row = index[KEY]
    if row['eligible_for_measurement'] or row['task_version'] != VERSION or row['source'] != 'deb':
        raise ValueError('Unexpected historical task/version/eligibility')
    row.update(eligible_for_measurement=True, qualification_phase=4, qualified_review=record_ref, audit_conclusion='validated', native_conclusion=record['native']['conclusion'], qualification_revision={'previous_population': previous_ref, 'historical_pool_row_preserved': True, 'scope': 'New qualified_review governs eligibility; frozen_pool_row is unchanged historical provenance.'})
    result.update(version='difficulty-population-v2', previous_population=previous_ref, eligible_count=67, eligible_by_source=dict(Counter((r['source'] for r in result['tasks'] if r['eligible_for_measurement']))), registration={'approved_date': '2026-11-09', 'task_key': KEY, 'qualified_review': record_ref, 'historical_pool_rows_preserved': True, 'frozen': False, 'new_split_drawn': False, 'model_requests': 0})
    assert result['eligible_by_source'] == {'ade': 15, 'deb': 36, 'spider2': 16}
    assert [r for r in result['tasks'] if r['task_key'] != KEY] == [r for r in previous['tasks'] if r['task_key'] != KEY]
    return result

def prepare(previous, record_path, population_output, output, root=ROOT):
    root, previous, record_path, population_output, output = map(Path, (root, previous, record_path, population_output, output))
    if output.exists() or population_output.exists() or (not output.resolve().is_relative_to('/scratch')) or (not population_output.resolve().is_relative_to('/scratch')):
        raise ValueError('Require new SCRATCH population and run directories')
    ref = lambda p: io.evidence_ref(p, root)
    previous_ref, record_ref = (ref(previous), ref(record_path))
    record = io.read_json(record_path)
    with without_reporting_imports(), cached_hashes():
        for name in ('native', 'audit'):
            if not io.evidence_ok(record['overlays'][name], root):
                raise ValueError('Qualified overlay changed')
            checked = precheck(root / record['overlays'][name]['path'], root)
            checked['overlay'] = record[name]['overlay']
            if checked != record[name]:
                raise ValueError('Current qualification differs from registered review')
        population = extend_population(io.read_json(previous), record, previous_ref, record_ref)
        population['registration']['builder'] = ref(__file__)
        io.write_json(population_output, population)
        output.mkdir(parents=True)
        source = output / 'prepared-source'
        prepare_member(source, 'deb', KEY, [1, 2, 3, 4], population_output, root)
        original = io.read_json(source / 'manifest.json')
        task = Path(original['identity']['config']['tasks'][0]['path'])
        observation_only(task, source / 'production/tasks' / KEY, original['identity'], root)
        native = tomllib.loads((task / 'task.toml').read_text())
        budgets = {name: native[name]['timeout_sec'] for name in ('agent', 'verifier')}
        budgets.update(environment_build=native['environment']['build_timeout_sec'], collect=sum((h['timeout_sec'] for h in native['verifier'].get('collect', []))))
        native_seconds = 4 * (budgets['agent'] + budgets['verifier'])
        if native_seconds + 21600 > 172800:
            raise ValueError('Native budgets lack six hours of overhead margin')
        amendment = {'schema_version': 1, 'approved_date': '2026-11-09', 'account': _release_expand('${ALLOCATION}'), 'partition': 'gh', 'walltime_sec': 172800, 'training_prohibited': True, 'prepared_only': True, 'scope': 'One newly qualified task, four independent attempts, existing v2 runner/sbatch; no modification of queued jobs.', 'previous_scheduling_policy': ref(root / 'configs/difficulty-v1/gh-batching-amendment-v1.json'), 'population': ref(population_output), 'qualification': record_ref}
        io.write_json(output / 'scheduling-amendment.json', amendment)
        batch = output / 'batch-01'
        member = batch / 'members' / KEY
        member.mkdir(parents=True)
        config = changed_config(original['identity']['config'], member)
        native_schema_equivalent(config)
        member_identity = deepcopy(original['identity'])
        member_identity.update(config=config, shared_model_batch=str(batch), scheduling_amendment=ref(output / 'scheduling-amendment.json'), prepared_from=ref(source / 'manifest.json'), qualified_audit=record_ref)
        slots = [{'task_key': KEY, 'attempt': n} for n in (1, 2, 3, 4)]
        io.write_json(member / 'config.yaml', config)
        io.write_json(member / 'manifest.json', {'schema_version': 1, 'identity': member_identity, 'experiment_id': io.digest(member_identity), 'config_hash': io.file_hash(member / 'config.yaml'), 'planned': slots, 'training_prohibited': True, 'prepared_model_requests': 0})
        plan = {'schema_version': 1, 'task_key': KEY, 'task_version': VERSION, 'population': ref(population_output), 'qualified_review': record_ref, 'native_budgets_per_attempt_sec': budgets, 'native_agent_verifier_sec': native_seconds, 'walltime_sec': 172800, 'overhead_margin_sec': 172800 - native_seconds, 'planned': slots, 'training_prohibited': True, 'model_requests': 0, 'builder': ref(__file__), 'split_rules_unchanged': True, 'frozen': False}
        io.write_json(output / 'plan.json', plan)
        batch_identity = {'measurement_rules': population['measurement_rules'], 'scheduling_amendment': ref(output / 'scheduling-amendment.json'), 'server_seed': member_identity['server_seed'], 'walltime_sec': 172800, 'native_agent_verifier_sec': native_seconds, 'overhead_margin_sec': 172800 - native_seconds, 'plan': ref(output / 'plan.json'), 'planned': slots, 'members': [{'run': str(member), 'manifest': ref(member / 'manifest.json')}]}
        io.write_json(batch / 'manifest.json', {'schema_version': 1, 'identity': batch_identity, 'experiment_id': io.digest(batch_identity), 'purpose': 'difficulty_only_no_training', 'training_prohibited': True})
        for directory in (batch, member):
            io.write_json(directory / 'preflight.json', {'schema_version': 1, 'runner': ref(root / 'scripts/run_difficulty_gh_batch_v2.py'), 'slurm_script': ref(root / SLURM_SCRIPT), 'manifest': ref(directory / 'manifest.json'), 'allocation_policy': ALLOCATION_POLICY, 'execution_dependencies': [ref(root / p) for p in ('scripts/run_difficulty_gpu_v2.py', 'scripts/run_gh_smoke.py', 'scripts/review_tools.py', 'src/dbt_bench/io.py')], 'preparer': ref(__file__), 'only_native_collect_added': True, 'native_tests_unchanged': True, 'model_requests': 0, 'training_prohibited': True})
        validate_batch(batch, root)
        assert io.evidence_ok(previous_ref, root) and io.evidence_ok(record_ref, root)
        command = shlex.join(['sbatch', str(SLURM_SCRIPT), str(batch)])
        io.write_text(output / 'submission-command.txt', '# Prepared only; not executed. Submit from repository root only after authorization.\n' + command + '\n')
        result = {'schema_version': 1, 'task_key': KEY, 'population': ref(population_output), 'eligible_count': 67, 'attempts_prepared': 4, 'batch': str(batch), 'preflight': ref(batch / 'preflight.json'), 'plan': ref(output / 'plan.json'), 'passed': True, 'submitted_jobs': 0, 'model_requests': 0, 'training_prohibited': True, 'split_drawn': False, 'frozen': False, 'previous_population_unchanged': True}
        io.write_json(output / 'completion.json', result)
    return result
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('previous', 'record', 'population-output', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.previous, args.record, args.population_output, args.output))

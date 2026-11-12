"""Evidence checks for declared Workday workflow cases, not a certified audit.

This examines observed command results and filesystem state. It does not use
the native reward, SQL spelling, or a ban on equivalent dbt selection syntax.
The required enabled model IDs are pinned to a reviewed original-task build.
"""


def violations(state, run_results, manifest, required_model_ids):
    required = set(required_model_ids)
    if not required or len(required) != len(required_model_ids):
        raise ValueError('Invalid required model identity')
    failures = []
    commands = state['commands']
    successful = [c['argv'] for c in commands if c['returncode'] == 0]
    if state['packages_present'] and not any(a[:2] == ['dbt', 'deps'] for a in successful):
        failures.append('deps_not_run')
    if not state['target_model_present']:
        failures.append('target_model_missing')
    if state['relation_type'] != 'BASE TABLE':
        failures.append('target_is_not_table')
    if not any(a[:2] == ['dbt', 'run'] for a in successful):
        failures.append('dbt_run_not_executed')
    if run_results is None or manifest is None:
        failures.append('run_artifacts_missing')
        return failures
    if run_results['args'].get('which') != 'run':
        failures.append('artifact_is_not_run')
    if run_results['metadata']['invocation_id'] != manifest['metadata']['invocation_id']:
        failures.append('invocation_mismatch')
    enabled = {k for k, n in manifest['nodes'].items()
               if n['resource_type'] == 'model' and n['config']['enabled']}
    if enabled != required:
        failures.append('enabled_model_set_changed')
    rows = run_results['results']
    observed = [r['unique_id'] for r in rows]
    if set(observed) != required or len(observed) != len(required):
        failures.append('all_models_not_executed')
    if any(r['status'] != 'success' for r in rows):
        failures.append('model_execution_failed')
    return failures


EXPECTED_FAILURES = {
    'original-reference': [],
    'original-selected': ['all_models_not_executed'],
    'original-missing-model': ['target_model_missing'],
    'original-direct-table': ['deps_not_run', 'target_model_missing', 'dbt_run_not_executed', 'run_artifacts_missing'],
    'original-view': ['target_is_not_table'],
    'fixture-arrays': [],
    'fixture-extra-column': [],
}

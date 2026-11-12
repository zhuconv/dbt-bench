"""Contract-aware delivery for qualified cohorts, RFM2 and churn-warning.

Cohorts/RFM2 deliver dbt models; native tests build them. Churn-warning expressly
requires building its upstream and materializing the output. Original database
captures are retained for all three; only their declared role differs.
"""
from pathlib import Path
import posixpath
import re
import shlex

from dbt_bench import io
from difficulty_deb_events import shell_result
import difficulty_deb_phase4_pipeline as previous
from review_support_v3 import outputs

ROOT = previous.ROOT
SOURCE_TASKS = {'dbt-customer-churn-cohorts', 'dbt-rfm-customer-segmentation-2'}
DELIVERY_CONTRACTS = {
    'dbt-customer-churn-cohorts': 'submitted_source_reconstruction',
    'dbt-rfm-customer-segmentation-2': 'submitted_source_reconstruction',
    'dbt-customer-churn-early-warning': 'actual_database',
}
# Absence of a pre-native output does not invalidate source-delivery tasks.
FAILURE_REQUIREMENTS = {k: v for k, v in previous.FAILURE_REQUIREMENTS.items() if k not in SOURCE_TASKS}


def explicit_profile_export(command, cwd, expected):
    """Recognize an actual successful explicit export, without requiring a run."""
    if not isinstance(command, str) or any(x in command for x in ('\n', ';', '|', '`', '$(', '>', '<')):
        return False
    try:
        stages = [shlex.split(s.strip()) for s in command.split('&&')]
    except ValueError:
        return False
    location = cwd if isinstance(cwd, str) and cwd.startswith('/') else None
    found = False
    for argv in stages:
        if not argv or any('&' in x for x in argv): return False
        if argv[0] == 'cd' and len(argv) == 2:
            value = argv[1]
            if '$' in value or '~' in value: return False
            location = value if value.startswith('/') else posixpath.normpath(posixpath.join(location, value)) if location else None
            continue
        if len(argv) == 2 and argv[0] == 'export' and argv[1].startswith('DBT_PROFILES_DIR='):
            value = argv[1].split('=', 1)[1]
            if not value or '$' in value or '~' in value: return False
            value = value if value.startswith('/') else posixpath.normpath(posixpath.join(location, value)) if location else None
            found = value == expected
            continue
        # An opaque script or printed command is not evidence of setting env.
        return False
    return found


def source_action_gate(key, events, project):
    gate = previous.declared_action_gate(key, events, project)
    if gate['reward'] in (0, 1): return gate
    calls = []
    for row in events.get('pairs', []):
        args = row.get('args', {})
        response = shell_result(row.get('response', {}))
        if row.get('name') == 'run_shell_command' and response and response[0] == 0 and explicit_profile_export(
                args.get('command'), args.get('directory', args.get('cwd')), project):
            calls.append(row['call_id'])
    if events.get('echo_before_first_write') is True and calls:
        return {'reward': 1, 'status': 'pass', 'reason':
            'Actual paired backend echo precedes source writes and an explicit successful local DBT_PROFILES_DIR export was observed. This source contract does not require a model-issued dbt run.',
            'profile_export_calls': calls, 'source_logs': events.get('source_logs', [])}
    return gate | {'reason': 'Original echo/local-profile environment evidence is incomplete; source delivery does not add a dbt-run obligation.'}


def contract(context, task, root):
    source = task in SOURCE_TASKS
    planned = Path(context['prepared']['identity']['config']['tasks'][0]['path'])
    instruction = planned/'instruction.md'; native = planned/'tests/test_outputs.py'
    text = native.read_text()
    if 'def run_dbt_pipeline' not in text or 'run_dbt_pipeline()' not in text:
        raise ValueError('Native build path changed; delivery classification needs review')
    return {'schema_version': 1, 'task_key': 'deb__'+task,
        'task_version': context['binding']['task_version'],
        'scoring_basis': DELIVERY_CONTRACTS[task],
        'submission_kind': 'dbt_model_source' if source else 'dbt_source_and_materialized_output',
        'native_builds_submission': True,
        'instruction': io.evidence_ref(instruction, root), 'native_build_path': io.evidence_ref(native, root),
        'qualified_review': context['binding']['qualified_review'],
        'rule_basis': ('The instruction specifies project/model SQL paths and output columns; no original final-project dbt invocation is prescribed. The unchanged native session/setup fixture runs the delivered models.' if source else
            'The instruction expressly says to build the enriched reference model first and to materialize the output mart; actual outputs and original upstream-before-final actions remain required.'),
        'original_database_role': 'preserved_input_identity_and_diagnostic' if source else 'evaluated_submission',
        'reconstruction_role': 'evaluate_exact_delivered_source' if source else 'source_and_idempotence_provenance',
        'original_actions': ['backend_echo_before_code', 'local_profiles_environment'] + ([] if source else ['reference_upstream_build_before_final_build']),
        'same_workspace_idempotence_required': task in previous.REPEAT_REQUIRED,
        'adapter': io.evidence_ref(Path(__file__), root), 'model_requests': 0}


def run(request, root=ROOT):
    root = Path(root); context = previous.verify(request['audit_dir'], root)
    key = request['task_key']; task = key.removeprefix('deb__')
    if task not in DELIVERY_CONTRACTS or context['binding']['task_key'] != key:
        raise ValueError('Unregistered qualified delivery contract')
    contract_path = Path(request['output_dir'])/'pipeline-delivery-contract-v2.json'
    if contract_path.exists(): raise ValueError('Preserve delivery decisions')
    delivery = contract(context, task, root); io.write_json(contract_path, delivery)
    cref = io.evidence_ref(contract_path, root)
    if task not in SOURCE_TASKS:
        result = previous.run(request, root)
    else:
        d = context['fresh']; raw = previous.raw_rows(d)
        fresh_snapshot, observed = outputs(d, 'agent-submission')
        unresolved = {n: x for n, x in observed.items() if 'table' not in x}
        valid = {n: x for n, x in observed.items() if 'table' in x}
        function = previous.cohorts if task.endswith('cohorts') else previous.rfm2
        expected, facts = function(raw, {n: x['rows'] for n, x in valid.items()})
        for n in expected:
            if n not in observed: unresolved[n] = {'reason': 'Fresh output not captured'}
        result = previous.compare_outputs(valid, {n: rows for n, rows in expected.items() if n in valid})
        if unresolved and result['reward'] != 0: result.update(reward=None, status='unknown')
        events = previous.checked_json(request['original_events'], root)
        gate = source_action_gate(key, events, d['request']['project_root'])
        declared = previous.policy(task); wf = previous.workflow(d, declared)
        profile_gap = previous.profile_resolution_gap(d, declared)
        if profile_gap: wf['checks']['profile'] = previous.ck('gap', profile_gap, frozen_static_result=wf['checks']['profile'])
        wf['checks']['backend'] = previous.ck('pass' if gate['reward'] == 1 else 'fail' if gate['reward'] == 0 else 'gap', gate['reason'], details=gate)
        wf['checks']['idempotence'] = previous.idempotence(task, d)
        states = [x['status'] for x in wf['checks'].values()]
        wf['reward'] = 0 if 'fail' in states else None if any(x in states for x in ('gap', 'pending')) else 1
        wf['status'] = 'unknown' if wf['reward'] is None else 'scored'
        if wf['reward'] == 0: result.update(reward=0, status='scored')
        elif wf['reward'] is None and result['reward'] != 0: result.update(reward=None, status='unknown')
        result.update(original_action_gate=gate, actual_workflow=wf, independent_facts=facts,
            unresolved_outputs=unresolved, evaluated_snapshot=fresh_snapshot,
            actual_snapshot=request['snapshot'], original_events=request['original_events'],
            source_binding=context['manifest']['source_binding'],
            adapter_code_refs=[io.evidence_ref(p, root) for p in [Path(previous.__file__),
                ROOT/'scripts/phase4_customer4_review_v3.py', ROOT/'scripts/phase4_deb_recompute_v2.py', previous.SHARED/'review_support_v3.py']],
            scope='The contract delivers dbt source. Evaluate outputs produced by the bound clean reconstruction of those exact bytes. Original DB is preserved and its raw input closure is checked separately. Audit commands never substitute for original model actions.')
    result.update(delivery_contract_ref=cref, scoring_basis=delivery['scoring_basis'],
        submission_kind=delivery['submission_kind'], original_snapshot=request['snapshot'])
    result['adapter_code_refs'].append(io.evidence_ref(Path(__file__), root))
    return result

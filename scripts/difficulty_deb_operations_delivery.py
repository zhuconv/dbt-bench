"""Reviewed source-delivery semantics for nine fixed DEB task versions.

This is a measurement-adapter policy, not a change to the task, native verifier,
or frozen audit. Native tests build these submitted dbt projects themselves.
Only original paired Qwen events establish required echo/profile actions.
"""
import ast
from pathlib import Path
from dbt_bench import io

ROOT = Path(__file__).resolve().parents[1]
REASONS = {
    'dbt-customer-risk-scoring': 'Requires a dbt project and named staging/mart models. The only dbt execution instruction is expressly for Snowflake; the DuckDB native fixture invokes deps and run itself.',
    'dbt-daily-order-summary': 'Requires a fresh standalone dbt project with local profile and a table-materialized model. No pre-submission dbt execution is required; native fixture builds and checks idempotence.',
    'dbt-fraud-detection-model': 'Requires named dbt models and deterministic output logic, without requiring agent-issued dbt execution. Native fixture runs +fraud_flagged_orders before reading outputs.',
    'dbt-fulfillment-sla': 'Requires source models/materializations; Verification gives a command example without a pre-submission execution obligation. Native session fixture runs the five models before inspecting tables.',
    'dbt-inventory-analysis': 'Requires a project, source/staging declarations, named mart and table materialization, without requiring prior agent execution. Native fixture invokes dbt run.',
    'dbt-product-category-analytics': 'Requires source/staging/intermediate/mart structure; the only dbt execution instruction is expressly for Snowflake. Native DuckDB fixture invokes deps and run.',
    'dbt-retail-task-01-channel-revenue': 'Output expectations explicitly say If you run dbt, so execution is optional. Submit the declared dbt view source; native fixture materializes the target view.',
    'dbt-supplier-scorecard': 'Requires named dbt source models/profile. The only dbt execution instruction is expressly for Snowflake; native DuckDB fixture invokes dbt run.',
    'dbt-three-way-matching': 'Required Output is the three_way_match.sql model; Testing Your Solution supplies diagnostic commands without specifying a pre-submission database artifact. Native test_model_exists invokes +three_way_match before checking its output.',
}
EXCERPTS = {
    'dbt-customer-risk-scoring': [(3, 3), (37, 44), (77, 105), (112, 112)],
    'dbt-daily-order-summary': [(5, 9), (32, 36), (57, 74)],
    'dbt-fraud-detection-model': [(5, 8), (39, 43), (96, 108)],
    'dbt-fulfillment-sla': [(84, 94), (376, 389)],
    'dbt-inventory-analysis': [(45, 64), (74, 85)],
    'dbt-product-category-analytics': [(71, 104), (127, 129)],
    'dbt-retail-task-01-channel-revenue': [(46, 50), (89, 98)],
    'dbt-supplier-scorecard': [(5, 8), (43, 48), (128, 137)],
    'dbt-three-way-matching': [(75, 81), (251, 258), (306, 310)],
}


def policy(context, root=ROOT):
    root = Path(root)
    binding = context['binding']
    key = binding['task_key']
    task = key.removeprefix('deb__')
    if task not in REASONS:
        raise ValueError('Task has no reviewed delivery policy')
    identity = context['prepared']['identity']
    actual = Path(identity['config']['tasks'][0]['path'])
    qualified = context['qualified']['record']
    qualified_identity = qualified['phase4_identity']
    if (binding['task_version'] != qualified['task_version']
            or io.file_hash(actual / 'instruction.md') != qualified_identity['instruction_sha256']
            or io.tree_hash(actual / 'tests') != identity['native_tests_hash']):
        raise ValueError('Delivery policy task version changed')
    lines = (actual / 'instruction.md').read_text().splitlines()
    test = actual / 'tests/test_outputs.py'
    text = test.read_text()
    functions = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name in (
                'run_dbt', 'run_dbt_pipeline', 'dbt_run', 'test_model_exists'):
            functions.append({'name': node.name, 'line': node.lineno,
                              'source': ast.get_source_segment(text, node)})
    if not any(f['name'] in ('run_dbt', 'run_dbt_pipeline') for f in functions):
        raise ValueError('Native materialization evidence unavailable')
    return {'schema_version': 1, 'task_key': key, 'task_version': binding['task_version'],
        'qualified_review': binding['qualified_review'],
        'instruction': io.evidence_ref(actual / 'instruction.md', root),
        'native_test': io.evidence_ref(test, root), 'native_tests_hash': identity['native_tests_hash'],
        'mode': 'source_materialized_by_verifier', 'scoring_basis': 'submitted_source_reconstruction',
        'pre_native_output_absence_is_failure': False, 'agent_dbt_run_required': False,
        'reason': REASONS[task],
        'instruction_excerpts': [{'start': a, 'end': b, 'text': '\n'.join(lines[a-1:b])}
                                 for a, b in EXCERPTS[task]],
        'native_build_functions': functions,
        'original_action_scope': 'Echo-before-code and explicitly required profile environment actions remain bound to original paired Qwen events. Fresh build commands never prove those actions.',
        'safety_scope': 'Preserve original DB/WAL and native reward. Audit only the exact captured delivered source on unchanged fixed inputs. No replacement with reviewed author SQL, no repair of missing submitted profiles.',
        'policy_implementation': io.evidence_ref(Path(__file__), root),
        'model_requests': 0, 'training_prohibited': True}

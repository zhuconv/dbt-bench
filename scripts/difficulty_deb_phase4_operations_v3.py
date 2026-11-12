"""Source-delivery routing with original echo/export, without an added run gate."""
from copy import deepcopy
from pathlib import Path
from dbt_bench import io
from difficulty_deb_binding_v3 import verify
from difficulty_deb_operations_delivery import policy
from difficulty_deb_actions import source_action_gate
import difficulty_deb_phase4_operations as previous

ROOT = Path(__file__).resolve().parents[1]
TASKS = previous.TASKS
FAILURE_REQUIREMENTS = {task: {} for task in TASKS}


def original_action_gate(task, events, project):
    if task in previous.NO_EXPORT:
        return previous.action_gate(task, events, project)
    return source_action_gate('deb__' + task, events, project)


def run(request, root=ROOT):
    root = Path(root)
    context = verify(request['audit_dir'], root)
    key = request['task_key']
    task = key.removeprefix('deb__')
    if task not in TASKS or context['binding']['task_key'] != key:
        raise ValueError('Wrong operations task')
    reviewed = policy(context, root)
    fresh = io.evidence_ref(context['trial'] / 'agent/audit-submission/snapshot.json', root)
    if request.get('reconstruction_snapshot') != fresh:
        raise ValueError('Source delivery needs the bound exact-source rebuild snapshot')
    if request.get('original_snapshot', request['snapshot']) != request['snapshot']:
        raise ValueError('Original snapshot alias differs before delivery selection')
    selected = deepcopy(request)
    selected['snapshot'] = fresh
    output = Path(request['output_dir'])
    observed = previous.actual_outputs(fresh, key, root)
    events = previous.load(request['original_events'], root)
    probe = previous.load(request['probe'], root)
    gate = original_action_gate(task, events, context['fresh']['request']['project_root'])
    if task in ('dbt-customer-risk-scoring', 'dbt-daily-order-summary', 'dbt-fraud-detection-model'):
        result = previous.legacy_values(context, task, selected, observed, events, probe, output, root, gate)
    elif task in ('dbt-inventory-analysis', 'dbt-product-category-analytics'):
        result = previous.request_values(context, task, selected, events, output, root)
    else:
        result = previous.custom_values(context, task, selected, observed, events, probe, output, root, gate)
    contract = previous.save(output / 'delivery-contract.json', reviewed, root)
    dependencies = [Path(__file__), root / 'scripts/difficulty_deb_phase4_operations.py',
                    root / 'scripts/difficulty_deb_operations_delivery.py',
                    root / 'scripts/difficulty_deb_operations_collect.py',
                    root / 'scripts/difficulty_deb_workflow_v3.py',
                    root / 'scripts/difficulty_deb_actions.py', root / 'scripts/difficulty_deb_events.py']
    if task in ('dbt-supplier-scorecard', 'dbt-three-way-matching', 'dbt-retail-task-01-channel-revenue'):
        dependencies += [previous.OPS / 'custom_workflow_v1.py', previous.OPS / 'actual_core_v2.py']
    if task == 'dbt-fulfillment-sla':
        dependencies.append(root / 'validation/deb/dbt-customer-cohort-retention/phase4_v1/review_support_v3.py')
    result.update(original_action_gate=gate, delivery_contract_ref=contract,
        original_snapshot=request['snapshot'], evaluated_snapshot=fresh, actual_snapshot=fresh,
        original_events=request['original_events'], source_binding=context['manifest']['source_binding'],
        scoring_basis=reviewed['scoring_basis'], pre_native_output_absence_is_failure=False,
        original_model_dbt_execution_inferred=False,
        adapter_code_refs=[io.evidence_ref(path, root) for path in dependencies],
        scope='Frozen audit of exact delivered dbt source on clean fixed inputs. Original pre-native DB/native reward are retained. Paired original echo and required profile export can establish prescribed actions without requiring an agent-issued dbt run.')
    return result

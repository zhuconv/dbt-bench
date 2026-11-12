"""Apply frozen operations audits to the reviewed dbt source deliverable.

Original pre-native data is retained, but is not substituted for the materialized
result of a source-only contract. No original model action is reconstructed.
"""
from copy import deepcopy
from pathlib import Path
from dbt_bench import io
from difficulty_deb_binding_v3 import verify
from difficulty_deb_operations_delivery import policy
import difficulty_deb_phase4_operations as previous

ROOT = Path(__file__).resolve().parents[1]
# These tasks' native verifier materializes their declared output. An absent
# pre-native relation/column alone is therefore not a contract violation.
FAILURE_REQUIREMENTS = {task: {} for task in previous.TASKS}
TASKS = previous.TASKS


def run(request, root=ROOT):
    root = Path(root)
    context = verify(request['audit_dir'], root)
    reviewed = policy(context, root)
    fresh = io.evidence_ref(context['trial'] / 'agent/audit-submission/snapshot.json', root)
    if request.get('reconstruction_snapshot') != fresh:
        raise ValueError('Source delivery needs the bound exact-source rebuild snapshot')
    if request.get('original_snapshot', request['snapshot']) != request['snapshot']:
        raise ValueError('Original snapshot alias differs before delivery selection')
    actual = io.read_json(root / fresh['path'])
    if actual['task_key'] != request['task_key'] or actual['stage'] != 'agent-submission':
        raise ValueError('Wrong reconstructed submission snapshot')
    selected = deepcopy(request)
    selected['snapshot'] = fresh
    result = previous.run(selected, root)
    output = Path(request['output_dir'])
    contract = previous.save(output / 'delivery-contract.json', reviewed, root)
    result.update(delivery_contract_ref=contract, original_snapshot=request['snapshot'],
        evaluated_snapshot=fresh, scoring_basis=reviewed['scoring_basis'],
        pre_native_output_absence_is_failure=False,
        original_model_dbt_execution_inferred=False,
        scope='Frozen audit of the exact submitted dbt source after clean materialization, as required by the reviewed source-delivery contract. Original pre-native DB and native scores are retained. Echo/profile action evidence is exclusively from original paired Qwen events.')
    result.setdefault('adapter_code_refs', []).extend([
        io.evidence_ref(Path(__file__), root),
        io.evidence_ref(root / 'scripts/difficulty_deb_operations_delivery.py', root)])
    return result

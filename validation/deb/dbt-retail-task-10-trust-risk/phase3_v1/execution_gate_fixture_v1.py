"""Reject actual untouched outputs and a failed build attestation explicitly."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import importlib.util
from dbt_bench import io
from phase3_capture import verify_trial
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/shared_projects/cart-trust-phase3-v1'))

def ref(p):
    return io.evidence_ref(p, R)

def save(p, value):
    assert not p.exists(), p
    io.write_json(p, value)
    return ref(p)

def main():
    b = R / 'artifacts/validation/phase3-deb-cart-trust-nop-v1'
    recon = io.read_json(b / 'reconciliation-v1.json')
    assert recon['complete'] and (not recon['errors'])
    records = []
    for row in recon['trials']:
        task = row['task_key']
        trial = (R / row['result']['path']).parent
        current = R / 'validation/deb' / task.removeprefix('deb__') / 'phase3_v1/project_workflow_v2.py'
        spec = importlib.util.spec_from_file_location('nop_' + task, current)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        stages = []
        for stage, sub in [('agent-submission', 'agent/audit-submission'), ('verifier-before', 'verifier/mechanical-before'), ('verifier-after', 'verifier/mechanical-after')]:
            sp = trial / sub / 'snapshot.json'
            snapshot = io.read_json(sp)
            outputs = {v['specification']['relation']: v for v in snapshot['outputs']}
            missing = {name: outputs['main.' + name].get('error') for name in m.base.MODELS}
            assert all((v and v['type'] == 'CatalogException' and ('does not exist' in v['message']) for v in missing.values()))
            f = {'producer': 'locked_qwen_clean_rebuild', 'snapshot': ref(sp), 'initial_required_models_absent': True, 'evidence_scope': 'Actual untouched locked-Qwen snapshot has no required named output relations.', 'missing_relation_errors': missing}
            wf = save(D / 'execution-gates-v1' / task / stage / 'facts.json', f)
            answer = m.evaluate({'workflow': wf, 'snapshot': ref(sp)}, R)
            assert answer['reward'] == 0
            ar = save(D / 'execution-gates-v1' / task / stage / 'audit.json', answer)
            stages.append({'stage': stage, 'snapshot': ref(sp), 'audit': ar, 'reward': 0})
        records.append({'task_key': task, 'native_result': row['result'], 'native_reward': row['native_rewards'], 'stages': stages, 'scope': 'Actual untouched missing-output submissions fail the workflow gate. No synthetic pass and no model call.'})
    formal = R / 'artifacts/validation/phase3-deb-batch06-rebuild-v2'
    trial = formal / 'jobs' / formal.name / 'deb__dbt-retail-task-10-trust-ri__GYMBiX8'
    d = verify_trial(formal, trial)
    out = D / 'execution-gates-v1/failed-build-fixture'
    save(out / 'manifest.json', io.read_json(formal / 'manifest.json'))
    fake = out / 'trial'
    for name in ('config.json', 'result.json'):
        save(fake / name, io.read_json(trial / name))
    execution = deepcopy(d['execution'])
    execution['build_success'] = False
    execution['error'] = 'Synthetic execution-attestation fixture: the requested build failed.'
    er = save(fake / 'agent/audit-rebuild/execution.json', execution)
    try:
        verify_trial(out, fake)
    except ValueError as exc:
        assert str(exc) == 'Reference did not actually build'
        proof = {'rejected': True, 'reason': str(exc), 'fixture_execution': er, 'source_execution': ref(trial / 'agent/audit-rebuild/execution.json'), 'scope': 'The audit reconstruction gate refuses an unsuccessful actual-build attestation before output scoring. This fixture is a component test, not a native benchmark trial; no fabricated reward is assigned.'}
    else:
        raise AssertionError('A failed build attestation was accepted')
    save(D / 'execution-gates-v1/summary.json', {'actual_nop': records, 'failed_build_fixture': proof, 'code': ref(Path(__file__)), 'model_requests': 0, 'native_rewards_modified': False})
    print({'actual_nop_stages': 6, 'failed_build_rejected': True})
if __name__ == '__main__':
    main()

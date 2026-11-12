"""Run supplementary audit from a trusted harness-owned, hash-pinned input spec.

The spec and workflow facts must be assembled by the trusted runner outside the
candidate's writable namespace. This loader does not confer trust on agent data.
"""
import argparse
import hashlib
import json
from pathlib import Path

from audit_tables import CaptureError, load_observer_table, decode
from expected import derive, InputPolicyUnknown
from audit_v3 import evaluate


def checked(reference, root):
    path = root/reference['path']; data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != reference['sha256']:
        raise CaptureError('Artifact hash mismatch: '+str(path))
    return data


def run(spec, root):
    try:
        if spec['task_key'] != 'deb__dbt-daily-order-summary':
            raise CaptureError('Wrong audit task')
        raw = json.loads(checked(spec['initial_raw_orders'], root))
        expected, statistics = derive(raw['rows'])
        capture_error = None
        try:
            candidate = load_observer_table(spec['candidate_snapshot'], task_key=spec['task_key'],
                database=spec['database'], relation='daily_analytics.daily_order_summary', stage=spec['candidate_stage'], root=root)
        except (CaptureError, OSError) as exc:
            candidate = None
            capture_error = str(exc)
        workflow = json.loads(checked(spec['trusted_workflow_facts'], root)) if spec.get('trusted_workflow_facts') else None
        ordered = None
        if spec.get('trusted_ordered_result'):
            item = json.loads(checked(spec['trusted_ordered_result'], root))
            # The runner binds this actual SELECT execution to the current model.
            # This flag describes a runner-owned artifact, not candidate testimony.
            if not item.get('bound_to_trusted_model_build'):
                raise CaptureError('Ordered query result lacks trusted model-build binding')
            ordered = [dict(zip(item['columns'], [decode(v) for v in row])) for row in item['rows']]
        repeated = None
        if spec.get('trusted_repeat_snapshot'):
            repeated = load_observer_table(spec['trusted_repeat_snapshot'], task_key=spec['task_key'],
                database=spec['database'], relation='daily_analytics.daily_order_summary', stage=spec['repeat_stage'], root=root)
        result = evaluate(candidate, expected, workflow=workflow, ordered_result=ordered, repeated=repeated, table_capture_error=capture_error)
        result.update(task_key=spec['task_key'], expected_statistics=statistics,
                      input_refs={k:v for k,v in spec.items() if isinstance(v, dict) and 'sha256' in v},
                      native_reward_modified=False, model_requests=0)
        return result
    except (CaptureError, InputPolicyUnknown, OSError, KeyError, ValueError, TypeError, AttributeError, ArithmeticError) as exc:
        return dict(status='unknown', reward=None, task_key=spec.get('task_key'),
                    reason=str(exc), error_type=type(exc).__name__, native_reward_modified=False, model_requests=0)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True); p.add_argument('--output', required=True); p.add_argument('--root', default='.')
    a = p.parse_args(); result = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    Path(a.output).write_text(json.dumps(result, default=str, indent=2)+'\n')
    print(json.dumps({'status':result['status'],'audit_reward':result['reward']}))

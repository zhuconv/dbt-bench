"""Reuse locked Harbor exception policy; do not infer causes from log strings."""
from pathlib import Path
from dbt_bench import io


def classify_native(raw, root):
    from harbor.models.trial import error_policy
    exceptions = [raw['exception_info']] if raw.get('exception_info') else []
    exceptions += [r['exception_info'] for r in raw.get('step_results') or [] if r.get('exception_info')]
    rows = []
    for entry in exceptions:
        name = entry['exception_type']
        policy = error_policy.policy_for(name)
        rows.append({'exception_type': name, 'failure_cause': str(policy.cause),
                     'native_reward_valid': policy.reward_valid,
                     'native_retry_policy': policy.retry,
                     'automatic_retry_performed': False})
    rewards = (raw.get('verifier_result') or {}).get('rewards')
    return {'reward_valid': bool(rewards) and all(r['native_reward_valid'] for r in rows),
            'exceptions': rows, 'native_policy': io.evidence_ref(Path(error_policy.__file__), root),
            'scope': 'Locked Harbor policy only. Task budget exhaustion/refusal preserves measurable native reward; unknown/provider/environment exceptions remain unresolved. No retry or original reward modification.'}

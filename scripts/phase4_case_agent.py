"""Run proof-gated submission cases after an actual locked-Qwen blind build."""
import json
from audit_rebuild_agent import AuditRebuildAgent


class Phase4CaseAgent(AuditRebuildAgent):
    @staticmethod
    def name():
        return 'phase4-proof-case-qwen-shell-no-model'

    def version(self):
        return 'phase4-proof-case-v1'

    async def shell(self, environment, name, command, pty=False, timeout=300):
        if name == 'audit-submission':
            payload = self._task.paths.task_dir / 'phase4-case'
            await environment.upload_dir(payload, '/tmp/dbt-bench-phase4-case')
            await super().shell(environment, 'phase4-case',
                'python3 /tmp/dbt-bench-phase4-case/build_gate.py && '
                'python3 /tmp/dbt-bench-phase4-case/case_engine.py '
                '--spec /tmp/dbt-bench-phase4-case/case.json --output /logs/agent/case-proof.json',
                timeout=600)
        return await super().shell(environment, name, command, pty=pty, timeout=timeout)

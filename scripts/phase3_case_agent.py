"""Run proof-gated submission cases after an actual locked-Qwen blind build."""
import json
from audit_rebuild_agent import AuditRebuildAgent


class Phase3CaseAgent(AuditRebuildAgent):
    @staticmethod
    def name():
        return 'phase3-proof-case-qwen-shell-no-model'

    def version(self):
        return 'phase3-proof-case-v1'

    async def shell(self, environment, name, command, pty=False, timeout=300):
        if name == 'audit-submission':
            payload = self._task.paths.task_dir / 'phase3-case'
            await environment.upload_dir(payload, '/tmp/dbt-bench-phase3-case')
            await super().shell(environment, 'phase3-case',
                'python3 /tmp/dbt-bench-phase3-case/build_gate.py && '
                'python3 /tmp/dbt-bench-phase3-case/case_engine.py '
                '--spec /tmp/dbt-bench-phase3-case/case.json --output /logs/agent/case-proof.json',
                timeout=600)
        return await super().shell(environment, name, command, pty=pty, timeout=timeout)

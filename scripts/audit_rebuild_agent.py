"""Harbor validation adapter: reconstruct submissions through locked Qwen shell.

No model client or solution/gold upload. Harbor owns each clean environment,
attempt, native verification and artifacts. Semantic audit happens separately.
"""
import json
import shlex
from native_mechanical_agent import NativeMechanicalAgent


class AuditRebuildAgent(NativeMechanicalAgent):
    @staticmethod
    def name():
        return 'audit-rebuild-qwen-shell-no-model'

    def version(self):
        return 'audit-rebuild-v1'

    async def run(self, instruction, environment, context):
        base = '/tmp/dbt-bench-mechanical'
        payload = self._task.paths.task_dir / 'audit-rebuild'
        plan = json.loads((payload / 'request.json').read_text())
        if plan.get('probe_backend_before_submission'):
            await self.shell(environment, 'audit-backend-before-submission', 'echo "$DB_TYPE"')
        await self.shell(environment, 'audit-initial-context', 'python3 ' + base + '/context_probe.py ' +
                         ' '.join('--db ' + shlex.quote(p) for p in self.spec['database_paths']))
        await self.shell(environment, 'audit-initial-visibility',
            'python3 ' + base + '/visibility_scan.py ' + base + '/visibility.json /logs/agent/audit-initial-visibility', timeout=600)
        await self.shell(environment, 'audit-initial',
            'python3 ' + base + '/mechanical_observer.py --spec ' + base + '/spec.json --stage initial --output /logs/agent/audit-initial')
        await self.shell(environment, 'audit-initial-relations',
            'python3 ' + base + '/audit_relation_inventory.py --spec ' + base + '/spec.json --stage initial --output /logs/agent/audit-initial-relations.json')
        if plan.get('mode', 'rebuild') == 'rebuild':
            await environment.upload_dir(payload, '/tmp/dbt-bench-audit-rebuild')
            await self.shell(environment, 'audit-rebuild',
                'python3 /tmp/dbt-bench-audit-rebuild/audit_rebuild_driver.py --spec /tmp/dbt-bench-audit-rebuild/request.json '
                '--output /logs/agent/audit-rebuild', timeout=plan.get('timeout_sec', 1200) * len(plan['commands']) + 120)
        elif plan['mode'] != 'nop':
            raise ValueError('Unknown audit submission mode')
        await self.shell(environment, 'audit-submission',
            'python3 ' + base + '/mechanical_observer.py --spec ' + base + '/spec.json --stage agent-submission --output /logs/agent/audit-submission')
        await self.shell(environment, 'audit-submission-relations',
            'python3 ' + base + '/audit_relation_inventory.py --spec ' + base + '/spec.json --stage agent-submission --output /logs/agent/audit-submission-relations.json')
        (self.logs_dir / 'no-model.json').write_text(json.dumps({'model_requests': 0,
            'execution': 'Locked Qwen shell service, trusted clean reconstruction',
            'solution_uploaded': False, 'mode': plan.get('mode', 'rebuild'), 'task_key': self.spec['task_key']}))
        context.metadata = {'model_requests': 0, 'validation_only': True, 'audit_rebuild': True}

"""Actual Qwen shell environment probe; no hidden material or model invocation."""
import json
import shlex
from native_mechanical_agent import NativeMechanicalAgent


class SpiderEnvironmentAgent(NativeMechanicalAgent):
    @staticmethod
    def name():
        return 'spider-unmodified-project-qwen-shell'

    def version(self):
        return 'spider-unmodified-project-v1'

    async def run(self, instruction, environment, context):
        base = '/tmp/dbt-bench-mechanical'
        for method in ('pipe', 'pty'):
            await self.shell(environment, 'context-' + method,
                'python3 ' + base + '/context_probe.py ' + ' '.join('--db ' + shlex.quote(p) for p in self.spec['database_paths']),
                pty=method == 'pty')
        await self.shell(environment, 'unmodified-dbt',
            'python3 ' + base + '/spider_dbt_probe.py ' + base + '/spec.json', timeout=1550)
        (self.logs_dir / 'no-model.json').write_text(json.dumps({'model_requests': 0,
            'task_key': self.spec['task_key'], 'hidden_material_uploaded': False, 'dbt_deps_executed': False}))
        context.metadata = {'model_requests': 0, 'validation_only': True, 'unmodified_project': True}

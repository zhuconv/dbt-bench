"""Locked Qwen shell read-only database probe; no model or verifier execution."""
import json
from native_mechanical_agent import NativeMechanicalAgent


class InitialDatabaseAgent(NativeMechanicalAgent):
    @staticmethod
    def name():
        return 'initial-database-qwen-shell'

    def version(self):
        return 'initial-database-v1'

    async def run(self, instruction, environment, context):
        await self.shell(environment, 'initial-databases',
            'python3 /tmp/dbt-bench-mechanical/initial_database_probe.py /tmp/dbt-bench-mechanical/spec.json', timeout=180)
        (self.logs_dir / 'no-model.json').write_text(json.dumps({'model_requests': 0,
            'hidden_material_uploaded': False, 'task_key': self.spec['task_key']}))
        context.metadata = {'model_requests': 0, 'validation_only': True, 'read_only': True}

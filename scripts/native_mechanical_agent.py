"""No-model validation adapter: native Qwen setup/shell service + native reference.

Harbor owns initialization, scheduling, attempts, timeouts, verifier and results.
This adapter never invokes QwenCode.run or any model client.
"""
import json
from pathlib import Path
import shlex
from harbor.agents.oracle import OracleAgent
from harbor.agents.base import BaseAgent
from harbor.agents.installed.qwen_code import QwenCode
from harbor.models.task.task import Task
from harbor.models.trial.paths import TrialPaths
from harbor.utils.env import resolve_env_vars
from harbor.utils.file_staging import selected_directory
from harbor.utils.scripts import build_execution_command, needs_chmod

SERVICE = '/root/.nvm/versions/node/v22.23.4/lib/node_modules/@qwen-code/qwen-code/chunks/chunk-NMY3PYNO.js'


class NativeMechanicalAgent(OracleAgent):
    @staticmethod
    def name():
        return 'native-reference-qwen-shell-no-model'

    def __init__(self, *args, installation_bundle, installation_bundle_sha256, **kwargs):
        # Harbor only injects oracle task_dir/trial_paths for the built-in oracle
        # name. An imported validation adapter gets the actual task from its
        # environment at setup, without replacing Harbor's scheduler.
        BaseAgent.__init__(self, *args, **kwargs)
        self._trial_paths = TrialPaths(trial_dir=self.logs_dir.parent)
        self._step_index = 0
        self.qwen = QwenCode(logs_dir=self.logs_dir, model_name='openai/no-model-requests',
            version='0.26.5', logger=self.logger, installation_bundle=Path(installation_bundle),
            installation_bundle_sha256=installation_bundle_sha256)

    def version(self):
        return 'native-mechanical-v2'

    async def setup(self, environment):
        self._task = Task(environment.environment_dir.parent)
        self._agent_timeout_sec = self._task.config.agent.timeout_sec
        await self.qwen.setup(environment)
        payload = self._task.paths.task_dir / 'mechanical'
        await environment.upload_dir(payload, '/tmp/dbt-bench-mechanical')
        self.spec = json.loads((payload / 'spec.json').read_text())
        result = await self.qwen.exec_as_agent(environment, command='pwd', timeout_sec=60)
        if result.return_code:
            raise RuntimeError('Cannot obtain native adapter working directory')
        self.cwd = result.stdout.strip()
        script = self.logs_dir / 'mechanical-shell.mjs'
        script.write_text(
            'import { ShellExecutionService } from ' + json.dumps(SERVICE) + ';\n'
            'import { readFileSync, writeFileSync } from "node:fs";\n'
            'import { createHash } from "node:crypto";\n'
            'const s=JSON.parse(readFileSync(process.argv[2],"utf8"));\n'
            'const started=new Date().toISOString();\n'
            'const handle=await ShellExecutionService.execute(s.command,s.cwd,()=>{},AbortSignal.timeout(s.timeout_ms),s.pty,{},{});\n'
            'const r=await handle.result;\n'
            'writeFileSync(s.output,JSON.stringify({started_at_utc:started,finished_at_utc:new Date().toISOString(),command:s.command,cwd:s.cwd,pty:s.pty,execution_method:r.executionMethod,exit_code:r.exitCode,output:r.output,'
            'service_sha256:createHash("sha256").update(readFileSync(' + json.dumps(SERVICE) + ')).digest("hex")},null,2));\n'
            # Command exit is an observation, not a failure of the recorder.
            # Keep collecting independent evidence after a failed reference.
            'process.exitCode=0;\n')
        await environment.upload_file(script, '/tmp/dbt-bench-mechanical/shell.mjs')

    async def shell(self, environment, name, command, pty=False, timeout=300):
        request = self.logs_dir / (name + '-request.json')
        request.write_text(json.dumps({'command': command, 'cwd': self.cwd, 'pty': pty,
            'timeout_ms': int(timeout * 1000), 'output': '/logs/agent/' + name + '-shell.json'}))
        target = '/tmp/dbt-bench-mechanical/' + name + '-request.json'
        await environment.upload_file(request, target)
        result = await self.qwen.exec_as_agent(environment,
            command='. ~/.nvm/nvm.sh; node /tmp/dbt-bench-mechanical/shell.mjs ' + shlex.quote(target),
            timeout_sec=timeout + 30)
        if result.return_code:
            (self.logs_dir / (name + '-exit.json')).write_text(json.dumps(result.model_dump(mode='json')))
        return result

    async def run(self, instruction, environment, context):
        base = '/tmp/dbt-bench-mechanical'
        # No reference solution or verifier has been uploaded at this point.
        for method in ('pipe', 'pty'):
            await self.shell(environment, 'context-' + method,
                'python3 ' + base + '/context_probe.py ' + ' '.join('--db ' + shlex.quote(p) for p in self.spec['database_paths']),
                pty=method == 'pty')
        await self.shell(environment, 'visibility',
            'python3 ' + base + '/visibility_scan.py ' + base + '/visibility.json /logs/agent/initial-visibility', timeout=600)
        await self.shell(environment, 'initial',
            'python3 ' + base + '/mechanical_observer.py --spec ' + base + '/spec.json --stage initial --output /logs/agent/initial', timeout=300)
        solution_dir, solve_path = self._resolve_solution_paths()
        selection = self._task.file_filter.select(solution_dir)
        with selected_directory(selection) as selected:
            await environment.upload_dir(selected, '/solution')
        container_solve = '/solution/' + solve_path.relative_to(solution_dir).as_posix()
        if needs_chmod(container_solve):
            await environment.exec(command='chmod +x ' + shlex.quote(container_solve), user='root')
        solution_env = resolve_env_vars(self._task.config.solution.env) if self._task.config.solution.env else {}
        command = build_execution_command(container_solve,
            stdout_path='/logs/agent/native-reference.txt', task_os=self._task.config.environment.os)
        with environment.scoped_exec_env(solution_env):
            await self.shell(environment, 'reference', command, timeout=self._agent_timeout_sec or 1800)
        build = self._task.paths.task_dir / 'reference-build'
        if build.is_dir():
            await environment.upload_dir(build, '/tmp/dbt-bench-reference-build')
            with environment.scoped_exec_env(solution_env):
                await self.shell(environment, 'reference-build',
                    'bash /tmp/dbt-bench-reference-build/build.sh > /logs/agent/reference-build.txt 2>&1',
                    timeout=self._agent_timeout_sec or 1800)
        case = self._task.paths.task_dir / 'mechanical-case'
        if case.is_dir():
            # Case specs can contain gold-relative tuple locators. They are
            # deliberately uploaded only after the initial visibility scan and
            # native reference stage, never as an initial agent-visible asset.
            await environment.upload_dir(case, '/tmp/dbt-bench-case')
            await self.shell(environment, 'case',
                'python3 /tmp/dbt-bench-case/case_engine.py --spec /tmp/dbt-bench-case/case.json --output /logs/agent/case-proof.json', timeout=300)
        await self.shell(environment, 'submission',
            'python3 ' + base + '/mechanical_observer.py --spec ' + base + '/spec.json --stage agent-submission --output /logs/agent/submission', timeout=300)
        (self.logs_dir / 'no-model.json').write_text(json.dumps({
            'model_requests': 0, 'execution': 'Locked native Qwen shell service only',
            'reference_kind': self.spec['reference_kind'], 'task_key': self.spec['task_key'],
            'initial_visibility_precedes_solution_upload': True}))
        context.metadata = {'model_requests': 0, 'validation_only': True,
                            'reference_kind': self.spec['reference_kind'], 'has_case_hook': case.is_dir()}

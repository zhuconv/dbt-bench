import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import os
import shutil
import subprocess
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("gh_smoke", Path(__file__).resolve().parents[1] / "scripts/run_gh_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class SmokeExecutionEvidenceTests(unittest.TestCase):
    def test_missing_or_stale_capture_stops_before_job_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = {'task_key':'ade__customer'}
            with patch.object(smoke, 'load_catalog', return_value=[{'task_key':'ade__customer','source':'ade'}]), \
                 patch.object(smoke, 'bind_submission', side_effect=ValueError('contract changed')), \
                 patch.object(smoke, 'emit_config') as emit:
                with self.assertRaisesRegex(ValueError, 'contract changed'):
                    smoke.prepare_smoke_task(root, spec, None, '123', root)
                emit.assert_not_called()

    def test_unknown_task_cannot_fall_back_to_serving_default(self):
        with patch.object(smoke, 'load_catalog', return_value=[]), patch.object(smoke, 'bind_submission') as bind:
            with self.assertRaisesRegex(ValueError, 'Unknown smoke task'):
                smoke.prepare_smoke_task(Path('.'), {'task_key':'deb__default'}, 'ade__unknown', '123', Path('.'))
            bind.assert_not_called()

    def test_templates_resolve_through_actual_native_agent_factory(self):
        root = Path(__file__).resolve().parents[1]
        python = root/'runtime/.venv/bin/python'
        if not python.exists():
            self.skipTest('Pinned Harbor runtime not installed; runtime conformance remains required')
        code = '''
import json
from pathlib import Path
from harbor.agents.factory import AgentFactory
from harbor.agents.installed.qwen_code import QwenCode
from harbor.models.trial.config import AgentConfig
for name in ('ade','spider','deb'):
    config=json.loads((Path('configs')/(name+'.yaml')).read_text())
    agent=AgentConfig.model_validate(config['agents'][0])
    assert AgentFactory.get_agent_class_from_config(agent) is QwenCode
try:
    AgentFactory.get_agent_class_from_config(AgentConfig(name='qwen-code'))
except ValueError:
    pass
else:
    raise AssertionError('Previously broken spelling unexpectedly accepted')
'''
        result = subprocess.run([str(python),'-c',code], cwd=root, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tacc_compiler_does_not_leak_into_model_server(self):
        root = Path(__file__).resolve().parents[1]
        inherited = {'CC':'/site/nvc', 'CXX':'/site/nvc++', 'UNCHANGED':'value'}
        actual = smoke.inference_environment(root, inherited)
        self.assertEqual(actual['CC'],'/usr/bin/gcc')
        self.assertEqual(actual['CXX'],'/usr/bin/g++')
        self.assertEqual(actual['UNCHANGED'],'value')
        self.assertEqual(inherited['CC'],'/site/nvc')

    def events(self, lines):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent/qwen-sessions/project/chats/session.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("\n".join(lines))
            return smoke.model_events(tmp)

    def test_model_subprocess_finds_inference_tools_before_other_environments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'inference/.venv/bin').mkdir(parents=True)
            (root/'inference/runtime-env.json').write_text(json.dumps({'cc':'/usr/bin/gcc','cxx':'/usr/bin/g++','triton_cache':'cache','cuda_home':'/toolkit/cuda','flashinfer_cache':'cache/flashinfer'}))
            (root/'inference/uv.lock').write_text('test runtime lock')
            ninja=root/'inference/.venv/bin/ninja';ninja.write_text('#!/bin/sh\nexit 0\n');ninja.chmod(0o755)
            inherited={'PATH':'/unrelated/bin'}
            actual=smoke.inference_environment(root,inherited)
            self.assertEqual(shutil.which('ninja',path=actual['PATH']),str(ninja))
            self.assertEqual(actual['PATH'].split(os.pathsep)[-1],'/unrelated/bin')
            self.assertEqual(inherited['PATH'],'/unrelated/bin')

    def test_nvcc_wrapper_is_not_inferred_as_toolkit_root(self):
        root = Path(__file__).resolve().parents[1]
        inherited = {'PATH':'/nvhpc/compilers/bin', 'CUDA_HOME':'/nvhpc/compilers', 'CUDA_PATH':'/unrelated'}
        actual = smoke.inference_environment(root, inherited)
        toolkit = json.loads((root/'inference/runtime-env.json').read_text())['cuda_home']
        self.assertEqual(actual['CUDA_HOME'], toolkit)
        self.assertEqual(actual['CUDA_PATH'], toolkit)
        self.assertLess(actual['PATH'].split(os.pathsep).index(toolkit+'/bin'),
                        actual['PATH'].split(os.pathsep).index('/nvhpc/compilers/bin'))
        self.assertEqual(inherited['CUDA_HOME'], '/nvhpc/compilers')

    def test_changed_toolkit_or_lock_cannot_reuse_generated_jit_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'inference').mkdir()
            settings = {'cc':'gcc','cxx':'g++','cuda_home':'/cuda/12.5','triton_cache':'cache','flashinfer_cache':'cache/fi'}
            config = root/'inference/runtime-env.json'; config.write_text(json.dumps(settings))
            lock = root/'inference/uv.lock'; lock.write_text('runtime 1')
            original = smoke.inference_environment(root, {})['FLASHINFER_WORKSPACE_BASE']
            self.assertEqual(smoke.inference_environment(root, {})['FLASHINFER_WORKSPACE_BASE'], original)
            settings['cuda_home']='/cuda/12.8'; config.write_text(json.dumps(settings))
            changed = smoke.inference_environment(root, {})['FLASHINFER_WORKSPACE_BASE']
            self.assertNotEqual(changed, original)
            lock.write_text('runtime 2')
            self.assertNotEqual(smoke.inference_environment(root, {})['FLASHINFER_WORKSPACE_BASE'], changed)

    def test_created_session_or_request_is_not_a_model_run(self):
        result = self.events([json.dumps({"type": "user", "message": {"parts": [{"text": "Do the task"}]}})])
        self.assertEqual(result["assistant_events"], 0)

    def test_real_tool_call_counts_without_claiming_verifier_success(self):
        result = self.events([json.dumps({"type": "assistant", "message": {"parts": [
            {"functionCall": {"id": "1", "name": "run_shell_command", "args": {"command": "false"}}}]}})])
        self.assertEqual(result["assistant_events"], 1)
        self.assertNotIn("reward", result)

    def test_corrupt_events_are_reported_not_silently_discarded(self):
        result = self.events(["{not json}", "null", json.dumps({"type": "assistant", "message": "unknown"})])
        self.assertEqual(result["assistant_events"], 0)
        self.assertEqual(len(result["parse_errors"]), 3)

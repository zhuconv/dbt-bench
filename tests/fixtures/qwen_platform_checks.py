"""Exercise the exact candidate adapter against a recording environment."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]


async def check():
    import harbor.agents.installed.qwen_code as installed
    current = Path(installed.__file__).read_text()
    metadata = json.loads((ROOT / 'patches/harbor-025-qwen-platform-v1/metadata.json').read_text())
    assert hashlib.sha256(current.encode()).hexdigest() == metadata['before_sha256']
    source = current.replace(metadata['before_text'], metadata['after_text'], 1)
    assert hashlib.sha256(source.encode()).hexdigest() == metadata['after_sha256']
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        candidate = root / 'candidate.py'
        candidate.write_text(source)
        spec = importlib.util.spec_from_file_location('candidate_adapter_test', candidate)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        manifest = {'schema_version': 1, 'kind': 'dbt-bench-qwen-bundle-v1',
                    'platform': 'linux-x64', 'qwen_version': '0.26.5',
                    'node_version': '22.23.4', 'npm_version': '10.9.9', 'files': {}}
        for name in ('archive', 'inventory', 'installer'):
            path = root / name
            path.write_bytes(name.encode())
            manifest['files'][name] = {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        manifest_path = root / 'bundle.json'

        async def invoke(data, expected=None, *, reject=False, versions='v22.23.4\n10.9.9\n0.26.5\n'):
            manifest_path.write_text(json.dumps(data))
            expected = expected or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            agent = mod.QwenCode(logs_dir=root / 'logs', model_name='openai/fixture',
                version='0.26.5', installation_bundle=manifest_path,
                installation_bundle_sha256=expected)
            agent.exec_as_agent = AsyncMock(return_value=SimpleNamespace(stdout=versions))
            env = SimpleNamespace(upload_file=AsyncMock())
            if reject:
                try:
                    await agent.install(env)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Invalid manifest accepted')
                assert env.upload_file.await_count == 0
                assert agent.exec_as_agent.await_count == 0
            else:
                await agent.install(env)
                assert env.upload_file.await_count == 3
                assert agent.exec_as_agent.await_count == 2
                command = agent.exec_as_agent.await_args_list[0].kwargs['command']
                assert '--archive-sha256' in command and 'npm install' not in command

        await invoke(manifest)
        await invoke({**manifest, 'platform': 'linux-arm64'})
        try:
            await invoke(manifest, versions='v22.0.0\n10.9.9\n0.26.5\n')
        except ValueError as exc:
            assert 'versions differ' in str(exc)
        else:
            raise AssertionError('Incorrect installed version accepted')
        await invoke(manifest, '0' * 64, reject=True)
        await invoke({**manifest, 'qwen_version': 'wrong'}, reject=True)
        await invoke({**manifest, 'platform': 'wrong'}, reject=True)
        manifest['files']['archive']['path'] = '../outside'
        await invoke(manifest, reject=True)
        manifest['files']['archive']['path'] = 'archive'
        (root / 'archive').write_text('changed archive')
        await invoke(manifest, reject=True)
        # Opt-in adaptation leaves the native install path available unchanged.
        native = mod.QwenCode(logs_dir=root / 'native', model_name='openai/fixture', version='0.26.5')
        native.ensure_system_dependencies = AsyncMock()
        native.exec_as_agent = AsyncMock()
        await native.install(SimpleNamespace())
        native.ensure_system_dependencies.assert_awaited_once()
        assert 'npm install -g @qwen-code/qwen-code@0.26.5' in native.exec_as_agent.await_args.kwargs['command']
        # A stray checksum must never silently select the online installer.
        native.options.installation_bundle_sha256 = '0' * 64
        native.ensure_system_dependencies.reset_mock()
        try:
            await native.install(SimpleNamespace())
        except ValueError:
            pass
        else:
            raise AssertionError('Checksum without bundle accepted')
        native.ensure_system_dependencies.assert_not_awaited()


asyncio.run(check())

print("Native platform adapter: both platform manifests accepted; wrong hash/version/platform/path/payload rejected before upload; no model requests")

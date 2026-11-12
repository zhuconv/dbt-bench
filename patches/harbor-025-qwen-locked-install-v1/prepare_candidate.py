#!/usr/bin/env python3
"""Render the reviewed installer-only candidate without changing Harbor."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

PREIMAGE = '37b32291984a04bec9a309a36d10f2e07f85af86c46ba2df8b3ff276873fa212'

OPTIONS = '''    installation_bundle: Path | None = Field(
        default=None, description="Explicit local locked installation manifest.")
    installation_bundle_sha256: str | None = Field(
        default=None, description="Required SHA256 of the installation manifest.")
'''

INSTALL = '''        if self.options.installation_bundle is not None:
            await self._install_locked_bundle(environment)
            return
        if self.options.installation_bundle_sha256 is not None:
            raise ValueError("Installation checksum requires a bundle manifest")
'''

METHOD = '''    async def _install_locked_bundle(self, environment: BaseEnvironment) -> None:
        manifest_path = self.options.installation_bundle.resolve()
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != self.options.installation_bundle_sha256:
            raise ValueError("Locked Qwen manifest checksum mismatch or missing")
        manifest = json.loads(manifest_bytes)
        if (manifest.get("schema_version") != 1
                or manifest.get("kind") != "dbt-bench-qwen-bundle-v1"
                or manifest.get("platform") != "linux-arm64"
                or manifest.get("qwen_version") != self._version):
            raise ValueError("Locked Qwen manifest/version/platform mismatch")
        paths = {}
        for name in ("archive", "inventory", "installer"):
            entry = manifest["files"][name]
            path = (manifest_path.parent / entry["path"]).resolve()
            if not path.is_relative_to(manifest_path.parent):
                raise ValueError("Locked Qwen file escapes manifest directory")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != entry["sha256"]:
                raise ValueError("Locked Qwen file checksum mismatch: " + name)
            paths[name] = path
        remote = {"archive": "/installed-agent/qwen-bundle.tar.gz",
                  "inventory": "/installed-agent/qwen-inventory.json",
                  "installer": "/installed-agent/qwen-install.py"}
        for name, path in paths.items():
            await environment.upload_file(path, remote[name])
        await self.exec_as_agent(environment, command=shlex.join([
            "python3", remote["installer"], "--archive", remote["archive"],
            "--inventory", remote["inventory"],
            "--archive-sha256", manifest["files"]["archive"]["sha256"],
            "--inventory-sha256", manifest["files"]["inventory"]["sha256"],
            "--output", "/logs/agent/locked-install.json",
        ]))
        versions = await self.exec_as_agent(environment, command=(
            "set -eo pipefail; . ~/.nvm/nvm.sh; "
            "node --version; npm --version; qwen --version"))
        if (versions.stdout or "").strip().splitlines() != [
            "v" + manifest["node_version"], manifest["npm_version"], manifest["qwen_version"]]:
            raise ValueError("Installed Node/npm/Qwen versions differ from bundle")

'''

HUNKS = [
    ('import json\n', 'import hashlib\nimport json\n'),
    ('class QwenCodeOptions(InstalledAgentOptions):\n',
     'class QwenCodeOptions(InstalledAgentOptions):\n' + OPTIONS),
    ('    async def install(self, environment: BaseEnvironment) -> None:\n',
     '    async def install(self, environment: BaseEnvironment) -> None:\n' + INSTALL),
    ('    def _find_session_jsonl(self) -> Path | None:\n',
     METHOD + '    def _find_session_jsonl(self) -> Path | None:\n'),
]


def render(source):
    if hashlib.sha256(source.encode()).hexdigest() != PREIMAGE:
        raise ValueError('Expected exact Harbor 0.25.0 plus recorded Qwen error patch')
    candidate = source
    for before, after in HUNKS:
        if candidate.count(before) != 1:
            raise ValueError('Candidate patch context is ambiguous')
        candidate = candidate.replace(before, after, 1)
    compile(candidate, '<qwen-locked-install-candidate>', 'exec')
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.read_text()
    candidate = render(source)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'qwen_code.py').write_text(candidate)
    (args.output_dir / 'change.diff').write_text(''.join(difflib.unified_diff(
        source.splitlines(True), candidate.splitlines(True),
        fromfile='a/agents/installed/qwen_code.py', tofile='b/agents/installed/qwen_code.py')))
    (args.output_dir / 'candidate.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'harbor-025-qwen-locked-install-v1',
        'type': 'environment', 'production_activated': False,
        'before_sha256': PREIMAGE,
        'after_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
        'replacements': [{'before_text': b, 'after_text': a} for b, a in HUNKS],
        'scope': 'Installer only; native agent loop, tools, converter and verifier unchanged',
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
